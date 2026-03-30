#!/usr/bin/env python3
import logging, os, signal, subprocess, sys, time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%dT%H:%M:%S')
logger = logging.getLogger(__name__)

RING_DIR = Path('/dev/shm/leonardo_ring')
CAMERA_DEV = '/dev/video0'
INPUT_FORMAT = 'mjpeg'
RESOLUTION = '640x480'
FRAMERATE = '10'
SEGMENT_SEC = 10
MAX_SEGMENTS = 6
BITRATE = '300k'

def cleanup_old_segments():
    segs = sorted(RING_DIR.glob('seg_*.mp4'), key=lambda p: p.stat().st_mtime)
    while len(segs) > MAX_SEGMENTS:
        segs.pop(0).unlink(missing_ok=True)

def start_recording():
    RING_DIR.mkdir(parents=True, exist_ok=True)
    pat = str(RING_DIR / 'seg_%03d.mp4')
    cmd = ['ffmpeg', '-y', '-f', 'v4l2', '-input_format', INPUT_FORMAT,
           '-video_size', RESOLUTION, '-framerate', FRAMERATE, '-i', CAMERA_DEV,
           '-c:v', 'libx265', '-preset', 'ultrafast', '-tune', 'zerolatency',
           '-b:v', BITRATE, '-x265-params', 'log-level=error',
           '-f', 'segment', '-segment_time', str(SEGMENT_SEC),
           '-segment_wrap', str(MAX_SEGMENTS), '-segment_format', 'mp4',
           '-movflags', '+faststart', '-reset_timestamps', '1', '-an', pat]
    logger.info('Starting ring recorder: %s %s @ %sfps', CAMERA_DEV, RESOLUTION, FRAMERATE)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def handle_sig(signum, frame):
        logger.info('Signal %d, stopping...', signum)
        proc.terminate()
        try: proc.wait(timeout=5)
        except: proc.kill()
        sys.exit(0)
    signal.signal(signal.SIGTERM, handle_sig)
    signal.signal(signal.SIGINT, handle_sig)
    logger.info('Recorder PID: %d', proc.pid)
    while True:
        ret = proc.poll()
        if ret is not None:
            err = proc.stderr.read().decode('utf-8', errors='replace')
            if ret != 0: logger.error('ffmpeg exit %d: %s', ret, err[-500:])
            else: logger.info('ffmpeg exited normally')
            break
        cleanup_old_segments()
        time.sleep(5)
    return ret

if __name__ == '__main__':
    try: sys.exit(start_recording() or 0)
    except KeyboardInterrupt: sys.exit(0)

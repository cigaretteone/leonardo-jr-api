#!/usr/bin/env python3
import hashlib, logging, subprocess, time
from pathlib import Path

logger = logging.getLogger(__name__)
RING_DIR = Path('/dev/shm/leonardo_ring')
SLICE_DIR = Path('/tmp/leonardo_video')

def slice_video(event_id, duration=10):
    SLICE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SLICE_DIR / f'{event_id}.mp4'
    segments = sorted(RING_DIR.glob('seg_*.mp4'), key=lambda p: p.stat().st_mtime)
    if len(segments) < 2:
        logger.warning('Not enough segments in %s', RING_DIR)
        return None, None, None
    # Skip latest (may be still writing)
    safe = segments[:-1]
    recent = safe[-2:] if len(safe) >= 2 else safe[-1:]
    # Verify segments are not too fresh
    now = time.time()
    recent = [s for s in recent if (now - s.stat().st_mtime) > 1.0]
    if not recent:
        logger.warning('No stable segments available')
        return None, None, None
    concat_list = SLICE_DIR / f'{event_id}_list.txt'
    with open(concat_list, 'w') as f:
        for seg in recent:
            f.write("file '" + str(seg) + "'\n")
    try:
        cmd = ['ffmpeg', '-y',
               '-f', 'concat', '-safe', '0',
               '-i', str(concat_list),
               '-c', 'copy',
               '-movflags', '+faststart',
               str(out_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        concat_list.unlink(missing_ok=True)
        if result.returncode != 0:
            logger.error('ffmpeg slice failed: %s', result.stderr[-300:])
            return None, None, None
    except Exception as e:
        logger.error('ffmpeg error: %s', e)
        concat_list.unlink(missing_ok=True)
        return None, None, None
    if not out_path.exists() or out_path.stat().st_size == 0:
        logger.warning('Sliced video is empty')
        return None, None, None
    sha = hashlib.sha256()
    with open(out_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha.update(chunk)
    file_size = out_path.stat().st_size
    logger.info('Video sliced: %s (%d bytes)', out_path, file_size)
    return str(out_path), sha.hexdigest(), file_size

def cleanup_slice(event_id):
    path = SLICE_DIR / f'{event_id}.mp4'
    if path.exists(): path.unlink(missing_ok=True)
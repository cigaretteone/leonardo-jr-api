#!/usr/bin/env python3
"""video_slicer.py - Phase 2.1: Ring buffer video slicer"""

import hashlib
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

RING_DIR = Path("/dev/shm/leonardo_ring")
SLICE_DIR = Path("/tmp/leonardo_video")

def slice_video(event_id, pre_sec=5, post_sec=5):
    SLICE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SLICE_DIR / f"{event_id}.mp4"
    segments = sorted(RING_DIR.glob("*.mp4"))
    if not segments:
        logger.warning("No ring buffer segments in %s", RING_DIR)
        return None, None, None
    duration = pre_sec + post_sec
    input_file = segments[-1]
    try:
        cmd = [
            "ffmpeg", "-y",
            "-sseof", str(-duration),
            "-i", str(input_file),
            "-t", str(duration),
            "-c", "copy",
            "-movflags", "+faststart",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.error("ffmpeg slice failed: %s", result.stderr[:200])
            return None, None, None
    except Exception as e:
        logger.error("ffmpeg error: %s", e)
        return None, None, None
    sha = hashlib.sha256()
    with open(out_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    file_size = out_path.stat().st_size
    logger.info("Video sliced: %s (%d bytes)", out_path, file_size)
    return str(out_path), sha.hexdigest(), file_size

def cleanup_slice(event_id):
    path = SLICE_DIR / f"{event_id}.mp4"
    if path.exists():
        path.unlink(missing_ok=True)

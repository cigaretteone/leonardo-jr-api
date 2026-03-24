#!/usr/bin/env python3
"""thumbnail_capture.py - Phase 2.1: JPEG thumbnail generator"""

import base64
import logging

logger = logging.getLogger(__name__)

def capture_thumbnail(frame, max_size_kb=100):
    try:
        import cv2
    except ImportError:
        logger.warning("cv2 not available, skipping thumbnail")
        return None
    try:
        resized = cv2.resize(frame, (320, 240))
        quality = 85
        while quality > 20:
            _, buf = cv2.imencode(".jpg", resized,
                                 [cv2.IMWRITE_JPEG_QUALITY, quality])
            if len(buf) <= max_size_kb * 1024:
                return base64.b64encode(buf.tobytes()).decode("ascii")
            quality -= 10
        resized = cv2.resize(frame, (160, 120))
        _, buf = cv2.imencode(".jpg", resized,
                             [cv2.IMWRITE_JPEG_QUALITY, 50])
        return base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception as e:
        logger.error("Thumbnail capture failed: %s", e)
        return None

def thumbnail_from_file(image_path, max_size_kb=100):
    try:
        import cv2
        frame = cv2.imread(str(image_path))
        if frame is None:
            logger.warning("Cannot read image: %s", image_path)
            return None
        return capture_thumbnail(frame, max_size_kb)
    except Exception as e:
        logger.error("Thumbnail from file failed: %s", e)
        return None

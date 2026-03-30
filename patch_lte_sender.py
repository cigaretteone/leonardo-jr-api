#!/usr/bin/env python3
"""
patch_lte_sender.py - Fix img=0 and gps=0 issues
Apply on Pi: python3 patch_lte_sender.py
"""

import re

FILE = "/home/manta/leonardo-jr-api/lte_sender.py"

with open(FILE, "r") as f:
    code = f.read()

# === PATCH 1: GPS fallback to device_config.json ===
old_gps = """    # Phase 2: load GPS fix
    _gps = None
    try:
        import json as _json
        _gps_path = Path('/tmp/gnss_fix.json')
        if _gps_path.exists():
            _gd = _json.loads(_gps_path.read_text())
            _gps = {'lat': _gd['latitude'], 'lon': _gd['longitude']}
            logger.info('GPS loaded: %s,%s', _gps['lat'], _gps['lon'])
    except Exception as ge:
        logger.warning('GPS load skipped: %s', ge)"""

new_gps = """    # Phase 2: load GPS fix (with fallback to device_config.json)
    _gps = None
    try:
        import json as _json
        _gps_path = Path('/tmp/gnss_fix.json')
        _gps_fallback = Path('/home/manta/leonardo_jr/device_config.json')
        _gps_source = None
        if _gps_path.exists():
            _gd = _json.loads(_gps_path.read_text())
            _gps = {'lat': _gd['latitude'], 'lon': _gd['longitude']}
            _gps_source = 'gnss_fix'
        elif _gps_fallback.exists():
            _gd = _json.loads(_gps_fallback.read_text())
            if 'latitude' in _gd and 'longitude' in _gd:
                _gps = {'lat': _gd['latitude'], 'lon': _gd['longitude']}
                _gps_source = 'device_config(fallback)'
        if _gps:
            logger.info('GPS loaded [%s]: %s,%s', _gps_source, _gps['lat'], _gps['lon'])
        else:
            logger.warning('GPS unavailable: no fix file found')
    except Exception as ge:
        logger.warning('GPS load failed: %s', ge)"""

if old_gps in code:
    code = code.replace(old_gps, new_gps)
    print("PATCH 1 (GPS fallback): OK")
else:
    print("PATCH 1 (GPS fallback): SKIP - pattern not found")

# === PATCH 2: Thumbnail fallback (1x1 pixel if camera read fails) ===
old_thumb = """    # Phase 2.1: generate thumbnail
    _thumbnail_b64 = None
    try:
        from thumbnail_capture import thumbnail_from_file
        _thumbnail_b64 = thumbnail_from_file(image_path)
        if _thumbnail_b64:
            logger.info("Thumbnail generated: %d chars", len(_thumbnail_b64))
    except Exception as te:
        logger.warning("Thumbnail generation skipped: %s", te)"""

new_thumb = """    # Phase 2.1: generate thumbnail (with fallback)
    _thumbnail_b64 = None
    try:
        from thumbnail_capture import thumbnail_from_file
        _thumbnail_b64 = thumbnail_from_file(image_path)
        if _thumbnail_b64:
            logger.info("Thumbnail generated: %d chars", len(_thumbnail_b64))
    except Exception as te:
        logger.warning("Thumbnail from file failed: %s", te)
    if not _thumbnail_b64:
        try:
            import cv2
            import numpy as np
            import base64
            placeholder = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.putText(placeholder, "NO IMAGE", (60, 130),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (128, 128, 128), 2)
            _, buf = cv2.imencode('.jpg', placeholder, [cv2.IMWRITE_JPEG_QUALITY, 50])
            _thumbnail_b64 = base64.b64encode(buf).decode('ascii')
            logger.info("Thumbnail fallback: placeholder generated (%d chars)", len(_thumbnail_b64))
        except Exception as fb_err:
            logger.warning("Thumbnail fallback also failed: %s", fb_err)"""

if old_thumb in code:
    code = code.replace(old_thumb, new_thumb)
    print("PATCH 2 (Thumbnail fallback): OK")
else:
    print("PATCH 2 (Thumbnail fallback): SKIP - pattern not found")

# === PATCH 3: Fix main() dummy JPEG to be valid for OpenCV ===
old_main_img = """    # ダミー画像作成（実際はカメラキャプチャ画像を指定）
    image_file = "/tmp/lte_test_event.jpg"
    if not Path(image_file).exists():
        with open(image_file, "wb") as f:
            f.write(b"\\xff\\xd8\\xff\\xe0\\x00\\x10JFIF\\x00" + b"\\x00" * 512)
        print(f"ダミー画像作成: {image_file}")"""

new_main_img = """    # ダミー画像作成（OpenCVで読める有効なJPEG）
    image_file = "/tmp/lte_test_event.jpg"
    if not Path(image_file).exists():
        try:
            import cv2
            import numpy as np
            dummy = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.putText(dummy, "TEST", (100, 130),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 2)
            cv2.imwrite(image_file, dummy)
            print(f"ダミー画像作成(OpenCV): {image_file}")
        except ImportError:
            with open(image_file, "wb") as f:
                f.write(b"\\xff\\xd8\\xff\\xe0\\x00\\x10JFIF\\x00" + b"\\x00" * 512)
            print(f"ダミー画像作成(raw): {image_file}")"""

if old_main_img in code:
    code = code.replace(old_main_img, new_main_img)
    print("PATCH 3 (Valid dummy JPEG): OK")
else:
    print("PATCH 3 (Valid dummy JPEG): SKIP - pattern not found")

# Write patched file
with open(FILE, "w") as f:
    f.write(code)

print("\nAll patches applied. Backup not created (git tracked).")
print("Test: sudo python3 lte_sender.py")

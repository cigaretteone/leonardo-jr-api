#!/usr/bin/env python3
"""Patch lte_sender.py for Phase 2.1 video/thumbnail support"""
import re

FILE = "/home/manta/leonardo-jr-api/lte_sender.py"

with open(FILE, "r", encoding="utf-8") as f:
    code = f.read()

# === PATCH 1: Add upload_video_http function after send_event_http ===
upload_func = '''

# ===========================================================
# Phase 2.1: Video upload
# ===========================================================

def upload_video_http(upload_url, video_path, sha256_hex, wwan_ip, codec="h265", resolution="480p", duration_sec=None):
    """Upload video file to server. Returns SendResult."""
    from urllib.parse import urlparse
    _parsed = urlparse(upload_url)
    _resolved = _resolve_host(_parsed.hostname)
    def _patched_gai(host, port, family=0, type=0, proto=0, flags=0):
        if host == _parsed.hostname:
            host = _resolved
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    socket.getaddrinfo = _patched_gai
    try:
        session = requests.Session()
        session.mount("https://", SourceIPAdapter(wwan_ip))
        headers = {
            "X-Api-Token": API_TOKEN,
            "Content-Type": "application/octet-stream",
            "X-SHA256": sha256_hex,
            "X-Codec": codec,
            "X-Resolution": resolution,
        }
        if duration_sec is not None:
            headers["X-Duration-Sec"] = str(duration_sec)
        with open(video_path, "rb") as f:
            video_data = f.read()
        for attempt in range(MAX_RETRY):
            headers["X-Retry-Count"] = str(attempt)
            try:
                resp = session.post(upload_url, data=video_data, headers=headers, timeout=60)
                if resp.status_code in (200, 201):
                    logger.info("Video upload success: %d", resp.status_code)
                    return SendResult(acked=True, should_queue=False)
                elif resp.status_code in (400, 404, 413, 403):
                    logger.error("Video upload permanent error %d: %s", resp.status_code, resp.text[:200])
                    return SendResult(acked=False, should_queue=False)
                else:
                    delay = BASE_RETRY_DELAY * (2 ** attempt)
                    logger.warning("Video upload retry %d/%d (status=%d)", attempt+1, MAX_RETRY, resp.status_code)
                    time.sleep(delay)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                delay = BASE_RETRY_DELAY * (2 ** attempt)
                logger.warning("Video upload retry %d/%d (%s)", attempt+1, MAX_RETRY, e)
                time.sleep(delay)
        return SendResult(acked=False, should_queue=True)
    finally:
        socket.getaddrinfo = _orig_getaddrinfo

'''

# Insert after the end of send_event_http (find the "Dual Reporting" comment block)
marker = "# [Dual Reporting"
if marker in code:
    code = code.replace(marker, upload_func + marker, 1)
    print("PATCH 1: upload_video_http added")
else:
    print("PATCH 1: MARKER NOT FOUND - manual fix needed")

# === PATCH 2: Add thumbnail_b64 to send_event_http payload ===
old_payload_end = '''        "gps": None,
        "device_status": None,
    }'''
new_payload_end = '''        "thumbnail_b64": metadata.get("thumbnail_b64"),
        "gps": None,
        "device_status": None,
    }'''
if old_payload_end in code:
    code = code.replace(old_payload_end, new_payload_end, 1)
    print("PATCH 2: thumbnail_b64 added to payload")
else:
    print("PATCH 2: MARKER NOT FOUND")

# === PATCH 3: Parse video_requested from response in send_event_http ===
old_success = '''                if resp.status_code in (200, 201):
                    logger.info(
                        "騾∽ｿ｡謌仙粥 event_id=%s status=%d",
                        payload["event_id"], resp.status_code,
                    )
                    return SendResult(acked=True, should_queue=False)'''
new_success = '''                if resp.status_code in (200, 201):
                    logger.info(
                        "騾∽ｿ｡謌仙粥 event_id=%s status=%d",
                        payload["event_id"], resp.status_code,
                    )
                    # Phase 2.1: parse video_requested
                    try:
                        resp_json = resp.json()
                        metadata["_video_requested"] = resp_json.get("video_requested", False)
                        metadata["_upload_url"] = resp_json.get("upload_url")
                    except Exception:
                        metadata["_video_requested"] = False
                        metadata["_upload_url"] = None
                    return SendResult(acked=True, should_queue=False)'''
if old_success in code:
    code = code.replace(old_success, new_success, 1)
    print("PATCH 3: video_requested parsing added")
else:
    print("PATCH 3: MARKER NOT FOUND")

# === PATCH 4: Add video upload to send_event_with_lte after event send success ===
old_send_success = '''        if result.acked:
            logger.info(
                "騾∽ｿ｡謌仙粥: event_id=%s detection_type=%s confidence=%.2f",
                metadata["event_id"], detection_type, confidence,
            )
            return True'''
new_send_success = '''        if result.acked:
            logger.info(
                "騾∽ｿ｡謌仙粥: event_id=%s detection_type=%s confidence=%.2f",
                metadata["event_id"], detection_type, confidence,
            )
            # Phase 2.1: video upload if requested
            if metadata.get("_video_requested") and metadata.get("_upload_url"):
                try:
                    from video_slicer import slice_video, cleanup_slice
                    v_path, v_sha, v_size = slice_video(metadata["event_id"])
                    if v_path and v_sha:
                        logger.info("Video ready: %s (%d bytes)", v_path, v_size)
                        v_result = upload_video_http(
                            metadata["_upload_url"], v_path, v_sha, bearer["ip"]
                        )
                        if v_result.acked:
                            logger.info("Video uploaded: event_id=%s", metadata["event_id"])
                            cleanup_slice(metadata["event_id"])
                        else:
                            logger.warning("Video upload failed: event_id=%s", metadata["event_id"])
                    else:
                        logger.warning("Video slice failed, skipping upload")
                except Exception as ve:
                    logger.error("Video pipeline error: %s", ve)
            return True'''
if old_send_success in code:
    code = code.replace(old_send_success, new_send_success, 1)
    print("PATCH 4: video upload flow added")
else:
    print("PATCH 4: MARKER NOT FOUND")

# === PATCH 5: Add thumbnail to metadata in send_event_with_lte ===
old_metadata_block = '''    metadata: dict = {
        "event_id": str(event_id),
        "device_id": DEVICE_ID,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "detection_type": detection_type,
        "confidence": round(float(confidence), 4),
    }'''
new_metadata_block = '''    # Phase 2.1: generate thumbnail
    _thumbnail_b64 = None
    try:
        from thumbnail_capture import thumbnail_from_file
        _thumbnail_b64 = thumbnail_from_file(image_path)
        if _thumbnail_b64:
            logger.info("Thumbnail generated: %d chars", len(_thumbnail_b64))
    except Exception as te:
        logger.warning("Thumbnail generation skipped: %s", te)

    metadata: dict = {
        "event_id": str(event_id),
        "device_id": DEVICE_ID,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "detection_type": detection_type,
        "confidence": round(float(confidence), 4),
        "thumbnail_b64": _thumbnail_b64,
    }'''
if old_metadata_block in code:
    code = code.replace(old_metadata_block, new_metadata_block, 1)
    print("PATCH 5: thumbnail generation added")
else:
    print("PATCH 5: MARKER NOT FOUND")

# === Write patched file ===
with open(FILE, "w", encoding="utf-8") as f:
    f.write(code)

print("\nAll patches applied to", FILE)

"""Phase 2.1 curlモックテスト — 全エンドポイント確認"""
import requests
import uuid
import hashlib
import json
import time

BASE = "https://leonardo-jr-api.onrender.com"
DEVICE_ID = "LJ-671493E4-QDSF"
TOKEN = "m0lCjXhKXBooGZ87ty_ASxbIQh0iD_MQwrYC-CVYuNU"
HEADERS = {"X-Api-Token": TOKEN, "Content-Type": "application/json"}

passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} -- {detail}")


print("=" * 60)
print("Phase 2.1 API Test")
print("=" * 60)

# ------------------------------------------------------------------
# Test 1: イベント送信（サムネ付き）→ 201 + video_requested=true
# ------------------------------------------------------------------
print("\n[1] イベント送信（サムネ付き）")
event_id = str(uuid.uuid4())  # テスト用UUID4
# 最小JPEGのbase64（1x1ピクセル灰色）
TINY_JPEG_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAFRABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8AJgA//9k="

payload = {
    "event_id": event_id,
    "device_id": DEVICE_ID,
    "event_type": "detection",
    "occurred_at": "2026-03-23T10:00:00.000Z",
    "detection": {
        "class": "bear",
        "confidence": 0.92,
        "distance_estimate": None
    },
    "thumbnail_b64": TINY_JPEG_B64
}

r = requests.post(f"{BASE}/api/v1/devices/{DEVICE_ID}/event", headers=HEADERS, json=payload)
body = r.json()
test("Status 201", r.status_code == 201, f"got {r.status_code}")
test("video_requested=true", body.get("video_requested") == True, f"got {body}")
test("upload_url contains event_id", event_id in body.get("upload_url", ""), f"got {body}")
test("upload_url is absolute", body.get("upload_url", "").startswith("https://"), f"got {body}")

# ------------------------------------------------------------------
# Test 2: 同じイベント再送 →
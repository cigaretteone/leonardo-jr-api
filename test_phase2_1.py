"""Phase 2.1 API Test"""
import requests, uuid, hashlib, time

BASE = "https://leonardo-jr-api.onrender.com"
DID = "LJ-671493E4-QDSF"
TKN = "m0lCjXhKXBooGZ87ty_ASxbIQh0iD_MQwrYC-CVYuNU"
HDR = {"X-Api-Token": TKN, "Content-Type": "application/json"}
JPEG = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFRABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCwAB//2Q=="
ok = 0
ng = 0

def t(name, cond, detail=""):
    global ok, ng
    if cond:
        ok += 1
        print(f"  PASS: {name}")
    else:
        ng += 1
        print(f"  FAIL: {name} -- {detail}")

print("=" * 60)
print("Phase 2.1 API Test")
print("=" * 60)

# [1] Event with thumbnail
print("\n[1] Event + thumbnail")
eid1 = str(uuid.uuid4())
p1 = {"event_id": eid1, "device_id": DID, "event_type": "detection", "occurred_at": "2026-03-23T10:00:00Z", "detection": {"class": "bear", "confidence": 0.92}, "thumbnail_b64": JPEG}
r = requests.post(f"{BASE}/api/v1/devices/{DID}/event", headers=HDR, json=p1, timeout=30)
b = r.json()
t("201", r.status_code == 201, f"{r.status_code}")
t("video_requested", b.get("video_requested") == True, f"{b}")
t("upload_url absolute", b.get("upload_url", "").startswith("https://"), f"{b}")

# [2] Duplicate
print("\n[2] Duplicate event")
r = requests.post(f"{BASE}/api/v1/devices/{DID}/event", headers=HDR, json=p1, timeout=30)
b = r.json()
t("200", r.status_code == 200, f"{r.status_code}")
t("duplicate", b.get("status") == "duplicate", f"{b}")
t("video_requested=false", b.get("video_requested") == False, f"{b}")

# [3] Event without thumbnail
print("\n[3] Event without thumbnail")
eid2 = str(uuid.uuid4())
p2 = {"event_id": eid2, "device_id": DID, "event_type": "detection", "occurred_at": "2026-03-23T10:01:00Z", "detection": {"class": "person", "confidence": 0.75}}
r = requests.post(f"{BASE}/api/v1/devices/{DID}/event", headers=HDR, json=p2, timeout=30)
t("201", r.status_code == 201, f"{r.status_code}")

# [4] Video upload
print("\n[4] Video upload")
video = b"FAKE_VIDEO_" * 300
sha = hashlib.sha256(video).hexdigest()
vh = {"X-Api-Token": TKN, "Content-Type": "application/octet-stream", "X-SHA256": sha, "X-Codec": "h265", "X-Resolution": "480p", "X-Duration-Sec": "8.0"}
r = requests.post(f"{BASE}/api/v1/devices/{DID}/events/{eid1}/video", headers=vh, data=video, timeout=60)
b = r.json()
t("201", r.status_code == 201, f"{r.status_code} {b}")
t("uploaded", b.get("status") == "uploaded", f"{b}")

# [5] Video duplicate
print("\n[5] Video duplicate")
r = requests.post(f"{BASE}/api/v1/devices/{DID}/events/{eid1}/video", headers=vh, data=video, timeout=60)
b = r.json()
t("200", r.status_code == 200, f"{r.status_code}")
t("duplicate", b.get("status") == "duplicate", f"{b}")

# [6] SHA mismatch
print("\n[6] SHA-256 mismatch")
eid3 = str(uuid.uuid4())
p3 = {"event_id": eid3, "device_id": DID, "event_type": "detection", "occurred_at": "2026-03-23T10:02:00Z", "detection": {"class": "bear", "confidence": 0.88}}
requests.post(f"{BASE}/api/v1/devices/{DID}/event", headers=HDR, json=p3, timeout=30)
time.sleep(1)
bh = dict(vh)
bh["X-SHA256"] = "0" * 64
r = requests.post(f"{BASE}/api/v1/devices/{DID}/events/{eid3}/video", headers=bh, data=video, timeout=60)
t("400", r.status_code == 400, f"{r.status_code} {r.text}")
t("hash_mismatch", "hash_mismatch" in r.text, f"{r.text}")

# [7] 404 event not found
print("\n[7] Video for nonexistent event")
r = requests.post(f"{BASE}/api/v1/devices/{DID}/events/00000000-0000-0000-0000-000000000000/video", headers=vh, data=video, timeout=60)
t("404", r.status_code == 404, f"{r.status_code} {r.text}")

# Summary
print("\n" + "=" * 60)
print(f"Results: {ok} PASSED / {ng} FAILED / {ok+ng} TOTAL")
print("=" * 60)
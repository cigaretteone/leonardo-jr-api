import os

TARGET = r"C:\Users\manta\leonardo-jr-api\lte_sender.py"
BACKUP = TARGET + ".bak"

# 1. 追加する定数
NEW_CONSTANTS = """
# ===========================================================
# [Dual Reporting] New API Config (Alpha)
# ===========================================================
JR_API_URL_TEMPLATE = "https://leonardo-jr-api.onrender.com/api/v1/devices/{}/event"
JR_API_TOKEN = "m0lCjXhKXBooGZ87ty_ASxbIQh0iD_MQwrYC-CVYuNU"
"""

# 2. 追加する送信関数
NEW_FUNCTION = """
def send_to_jr_api(metadata: dict, wwan_ip: str) -> None:
    \"\"\"
    [Dual Reporting] Send event metadata to the new Leonardo Jr. API.
    Does not block or fail the main process on error.
    \"\"\"
    try:
        import requests
        import socket
        from urllib.parse import urlparse
        
        device_id = metadata.get("device_id", DEVICE_ID)
        url = JR_API_URL_TEMPLATE.format(device_id)
        
        raw_type = metadata.get("event_type", "wildlife_detected")
        det_type = "bear" if "wildlife" in raw_type or "bear" in raw_type else "other"
        
        payload = {
            "detection_type": det_type,
            "confidence": metadata.get("confidence", 0.0),
            "timestamp": metadata.get("timestamp")
        }
        
        logger.info(f"JR_API: Sending dual report to {url}")

        session = requests.Session()
        session.mount("https://", SourceIPAdapter(wwan_ip))
        
        try:
            _parsed = urlparse(url)
            if '_resolve_host' in globals() and '_orig_getaddrinfo' in globals():
                _resolved = _resolve_host(_parsed.hostname)
                def _patched_gai(host, port, family=0, type=0, proto=0, flags=0):
                    if host == _parsed.hostname:
                        host = _resolved
                    return _orig_getaddrinfo(host, port, family, type, proto, flags)
                socket.getaddrinfo = _patched_gai
        except Exception:
            pass 

        try:
            resp = session.post(
                url,
                json=payload,
                headers={
                    "X-Api-Token": JR_API_TOKEN,
                    "Content-Type": "application/json"
                },
                timeout=10
            )
            if resp.status_code in [200, 201]:
                logger.info(f"JR_API: Success (ID: {resp.json().get('event_id')})")
            else:
                logger.error(f"JR_API: Failed {resp.status_code} {resp.text}")
        finally:
             if '_orig_getaddrinfo' in globals():
                 socket.getaddrinfo = _orig_getaddrinfo

    except Exception as e:
        logger.error(f"JR_API: Exception {e}")
        if '_orig_getaddrinfo' in globals():
             socket.getaddrinfo = _orig_getaddrinfo
"""

# 3. 挿入箇所の定義
CALL_SNIPPET_SEARCH = 'success = send_event_http(image_path, metadata, bearer["ip"])'
CALL_SNIPPET_INSERT = """
        # [8b] Dual Report to New API (Fire and Forget)
        send_to_jr_api(metadata, bearer["ip"])
"""

def patch_file():
    if not os.path.exists(TARGET):
        print(f"Error: {TARGET} not found.")
        return

    # UTF-8で読み込み
    with open(TARGET, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if any("JR_API_URL_TEMPLATE" in line for line in lines):
        print("File seems already patched. Skipping.")
        return

    print(f"Backing up to {BACKUP}...")
    with open(BACKUP, "w", encoding="utf-8") as f:
        f.writelines(lines)

    new_lines = []
    constants_inserted = False
    function_inserted = False
    
    for line in lines:
        new_lines.append(line)
        
        if "logger = logging.getLogger(__name__)" in line and not constants_inserted:
            new_lines.append(NEW_CONSTANTS)
            constants_inserted = True
            
        if "def save_to_local_queue" in line and not function_inserted:
            new_lines.pop() 
            new_lines.append(NEW_FUNCTION)
            new_lines.append(line)
            function_inserted = True
            
        if CALL_SNIPPET_SEARCH in line:
            new_lines.append(CALL_SNIPPET_INSERT)

    with open(TARGET, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    
    print("Patch applied successfully.")

if __name__ == "__main__":
    patch_file()
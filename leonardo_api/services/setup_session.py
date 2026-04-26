"""Setup-page-only short-lived JWT (30 min).
Distinct from auth.py JWT (which is for users) but shares the same secret key.
Stored in HttpOnly cookie 'setup_session'.
"""
import time
import jwt

from ..config import settings

JWT_ALGORITHM = "HS256"
SESSION_TTL_SECONDS = 30 * 60
COOKIE_NAME = "setup_session"


def issue(device_id: str) -> str:
    now = int(time.time())
    payload = {
        "device_id": device_id,
        "iat": now,
        "exp": now + SESSION_TTL_SECONDS,
        "scope": "setup",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify(token: str, expected_device_id: str) -> bool:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return False
    if payload.get("scope") != "setup":
        return False
    if payload.get("device_id") != expected_device_id:
        return False
    return True

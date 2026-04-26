import time
from threading import Lock

WINDOW_SECONDS = 15 * 60
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60

_state: dict[str, dict] = {}
_lock = Lock()


def is_locked(device_id: str) -> tuple[bool, int]:
    with _lock:
        entry = _state.get(device_id)
        if not entry:
            return False, 0
        now = time.time()
        if entry.get("locked_until", 0) > now:
            return True, int(entry["locked_until"] - now)
        return False, 0


def record_failure(device_id: str) -> bool:
    with _lock:
        now = time.time()
        entry = _state.setdefault(device_id, {"failures": [], "locked_until": 0})
        entry["failures"] = [t for t in entry["failures"] if now - t < WINDOW_SECONDS]
        entry["failures"].append(now)
        if len(entry["failures"]) >= MAX_FAILURES:
            entry["locked_until"] = now + LOCKOUT_SECONDS
            entry["failures"] = []
            return True
        return False


def reset(device_id: str) -> None:
    with _lock:
        _state.pop(device_id, None)

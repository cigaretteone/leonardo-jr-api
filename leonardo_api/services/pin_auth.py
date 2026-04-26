import hmac
import hashlib
import os


def generate_pin(device_id: str) -> str:
    secret = os.environ["SETUP_PIN_MASTER_SECRET"].encode()
    digest = hmac.new(secret, device_id.encode(), hashlib.sha256).digest()
    pin_int = int.from_bytes(digest[:4], "big")
    return str(pin_int % 1000000).zfill(6)


def verify_pin(device_id: str, submitted_pin: str) -> bool:
    expected = generate_pin(device_id)
    return hmac.compare_digest(expected, submitted_pin)

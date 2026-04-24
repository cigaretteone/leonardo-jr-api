#!/usr/bin/env python3
"""gnss_fix.py - GNSS fix via NMEA direct parse from ttyUSB2.

改修履歴:
  2026-04-19: Timeout 60s→180s, status=timeout JSON, alertbox通知追加
  2026-04-24: NMEA直接パース方式へ移行 (AT命令廃止)
    - ttyUSB2 (NMEA専用port) を O_RDONLY|O_NOCTTY|O_NONBLOCK で open
    - $GPGGA/$GPRMC を標準ライブラリのみでパース (pynmea2不使用)
    - ModemManager によるGNSS有効化を前提 (ExecStartPre で mmcli 実行済み)
    - LBS fallback 削除 (AT経由のため廃止)
"""

import json
import os
import select
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SERIAL_PORT = "/dev/ttyGNSS"
READ_TIMEOUT = 180
GNSS_JSON_PATH = Path("/tmp/gnss_fix.json")
DEVICE_CONFIG_PATH = Path("/home/manta/leonardo_jr/device_config.json")
ALERTBOX_CMD_PATH = Path("/run/alertbox/cmd")

# 日本域 bounding box (沖縄〜択捉)
_JAPAN_LAT = (24.0, 46.0)
_JAPAN_LON = (122.0, 154.0)


def _log(msg):
    print(msg, flush=True)


def _load_device_id():
    try:
        return json.loads(
            DEVICE_CONFIG_PATH.read_text(encoding="utf-8")
        ).get("device_id", "LJ-UNKNOWN")
    except Exception:
        return "LJ-UNKNOWN"


def _verify_checksum(sentence):
    """NMEA XOR checksum verification. Returns True if valid."""
    if "*" not in sentence:
        return False
    body, chk = sentence.rsplit("*", 1)
    body = body.lstrip("$")
    calc = 0
    for c in body:
        calc ^= ord(c)
    try:
        return calc == int(chk.strip()[:2], 16)
    except ValueError:
        return False


def _is_valid_coord(lat, lon):
    """Sanity check (値域) + 日本域チェック。範囲外なら False。"""
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        _log(f"[!] Coord out of range: {lat},{lon}")
        return False
    if not (_JAPAN_LAT[0] <= lat <= _JAPAN_LAT[1] and
            _JAPAN_LON[0] <= lon <= _JAPAN_LON[1]):
        _log(f"[!] Coord outside Japan bounds: {lat},{lon}")
        return False
    return True


def _nmea_to_decimal(raw, hemi):
    """Convert NMEA DDMM.MMMM + hemisphere to decimal degrees."""
    if not raw:
        return None
    dot = raw.index(".")
    deg = float(raw[: dot - 2])
    mins = float(raw[dot - 2 :])
    val = round(deg + mins / 60.0, 7)
    return -val if hemi in ("S", "W") else val


def _iso_ts(utc_time_str, date_str=None):
    """Build ISO8601 UTC string.

    GPRMC: date_str (ddmmyy) + time -> exact UTC timestamp
    GPGGA: no date_str -> datetime.now(utc) で日付境界の不整合を回避
    """
    if date_str and len(date_str) == 6:
        try:
            t = utc_time_str.split(".")[0]
            hh, mm, ss = t[0:2], t[2:4], t[4:6]
            dd, mo, yy = date_str[0:2], date_str[2:4], date_str[4:6]
            return f"20{yy}-{mo}-{dd}T{hh}:{mm}:{ss}Z"
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat()


def _parse_gpgga(fields):
    """
    $GPGGA/$GNGGA:
      [0]id [1]time [2]lat [3]N/S [4]lon [5]E/W
      [6]quality [7]sats [8]hdop [9]alt [10]M ...
    Returns (lat, lon, alt, ts) or None.
    """
    if len(fields) < 11:
        return None
    if not fields[6] or fields[6] == "0":
        return None
    try:
        lat = _nmea_to_decimal(fields[2], fields[3])
        lon = _nmea_to_decimal(fields[4], fields[5])
    except (ValueError, IndexError):
        return None
    if lat is None or lon is None:
        return None
    if not _is_valid_coord(lat, lon):
        return None
    try:
        alt = float(fields[9]) if fields[9] else 0.0
    except ValueError:
        alt = 0.0
    return lat, lon, alt, _iso_ts(fields[1])


def _parse_gprmc(fields):
    """
    $GPRMC/$GNRMC:
      [0]id [1]time [2]status [3]lat [4]N/S [5]lon [6]E/W
      [7]speed [8]course [9]date ...
    Returns (lat, lon, alt, ts) or None.
    """
    if len(fields) < 10:
        return None
    if fields[2] != "A":
        return None
    try:
        lat = _nmea_to_decimal(fields[3], fields[4])
        lon = _nmea_to_decimal(fields[5], fields[6])
    except (ValueError, IndexError):
        return None
    if lat is None or lon is None:
        return None
    if not _is_valid_coord(lat, lon):
        return None
    return lat, lon, 0.0, _iso_ts(fields[1], fields[9])


def _notify_alertbox(cmd):
    """Write cmd to alertbox. Non-fatal if dir absent or write fails."""
    if not ALERTBOX_CMD_PATH.parent.exists():
        _log(f"[!] Alertbox dir absent, skipping: {ALERTBOX_CMD_PATH.parent}")
        return
    try:
        ALERTBOX_CMD_PATH.write_text(cmd)
        _log(f"[*] Alertbox: {cmd}")
    except Exception as e:
        _log(f"[!] Alertbox write failed (non-fatal): {e}")


def _atomic_write_json(path: Path, data: dict) -> None:
    """Atomic JSON write via temp file + os.replace."""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        _log(f"[!] Failed to write {path}: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def main():
    sys.stdout.reconfigure(line_buffering=True)
    device_id = _load_device_id()
    timeout = int(sys.argv[1]) if len(sys.argv) > 1 else READ_TIMEOUT

    _log(f"[1] Opening {SERIAL_PORT} read-only (timeout={timeout}s)...")
    try:
        fd = os.open(SERIAL_PORT, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as e:
        _log(f"[!] Cannot open {SERIAL_PORT}: {e}")
        _atomic_write_json(GNSS_JSON_PATH, {
            "status": "error",
            "error": str(e),
            "device_id": device_id,
            "attempted_at": datetime.now(timezone.utc).isoformat(),
        })
        _notify_alertbox("GPS_FAIL")
        sys.exit(1)

    _log("[2] Reading NMEA stream...")
    start = time.monotonic()
    result = None
    buf = b""
    last_log = -10

    try:
        while time.monotonic() - start < timeout:
            elapsed = int(time.monotonic() - start)
            if elapsed - last_log >= 10:
                _log(f"    waiting... {elapsed}s/{timeout}s")
                last_log = elapsed

            ready, _, _ = select.select([fd], [], [], 1.0)
            if not ready:
                continue

            try:
                chunk = os.read(fd, 512)
            except BlockingIOError:
                continue
            if not chunk:
                break
            buf += chunk

            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                line = raw_line.decode("ascii", errors="ignore").strip()

                if not line.startswith("$") or not _verify_checksum(line):
                    continue

                body = line.split("*")[0].lstrip("$")
                fields = body.split(",")
                sid = fields[0]
                parsed = None

                if sid in ("GPGGA", "GNGGA"):
                    parsed = _parse_gpgga(fields)
                    source = sid
                elif sid in ("GPRMC", "GNRMC"):
                    parsed = _parse_gprmc(fields)
                    source = sid

                if parsed:
                    lat, lon, alt, ts = parsed
                    result = {"lat": lat, "lon": lon, "alt": alt,
                              "ts": ts, "source": source}
                    break

            if result:
                break
    finally:
        os.close(fd)

    if result is None:
        elapsed = int(time.monotonic() - start)
        _log(f"[3] Timeout after {elapsed}s — no fix.")
        _atomic_write_json(GNSS_JSON_PATH, {
            "status": "timeout",
            "device_id": device_id,
            "attempted_at": datetime.now(timezone.utc).isoformat(),
        })
        _notify_alertbox("GPS_FAIL")
        sys.exit(1)

    _log(f"[3] Fix: {result['lat']},{result['lon']} "
         f"alt={result['alt']}m via {result['source']}")
    now = datetime.now(timezone.utc).isoformat()

    # device_config.json — 既存キーを保持しつつ位置情報を更新
    try:
        cfg = json.loads(DEVICE_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        cfg = {"device_id": device_id}
    cfg.update({
        "latitude": result["lat"],
        "longitude": result["lon"],
        "altitude_m": result["alt"],
        "fix_method": "nmea",
        "accuracy_m": 10.0,
        "location_source": "gnss_nmea",
        "fixed_at": now,
    })
    _atomic_write_json(DEVICE_CONFIG_PATH, cfg)
    _log(f"[4] Updated {DEVICE_CONFIG_PATH}")

    _atomic_write_json(GNSS_JSON_PATH, {
        "status": "ok",
        "lat": result["lat"],
        "lon": result["lon"],
        "fixed_at": now,
        "device_id": device_id,
    })
    _notify_alertbox("GPS_OK")
    _log("[5] Done.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
voltage_monitor.py - Leonardo Jr. Phase 3.1
Pi5 PMIC EXT5V_V monitoring + graceful shutdown on low voltage.

Primary: vcgencmd pmic_read_adc (EXT5V_V)
Secondary: SIM7600G-H AT+CBC via /dev/ttyUSB3

Thresholds (EXT5V_V):
  NORMAL:   >= 4.6V
  WARN:     < 4.6V  -> log warning
  CRITICAL: < 4.3V  -> 3 consecutive readings -> graceful shutdown
"""

import subprocess
import time
import os
import sys
import csv
from datetime import datetime

POLL_INTERVAL = 10
WARN_VOLTAGE = 4.6
CRITICAL_VOLTAGE = 4.3
CRITICAL_COUNT_LIMIT = 3
LOG_PATH = "/var/log/leonardo_voltage.csv"

SERVICES_TO_STOP = [
    "leonardo-ring",
    "leonardo-alertbox",
]


def read_ext5v():
    """Read EXT5V_V from Pi5 PMIC via vcgencmd."""
    try:
        result = subprocess.run(
            ["vcgencmd", "pmic_read_adc"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "EXT5V_V" in line:
                # Format: "     EXT5V_V volt(24)=5.12416000V"
                val = line.split("=")[1].rstrip("V").strip()
                return float(val)
    except Exception as e:
        print("[voltage_monitor] PMIC read error: " + str(e), flush=True)
    return None


def read_cbc():
    """Read SIM7600G-H supply voltage via AT+CBC on ttyUSB3."""
    try:
        import serial
        s = serial.Serial("/dev/ttyUSB3", 115200, timeout=2)
        s.write(b"AT+CBC\r")
        time.sleep(0.5)
        data = s.read(s.in_waiting).decode(errors="ignore")
        s.close()
        for line in data.splitlines():
            if "+CBC:" in line:
                # Format: "+CBC: 3.875V"
                val = line.split(":")[1].strip().rstrip("V").strip()
                return float(val)
    except Exception as e:
        print("[voltage_monitor] CBC read error: " + str(e), flush=True)
    return None


def log_voltage(ts, ext5v, cbc, status):
    """Append one row to CSV log."""
    file_exists = os.path.exists(LOG_PATH)
    try:
        with open(LOG_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not file_exists:
                w.writerow(["timestamp", "ext5v_v", "cbc_v", "status"])
            w.writerow([ts, ext5v, cbc, status])
    except Exception as e:
        print("[voltage_monitor] Log write error: " + str(e), flush=True)


def graceful_shutdown():
    """Stop Leonardo services and power off."""
    print("[voltage_monitor] === GRACEFUL SHUTDOWN INITIATED ===", flush=True)

    for svc in SERVICES_TO_STOP:
        print("[voltage_monitor] Stopping " + svc, flush=True)
        subprocess.run(["systemctl", "stop", svc], timeout=10)

    print("[voltage_monitor] All services stopped. Powering off.", flush=True)
    subprocess.run(["shutdown", "-h", "now"])
    sys.exit(0)


def main():
    print("[voltage_monitor] Started. WARN=" + str(WARN_VOLTAGE)
          + "V CRITICAL=" + str(CRITICAL_VOLTAGE) + "V", flush=True)

    critical_count = 0

    while True:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ext5v = read_ext5v()
        cbc = read_cbc()

        if ext5v is None:
            status = "READ_ERROR"
            print("[voltage_monitor] " + ts + " EXT5V read failed", flush=True)
            log_voltage(ts, "ERR", cbc, status)
            time.sleep(POLL_INTERVAL)
            continue

        if ext5v < CRITICAL_VOLTAGE:
            critical_count += 1
            status = "CRITICAL(" + str(critical_count) + "/" + str(CRITICAL_COUNT_LIMIT) + ")"
            print("[voltage_monitor] " + ts
                  + " CRITICAL EXT5V=" + str(round(ext5v, 3)) + "V"
                  + " count=" + str(critical_count), flush=True)
            log_voltage(ts, round(ext5v, 3), cbc, status)

            if critical_count >= CRITICAL_COUNT_LIMIT:
                log_voltage(ts, round(ext5v, 3), cbc, "SHUTDOWN")
                graceful_shutdown()
        elif ext5v < WARN_VOLTAGE:
            critical_count = 0
            status = "WARN"
            print("[voltage_monitor] " + ts
                  + " WARN EXT5V=" + str(round(ext5v, 3)) + "V", flush=True)
            log_voltage(ts, round(ext5v, 3), cbc, status)
        else:
            critical_count = 0
            status = "OK"
            # Normal: log silently, no stdout spam
            log_voltage(ts, round(ext5v, 3), cbc, status)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

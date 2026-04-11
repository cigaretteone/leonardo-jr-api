#!/usr/bin/env python3
"""sim7600_poweroff.py - Power off SIM7600G-H module via AT+CPOF"""
import serial
import time
import sys

PORTS = ["/dev/ttyUSB2", "/dev/ttyUSB3"]

for port in PORTS:
    try:
        s = serial.Serial(port, 115200, timeout=3)
        s.write(b"AT+CPOF\r")
        time.sleep(2)
        resp = s.read(s.in_waiting).decode(errors="ignore").strip()
        s.close()
        if "OK" in resp or "NORMAL POWER DOWN" in resp:
            print("SIM7600G-H power off OK via " + port + ": " + resp)
            sys.exit(0)
    except Exception as e:
        print(port + " skip: " + str(e))

print("SIM7600G-H power off: no response (may already be off)")
sys.exit(0)

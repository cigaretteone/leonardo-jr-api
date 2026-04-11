import serial
import time

for port in range(6):
    dev = "/dev/ttyUSB" + str(port)
    try:
        s = serial.Serial(dev, 115200, timeout=2)
        time.sleep(0.1)
        cmds = [b"AT\r", b"AT+CADC?\r", b"AT+CBC\r"]
        for cmd in cmds:
            s.write(cmd)
            time.sleep(0.5)
            r = s.read(s.in_waiting).decode(errors="ignore").strip()
            label = cmd.decode().strip()
            print(dev + " | " + label + " | " + r)
        s.close()
    except Exception as e:
        print(dev + " | SKIP | " + str(e))

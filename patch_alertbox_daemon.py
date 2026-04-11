#!/usr/bin/env python3
"""
patch_alertbox_daemon.py - Fix reconnect on USB disconnect (OSError)
Apply on Pi: python3 patch_alertbox_daemon.py
"""

FILE = "/home/manta/leonardo-jr-api/alertbox_daemon.py"

with open(FILE, "r") as f:
    code = f.read()

old = """        except serial.SerialException as e:
            logger.error('Serial error: %s, reconnecting...', e)
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(2)
            try:
                ser = connect_serial()
            except Exception as e2:
                logger.error('Reconnect failed: %s', e2)
        except Exception as e:
            logger.error('Unexpected error: %s', e)"""

new = """        except (serial.SerialException, OSError) as e:
            logger.error('Serial/IO error: %s, reconnecting...', e)
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(3)
            for attempt in range(5):
                try:
                    ser = connect_serial()
                    logger.info('Reconnected after %d attempts', attempt + 1)
                    break
                except Exception as e2:
                    logger.error('Reconnect attempt %d/5 failed: %s', attempt + 1, e2)
                    time.sleep(5)
            else:
                logger.error('All reconnect attempts failed, will keep retrying in main loop')
        except Exception as e:
            logger.error('Unexpected error: %s', e)"""

if old in code:
    code = code.replace(old, new)
    print("PATCH (AlertBox reconnect): OK")
else:
    print("PATCH (AlertBox reconnect): SKIP - pattern not found")

with open(FILE, "w") as f:
    f.write(code)

print("Done. Restart: sudo systemctl restart leonardo-alertbox")

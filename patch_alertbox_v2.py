#!/usr/bin/env python3
"""
patch_alertbox_v2.py - Make ANY exception trigger reconnect
Apply on Pi: python3 patch_alertbox_v2.py
"""

FILE = "/home/manta/leonardo-jr-api/alertbox_daemon.py"

with open(FILE, "r") as f:
    code = f.read()

old = """        except (serial.SerialException, OSError) as e:
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

new = """        except Exception as e:
            logger.error('Error: %s (%s), reconnecting...', e, type(e).__name__)
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
                logger.error('All reconnect attempts failed, retrying next loop')"""

if old in code:
    code = code.replace(old, new)
    print("PATCH v2 (catch-all reconnect): OK")
else:
    print("PATCH v2: SKIP - pattern not found")

with open(FILE, "w") as f:
    f.write(code)

print("Done.")

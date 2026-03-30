#!/usr/bin/env python3
"""alertbox.py - Alert Box client via /run/alertbox/"""

import logging
import os
import time

logger = logging.getLogger(__name__)

CMD_FILE = '/run/alertbox/cmd'
RESP_FILE = '/run/alertbox/resp'
TIMEOUT_SEC = 4

def send_command(cmd):
    if not os.path.exists(CMD_FILE):
        logger.error('AlertBox daemon not running')
        return None
    with open(RESP_FILE, 'w') as f:
        f.write('')
    with open(CMD_FILE, 'w') as f:
        f.write(cmd)
    deadline = time.time() + TIMEOUT_SEC
    while time.time() < deadline:
        time.sleep(0.15)
        try:
            with open(RESP_FILE, 'r') as f:
                resp = f.read().strip()
            if resp:
                logger.info('AlertBox: %s -> %s', cmd, resp)
                return resp
        except Exception:
            pass
    logger.warning('AlertBox: %s -> timeout', cmd)
    return None

def alert_bear():
    return send_command('BEAR')

def alert_animal():
    return send_command('ANIMAL')

def alert_test():
    return send_command('TEST')

def alert_demo():
    return send_command('DEMO')

def alert_stop():
    return send_command('STOP')

def get_status():
    return send_command('STATUS')

def set_volume(vol):
    return send_command('VOL:{}'.format(vol))

def close():
    pass

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'STATUS'
    result = send_command(cmd.upper())
    print(result if result else 'ERROR: no response')
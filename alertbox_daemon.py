#!/usr/bin/env python3
"""alertbox_daemon.py - Persistent Alert Box connection daemon via /run/alertbox/"""

import logging
import os
import serial
import signal
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
)
logger = logging.getLogger(__name__)

ALERTBOX_DEV = '/dev/alertbox'
ALERTBOX_BAUD = 115200
RUN_DIR = '/run/alertbox'
CMD_FILE = os.path.join(RUN_DIR, 'cmd')
RESP_FILE = os.path.join(RUN_DIR, 'resp')

def setup_run_dir():
    os.makedirs(RUN_DIR, exist_ok=True)
    os.chmod(RUN_DIR, 0o777)
    for path in [CMD_FILE, RESP_FILE]:
        with open(path, 'w') as f:
            f.write('')
        os.chmod(path, 0o666)
    logger.info('Run directory ready: %s', RUN_DIR)

def connect_serial():
    ser = serial.Serial(ALERTBOX_DEV, ALERTBOX_BAUD, timeout=2)
    logger.info('Waiting for Arduino boot...')
    time.sleep(2.5)
    ser.reset_input_buffer()
    logger.info('Alert Box connected, boot complete')
    return ser

def run():
    setup_run_dir()
    ser = connect_serial()

    def handle_sig(signum, frame):
        logger.info('Signal %d, shutting down...', signum)
        ser.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sig)
    signal.signal(signal.SIGINT, handle_sig)

    while True:
        try:
            with open(CMD_FILE, 'r') as f:
                cmd = f.read().strip()
            if cmd:
                with open(CMD_FILE, 'w') as f:
                    f.write('')
                with open(RESP_FILE, 'w') as f:
                    f.write('')
                ser.reset_input_buffer()
                ser.write((cmd + '\n').encode('ascii'))
                ser.flush()
                time.sleep(0.5)
                resp = ser.readline().decode('ascii', errors='replace').strip()
                if not resp:
                    resp = 'NO_RESPONSE'
                with open(RESP_FILE, 'w') as f:
                    f.write(resp)
                logger.info('CMD: %s -> %s', cmd, resp)
        except Exception as e:
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
                logger.error('All reconnect attempts failed, retrying next loop')
        time.sleep(0.1)

if __name__ == '__main__':
    run()
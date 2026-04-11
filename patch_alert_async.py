#!/usr/bin/env python3
"""patch_alert_async.py - Make Alert Box non-blocking with threading"""

FILE = "/home/manta/leonardo-jr-api/lte_sender.py"

with open(FILE, "r") as f:
    code = f.read()

# PATCH 1: Replace synchronous alert with threaded version
old_alert = """    # ── Alert Box: immediate audio warning ──
    _alert_ok = False
    try:
        from alertbox import alert_bear, alert_animal
        if detection_type == 'bear':
            _ab_resp = alert_bear()
            if _ab_resp:
                _alert_ok = True
                logger.info('Alert Box: BEAR OK (%s)', _ab_resp)
            else:
                logger.warning('Alert Box: BEAR failed (timeout/no response)')
        elif detection_type in ('deer', 'boar', 'bird'):
            _ab_resp = alert_animal()
            if _ab_resp:
                _alert_ok = True
                logger.info('Alert Box: ANIMAL OK (%s)', _ab_resp)
            else:
                logger.warning('Alert Box: ANIMAL failed (timeout/no response)')
        else:
            _alert_ok = True  # no alert needed for this type
    except Exception as ab_err:
        logger.warning('Alert Box error (non-fatal): %s', ab_err)"""

new_alert = """    # ── Alert Box: immediate audio warning (async) ──
    import threading
    _alert_result = {'ok': False, 'done': False}

    def _alert_thread(det_type):
        try:
            from alertbox import alert_bear, alert_animal
            if det_type == 'bear':
                resp = alert_bear()
                if resp:
                    _alert_result['ok'] = True
                    logger.info('Alert Box: BEAR OK (%s)', resp)
                else:
                    logger.warning('Alert Box: BEAR failed (timeout/no response)')
            elif det_type in ('deer', 'boar', 'bird'):
                resp = alert_animal()
                if resp:
                    _alert_result['ok'] = True
                    logger.info('Alert Box: ANIMAL OK (%s)', resp)
                else:
                    logger.warning('Alert Box: ANIMAL failed (timeout/no response)')
            else:
                _alert_result['ok'] = True
        except Exception as ab_err:
            logger.warning('Alert Box error (non-fatal): %s', ab_err)
        finally:
            _alert_result['done'] = True

    _alert_t = threading.Thread(target=_alert_thread, args=(detection_type,), daemon=True)
    _alert_t.start()"""

if old_alert in code:
    code = code.replace(old_alert, new_alert)
    print("PATCH 1 (async alert start): OK")
else:
    print("PATCH 1 (async alert start): SKIP")

# PATCH 2: Replace _alert_ok reference in EVENT_SUMMARY with join + result
old_summary_ref = "                1 if _alert_ok else 0,"
new_summary_ref = "                1 if (_alert_t.join(timeout=5) or True) and _alert_result['ok'] else 0,"

if old_summary_ref in code:
    code = code.replace(old_summary_ref, new_summary_ref)
    print("PATCH 2 (async alert join): OK")
else:
    print("PATCH 2 (async alert join): SKIP")

with open(FILE, "w") as f:
    f.write(code)

print("\nDone. Test: sudo python3 lte_sender.py")

#!/usr/bin/env python3
"""patch_alert_flag.py - Fix EVENT_SUMMARY alert flag to reflect actual result"""

FILE = "/home/manta/leonardo-jr-api/lte_sender.py"

with open(FILE, "r") as f:
    code = f.read()

# PATCH 1: Capture alert result
old_alert = """    # ── Alert Box: immediate audio warning ──
    try:
        from alertbox import alert_bear, alert_animal
        if detection_type == 'bear':
            alert_bear()
            logger.info('Alert Box: BEAR triggered')
        elif detection_type in ('deer', 'boar', 'bird'):
            alert_animal()
            logger.info('Alert Box: ANIMAL triggered')
    except Exception as ab_err:
        logger.warning('Alert Box error (non-fatal): %s', ab_err)"""

new_alert = """    # ── Alert Box: immediate audio warning ──
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

if old_alert in code:
    code = code.replace(old_alert, new_alert)
    print("PATCH 1 (alert capture): OK")
else:
    print("PATCH 1 (alert capture): SKIP")

# PATCH 2: Fix EVENT_SUMMARY to use _alert_ok
old_summary = "            logger.info('EVENT_SUMMARY id=%s type=%s conf=%.2f img=%d vid=%d alert=1 gps=%d send=1',"
new_summary = "            logger.info('EVENT_SUMMARY id=%s type=%s conf=%.2f img=%d vid=%d alert=%d gps=%d send=1',"

if old_summary in code:
    code = code.replace(old_summary, new_summary)
    print("PATCH 2 (summary format): OK")
else:
    print("PATCH 2 (summary format): SKIP")

# PATCH 3: Add _alert_ok to the format args
old_args = """                metadata['event_id'], detection_type, confidence,
                1 if metadata.get('thumbnail_b64') else 0,
                1 if metadata.get('_vid_ok') else 0,
                1 if metadata.get('_gps') else 0)"""

new_args = """                metadata['event_id'], detection_type, confidence,
                1 if metadata.get('thumbnail_b64') else 0,
                1 if metadata.get('_vid_ok') else 0,
                1 if _alert_ok else 0,
                1 if metadata.get('_gps') else 0)"""

if old_args in code:
    code = code.replace(old_args, new_args)
    print("PATCH 3 (alert arg): OK")
else:
    print("PATCH 3 (alert arg): SKIP")

with open(FILE, "w") as f:
    f.write(code)

print("\nDone. Test: sudo python3 lte_sender.py")

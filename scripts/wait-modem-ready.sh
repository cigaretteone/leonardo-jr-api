#!/bin/bash
# Leonardo Jr. — modem-ready wait loop for Phase 16
# Waits up to 4 minutes for ModemManager modem to become operational.
# Exits 0 unconditionally so systemd ExecStartPre never blocks service start.

set +e
MAX_ATTEMPTS=120
SLEEP_SEC=2
i=0
while [ $i -lt $MAX_ATTEMPTS ]; do
    if /usr/bin/mmcli -m 0 --output-keyvalue 2>/dev/null \
       | /bin/grep -qE 'modem\.generic\.state.*(registered|connected)'; then
        echo "[wait-modem-ready] modem ready at attempt $i (~$((i*SLEEP_SEC))s)"
        exit 0
    fi
    i=$((i + 1))
    /bin/sleep $SLEEP_SEC
done
echo "[wait-modem-ready] timeout after $((MAX_ATTEMPTS * SLEEP_SEC))s, falling through"
exit 0

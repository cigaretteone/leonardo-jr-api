#!/usr/bin/env python3
"""Patch lte_sender.py: add Alert Box trigger before LTE operations"""

target = "/home/manta/leonardo-jr-api/lte_sender.py"

with open(target, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find "modem_index: Optional[str] = None" around line 756
insert_done = False
for i, line in enumerate(lines):
    if "modem_index: Optional[str] = None" in line and not insert_done:
        indent = "    "
        insert = [
            indent + "# ── Alert Box: immediate audio warning ──\n",
            indent + "try:\n",
            indent + "    from alertbox import alert_bear, alert_animal\n",
            indent + "    if detection_type == 'bear':\n",
            indent + "        alert_bear()\n",
            indent + "        logger.info('Alert Box: BEAR triggered')\n",
            indent + "    elif detection_type in ('deer', 'boar', 'bird'):\n",
            indent + "        alert_animal()\n",
            indent + "        logger.info('Alert Box: ANIMAL triggered')\n",
            indent + "except Exception as ab_err:\n",
            indent + "    logger.warning('Alert Box error (non-fatal): %s', ab_err)\n",
            indent + "\n",
        ]
        for j, ins_line in enumerate(insert):
            lines.insert(i + j, ins_line)
        insert_done = True
        print(f"Patch applied at line {i+1}")
        break

if not insert_done:
    print("PATCH FAILED: marker not found")
else:
    with open(target, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print("lte_sender.py patched successfully")

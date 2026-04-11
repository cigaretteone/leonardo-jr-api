#!/usr/bin/env python3
"""Patch lte_sender.py to include GPS from gnss_fix.json"""

target = "/home/manta/leonardo-jr-api/lte_sender.py"

with open(target, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find "gps": None in the payload inside send_event_http
done = False
for i, line in enumerate(lines):
    if '"gps": None,' in line and 'payload' not in line and not done:
        # Check this is inside send_event_http payload
        indent = line[:len(line) - len(line.lstrip())]
        lines[i] = indent + '"gps": metadata.get("_gps"),\n'
        done = True
        print(f"Patched payload gps at line {i+1}")
        break

# Find thumbnail generation block, add GPS loading before it
done2 = False
for i, line in enumerate(lines):
    if '# Phase 2.1: generate thumbnail' in line and not done2:
        indent = "    "
        insert = [
            indent + "# Phase 2: load GPS fix\n",
            indent + "_gps = None\n",
            indent + "try:\n",
            indent + "    import json as _json\n",
            indent + "    _gps_path = Path('/tmp/gnss_fix.json')\n",
            indent + "    if _gps_path.exists():\n",
            indent + "        _gd = _json.loads(_gps_path.read_text())\n",
            indent + "        _gps = {'lat': _gd['latitude'], 'lon': _gd['longitude']}\n",
            indent + "        logger.info('GPS loaded: %s,%s', _gps['lat'], _gps['lon'])\n",
            indent + "except Exception as ge:\n",
            indent + "    logger.warning('GPS load skipped: %s', ge)\n",
            indent + "\n",
        ]
        for j, ins_line in enumerate(insert):
            lines.insert(i + j, ins_line)
        done2 = True
        print(f"GPS loading inserted at line {i+1}")
        break

# Find metadata dict, add _gps
done3 = False
for i, line in enumerate(lines):
    if '"thumbnail_b64": _thumbnail_b64,' in line and not done3:
        indent = line[:len(line) - len(line.lstrip())]
        lines.insert(i + 1, indent + '"_gps": _gps,\n')
        done3 = True
        print(f"_gps added to metadata at line {i+2}")
        break

if done and done2 and done3:
    with open(target, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print("lte_sender.py GPS patches applied successfully")
else:
    print("WARNING: some patches failed - check manually")

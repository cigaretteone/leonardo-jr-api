#!/usr/bin/env python3
"""Patch gnss_fix.py to also write /tmp/gnss_fix.json"""

target = "/home/manta/leonardo_jr/gnss_fix.py"

with open(target, "r", encoding="utf-8") as f:
    code = f.read()

old = '    CONFIG_PATH.write_text(\n        json.dumps(cfg, indent=2), encoding="utf-8")'
new = '    CONFIG_PATH.write_text(\n        json.dumps(cfg, indent=2), encoding="utf-8")\n    Path("/tmp/gnss_fix.json").write_text(\n        json.dumps(cfg, indent=2), encoding="utf-8")\n    print(f"[5b] Also saved to /tmp/gnss_fix.json")'

if old in code:
    code = code.replace(old, new)
    with open(target, "w", encoding="utf-8") as f:
        f.write(code)
    print("gnss_fix.py patched: also writes /tmp/gnss_fix.json")
else:
    print("PATCH MARKER NOT FOUND - check manually")

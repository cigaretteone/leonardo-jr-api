#!/usr/bin/env python3
FILE = "/home/manta/leonardo-jr-api/lte_sender.py"
with open(FILE, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find line 450 area - first "return SendResult(acked=True" in send_event_http
for i in range(430, 460):
    if "return SendResult(acked=True, should_queue=False)" in lines[i]:
        indent = "                    "
        insert = [
            indent + "# Phase 2.1: parse video_requested\n",
            indent + "try:\n",
            indent + "    resp_json = resp.json()\n",
            indent + "    metadata['_video_requested'] = resp_json.get('video_requested', False)\n",
            indent + "    metadata['_upload_url'] = resp_json.get('upload_url')\n",
            indent + "except Exception:\n",
            indent + "    metadata['_video_requested'] = False\n",
            indent + "    metadata['_upload_url'] = None\n",
        ]
        for j, ins_line in enumerate(insert):
            lines.insert(i + j, ins_line)
        print(f"PATCH 3: inserted at line {i+1}")
        break
else:
    print("PATCH 3: FAILED")

with open(FILE, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("Done")

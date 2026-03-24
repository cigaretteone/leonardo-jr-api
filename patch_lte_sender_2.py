#!/usr/bin/env python3
"""Patch 3 & 4 for lte_sender.py"""

FILE = "/home/manta/leonardo-jr-api/lte_sender.py"

with open(FILE, "r", encoding="utf-8") as f:
    lines = f.readlines()

# === PATCH 3: Add video_requested parsing after first "return SendResult(acked=True" in send_event_http ===
# Find first "resp.status_code in (200, 201)" then find the next "return SendResult(acked=True"
patch3_done = False
in_send_event_http = False
for i, line in enumerate(lines):
    if "def send_event_http(" in line:
        in_send_event_http = True
    if in_send_event_http and "def " in line and "send_event_http" not in line:
        in_send_event_http = False
    if in_send_event_http and "return SendResult(acked=True, should_queue=False)" in line and not patch3_done:
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
        patch3_done = True
        print(f"PATCH 3: inserted at line {i+1}")
        break

if not patch3_done:
    print("PATCH 3: FAILED")

# Re-scan for PATCH 4
# Find "result.acked" inside send_event_with_lte, then find "return True" after it
patch4_done = False
in_send_event_with_lte = False
for i, line in enumerate(lines):
    if "def send_event_with_lte(" in line:
        in_send_event_with_lte = True
    if in_send_event_with_lte and "def " in line and "send_event_with_lte" not in line:
        in_send_event_with_lte = False
    if in_send_event_with_lte and "result.acked:" in line and "self." not in line and not patch4_done:
        # Find the "return True" after this line
        for k in range(i+1, min(i+20, len(lines))):
            if "return True" in lines[k]:
                indent = "            "
                insert = [
                    indent + "# Phase 2.1: video upload if requested\n",
                    indent + "if metadata.get('_video_requested') and metadata.get('_upload_url'):\n",
                    indent + "    try:\n",
                    indent + "        from video_slicer import slice_video, cleanup_slice\n",
                    indent + "        v_path, v_sha, v_size = slice_video(metadata['event_id'])\n",
                    indent + "        if v_path and v_sha:\n",
                    indent + "            logger.info('Video ready: %s (%d bytes)', v_path, v_size)\n",
                    indent + "            v_result = upload_video_http(\n",
                    indent + "                metadata['_upload_url'], v_path, v_sha, bearer['ip']\n",
                    indent + "            )\n",
                    indent + "            if v_result.acked:\n",
                    indent + "                logger.info('Video uploaded: event_id=%s', metadata['event_id'])\n",
                    indent + "                cleanup_slice(metadata['event_id'])\n",
                    indent + "            else:\n",
                    indent + "                logger.warning('Video upload failed: event_id=%s', metadata['event_id'])\n",
                    indent + "        else:\n",
                    indent + "            logger.warning('Video slice failed, skipping upload')\n",
                    indent + "    except Exception as ve:\n",
                    indent + "        logger.error('Video pipeline error: %s', ve)\n",
                ]
                for j, ins_line in enumerate(insert):
                    lines.insert(k + j, ins_line)
                patch4_done = True
                print(f"PATCH 4: inserted at line {k+1}")
                break
        break

if not patch4_done:
    print("PATCH 4: FAILED")

with open(FILE, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("Done")

#!/usr/bin/env python3
target = "/home/manta/leonardo-jr-api/lte_sender.py"
with open(target, "r", encoding="utf-8") as f:
    lines = f.readlines()

done = False
for i, line in enumerate(lines):
    if "except Exception as ve:" in line and not done:
        # Find the "return True" after this block
        for k in range(i, min(i+10, len(lines))):
            if "return True" in lines[k] and "result" not in lines[k]:
                indent = "            "
                summary = indent + "logger.info('EVENT_SUMMARY id=%s type=%s conf=%.2f img=%d vid=%d alert=1 gps=%d send=1',\n"
                summary += indent + "    metadata['event_id'], detection_type, confidence,\n"
                summary += indent + "    1 if metadata.get('thumbnail_b64') else 0,\n"
                summary += indent + "    1 if metadata.get('_vid_ok') else 0,\n"
                summary += indent + "    1 if metadata.get('_gps') else 0)\n"
                lines.insert(k, summary)
                done = True
                print(f"Summary log inserted at line {k+1}")
                break
        break

if not done:
    print("PATCH FAILED")
else:
    # Also mark video success
    old = "                            logger.info('Video uploaded: event_id=%s', metadata['event_id'])"
    new = "                            logger.info('Video uploaded: event_id=%s', metadata['event_id'])\n                            metadata['_vid_ok'] = True"
    content = ''.join(lines)
    if old in content:
        content = content.replace(old, new, 1)
        print("_vid_ok flag added")
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    print("lte_sender.py patched")

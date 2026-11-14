#!/usr/bin/env python3
"""PreToolUse hook on Read: deny whole-file reads of large text files (no offset/limit). Images and PDFs pass."""
import sys, json, os, re
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
inp = data.get("tool_input") or {}
p = inp.get("file_path") or ""
if not p: sys.exit(0)
ext = os.path.splitext(p)[1].lower()
if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"):
    # 2026-11-01: an image stays in context for every later call (4,816 images Oct 25-31, ~270M tokens re-read).
    # Deny wide originals unless the path is already a downscaled copy (*-small.* or under a dir named small).
    maxw = int(os.environ.get("TOKEN_GUARD_IMG_MAXW", "1200"))
    full = os.path.expanduser(p)
    if re.search(r"-small\.[a-z]+$|/small/", full):
        sys.exit(0)
    try:
        import subprocess
        out = subprocess.run(["sips", "-g", "pixelWidth", full], capture_output=True, text=True, timeout=4).stdout
        w = int(re.search(r"pixelWidth:\s*(\d+)", out).group(1))
    except Exception:
        sys.exit(0)
    if w > maxw:
        base = os.path.splitext(os.path.basename(full))[0]
        small = os.path.join(os.environ.get("TMPDIR", "/tmp"), "claude-small", base + "-small.png")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": f"{os.path.basename(full)} is {w} px wide; a full-size image stays in context for every "
            f"later call. Read a downscaled copy instead: mkdir -p \"{os.path.dirname(small)}\" && sips -Z 1000 \"{full}\" --out "
            f"\"{small}\" ; then Read {small}. Need the pixels? Crop the region first (sips -c h w / --cropOffset) and read that."}}))
    sys.exit(0)
if ext in (".pdf", ".ipynb"):
    sys.exit(0)
try: size = os.path.getsize(os.path.expanduser(p))
except Exception: sys.exit(0)
limit = int(os.environ.get("TOKEN_GUARD_READ_KB", "100")) * 1000
jlimit = int(os.environ.get("TOKEN_GUARD_JSON_KB", "40")) * 1000
cap = jlimit if ext in (".json", ".jsonl", ".csv", ".tsv") else limit
if size > cap and not (inp.get("limit") or inp.get("offset")):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": f"{os.path.basename(p)} is {size//1000} KB; a whole-file Read stays in context for every later call. "
        "Read a slice with offset/limit, or extract what you need with grep, jq, head or a python one-liner and print only that."}}))
sys.exit(0)

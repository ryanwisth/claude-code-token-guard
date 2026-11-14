#!/usr/bin/env python3
"""Per-call context guard (added 2026-11-01). The UserPromptSubmit guard only runs when the user types, so an autonomous
turn of 50+ calls sailed past both lines: Oct 25-31, 39 % of cache reads were above the 200k soft line and 787M above
the 240k hard line, mostly in side sessions. This script runs on every tool call:
- PostToolUse: above SOFT (200k) inject a reminder every N calls: write the handoff, finish the current step, stop.
- PreToolUse: above HARD (260k) deny every tool except the ones needed to save state and report (Edit/Write to a
  handoff or memory file, Bash, wrap_up, update_plan, AskUserQuestion), so the turn ends in a handoff, not a 300k wall.
Reads only the transcript tail (600 KB), ~5 ms."""
import sys, json, os, re
SOFT = int(os.environ.get("TOKEN_GUARD_CALL_SOFT_K", "200")) * 1000
HARD = int(os.environ.get("TOKEN_GUARD_CALL_HARD_K", "260")) * 1000
EVERY = int(os.environ.get("TOKEN_GUARD_CALL_EVERY", "8"))
SAVE_RE = re.compile(r"(HANDOFF|START-HERE|TRAPS|STATE|memory/|MEMORY\.md|\.claude/)", re.I)
ALWAYS_OK = {"Bash", "mcp__ccd_turn__wrap_up", "mcp__ccd_turn__update_plan", "AskUserQuestion", "Read", "Grep", "Glob",
             "mcp__ccd_session__start_session", "SendMessage", "TaskStop"}
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
path = data.get("transcript_path"); event = data.get("hook_event_name"); tool = data.get("tool_name") or ""
if not path or not os.path.exists(path):
    sys.exit(0)
ctx = None; calls_over = 0
try:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - 600_000)); tail = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(tail):
        if '"usage"' not in line: continue
        try: e = json.loads(line)
        except Exception: continue
        m = e.get("message") if isinstance(e, dict) else None
        if e.get("type") == "assistant" and not e.get("isSidechain") and isinstance(m, dict) and isinstance(m.get("usage"), dict):
            u = m["usage"]; c = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            if ctx is None: ctx = c
            if c >= SOFT: calls_over += 1
            else: break
            if calls_over > 400: break
except Exception:
    sys.exit(0)
if ctx is None or ctx < SOFT:
    sys.exit(0)
k = ctx // 1000
if event == "PreToolUse":
    if ctx < HARD or tool in ALWAYS_OK:
        sys.exit(0)
    inp = data.get("tool_input") or {}
    if tool in ("Edit", "Write", "MultiEdit") and SAVE_RE.search(str(inp.get("file_path") or "")):
        sys.exit(0)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": f"TOKEN GUARD: context is {k}k, past the {HARD//1000}k per-call hard line. Only saving "
        f"state is allowed now: update HANDOFF.md (state, verified facts, commands, decisions, next step), then wrap up "
        f"and tell the user the next prompt belongs in a fresh session. {tool} was not run."}}))
    sys.exit(0)
if event == "PostToolUse" and (calls_over == 1 or calls_over % EVERY == 0):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
        f"TOKEN GUARD: context is {k}k (call {calls_over} above the {SOFT//1000}k line). Every call re-reads all {k}k. "
        f"Finish the current step only, refresh HANDOFF.md, then end the turn with: 'Context is {k}k; next prompt "
        f"belongs in a fresh session.' At {HARD//1000}k every tool except saving state is denied."}}))
sys.exit(0)

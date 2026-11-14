#!/usr/bin/env python3
"""UserPromptSubmit hook, two lines:
- soft at 200k: tell Claude to refresh HANDOFF.md and tell the user to continue in a fresh session;
- hard at 240k: block the prompt and hand the user a ready-to-paste opener for a new chat.
A prompt that starts with "keep:" passes the hard stop once (answering a pending question, saving to the handoff,
checking on running background work). Reads the transcript tail on every prompt; scans the whole file only when blocking."""
import sys, json, os, re
SOFT_K = int(os.environ.get("TOKEN_GUARD_CTX_K", "200"))
HARD_K = int(os.environ.get("TOKEN_GUARD_BLOCK_K", "240"))
HANDOFF_RE = re.compile(r"(HANDOFF|START-HERE)[^/]*\.md$", re.I)
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
path = data.get("transcript_path")
prompt = data.get("prompt") or ""
if not path or not os.path.exists(path):
    sys.exit(0)


def lines(tail_bytes=None):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if tail_bytes:
            f.seek(max(0, size - tail_bytes))
        return f.read().decode("utf-8", "replace").splitlines()


ctx = None
try:
    for line in reversed(lines(600_000)):
        if '"usage"' not in line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        m = e.get("message") if isinstance(e, dict) else None
        if e.get("type") == "assistant" and not e.get("isSidechain") and isinstance(m, dict) and isinstance(m.get("usage"), dict):
            u = m["usage"]
            ctx = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            break
except Exception:
    sys.exit(0)
if ctx is None or ctx < SOFT_K * 1000:
    sys.exit(0)
k = ctx // 1000


def emit(system_message, context):
    print(json.dumps({
        "systemMessage": system_message,
        "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context},
    }))
    sys.exit(0)


if ctx < HARD_K * 1000:
    emit(f"Context {k}k ≥ {SOFT_K}k: write the handoff and continue in a fresh session. Hard stop at {HARD_K}k.",
         f"TOKEN GUARD: this session's context is {k}k tokens, above the {SOFT_K}k handoff line. Every further call "
         f"re-reads all {k}k, and at {HARD_K}k the user's prompts are blocked. Do this turn: (1) if HANDOFF.md for the "
         f"active project was not refreshed at this size, update it now (state, verified facts with evidence, exact "
         f"commands, decisions with the why, in-flight work, next steps); (2) answer the prompt; (3) end the reply with "
         f"one line: 'Context is {k}k; next prompt belongs in a fresh session (HANDOFF.md is current).' Do not start "
         f"new long work in this session.")

if prompt.lstrip().lower().startswith("keep:"):
    emit(f"keep: override, sent at {k}k. The next prompt is blocked unless it also starts with keep:.",
         f"TOKEN GUARD: context is {k}k, past the {HARD_K}k hard stop. The user sent this prompt with the keep: override, "
         f"so it ran here instead of in a new chat. Do only what it asks (answer the pending question, save to the "
         f"handoff or memory, commit, or report on background work already running). Start no new work. If HANDOFF.md "
         f"for the active project is not current, update it. End the reply with one line: 'Context is {k}k; start a "
         f"new chat for anything else.'")

# Never block what the app sends on its own (a background task or workflow finishing, a CI event) or a shell command
# The user runs from the composer: a lead chat past the line must still receive its fleet's results.
if prompt.lstrip().startswith(("<task-notification", "<ci-monitor-event", "<bash-input")):
    sys.exit(0)

# Hard stop: find the last handoff file this chat wrote and the chat's title, to build the opener.
handoff, title = None, None
try:
    for line in reversed(lines()):
        if title is None and '"custom-title"' in line:
            try:
                title = json.loads(line).get("customTitle")
            except Exception:
                pass
        if handoff is None and '"tool_use"' in line and ("handoff" in line.lower() or "start-here" in line.lower()):
            try:
                m = json.loads(line).get("message") or {}
            except Exception:
                m = {}
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Write", "Edit", "MultiEdit"):
                    fp = (b.get("input") or {}).get("file_path") or ""
                    if HANDOFF_RE.search(fp):
                        handoff = fp
        if handoff and title:
            break
except Exception:
    pass
home = os.path.expanduser("~")
short = lambda p: p.replace(home, "~", 1) if p.startswith(home) else p
first = f"Continue from {short(handoff)} (read it first)." if handoff else (
    f'Continue the "{title}" chat.' if title else "Continue the previous chat.")
opener = f"{first} Previous transcript: {short(path)}; search it only if something is missing, never read it whole."
head = (f"Context is {k}k, past the {HARD_K}k limit. This prompt was NOT sent.\n\n"
        f"To send it here anyway, resend it starting with keep: (a yes to a pending question, a save to the handoff, "
        f"or checking on work still running).\n\n"
        f"Open a new chat and paste:\n\n")
# The desktop app shows a block reason as the turn's error, cut at ~1,000 characters and 10 lines, so the opener goes
# last as one line and the echoed prompt gets only the room that is left.
said = " ".join(prompt.split())
room = 990 - len(head) - len(opener) - len(" Then: ")
if said and room > 40:
    opener += " Then: " + (said if len(said) <= room else said[:room - 1].rstrip() + "…")
print(json.dumps({"decision": "block", "reason": head + opener}))

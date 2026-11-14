#!/usr/bin/env python3
"""PreToolUse hook on Agent|Workflow: deny any agent spawn without an explicit model (the no-frontier-fan-out rule),
deny Workflow resumeFromRunId (re-runs the fleet live), and require a lean-* agent type on every spawn unless it is a
built-in type on the allow-list or carries a `lean-optout` marker (v2, 2026-10-18).

Why the lean rule: since the Oct 12 install, 482 of 662 agents still ran as general-purpose (median first-call floor
53.8k, re-read on every turn) against 16-20k for the lean types. The allow-list keeps built-in and skill agent types
working; the opt-out covers the few lanes that need MCP or browser tools (3 of 482)."""
import sys, json, os, re

LEAN = "lean-"
ALLOW = {"Explore", "Plan", "claude-code-guide", "statusline-setup"}
OPTOUT = re.compile(r"lean-optout", re.I)
TYPES = ("lean-sweep (haiku, 10 turns), lean-reader / lean-reader-long (sonnet, 15/40), "
         "lean-writer / lean-writer-long (sonnet, 15/40), lean-author (sonnet, 40, high effort: questions, keys, "
         "solutions), lean-judge / lean-judge-long (opus, 20/40), lean-research (opus, 25, web search + fetch)")


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


def agent_spans(script):
    """Yield (line number, text of the call) for every agent( ... ) call, skipping over quoted strings."""
    for m in re.finditer(r"\bagent\s*\(", script):
        i = m.end(); depth = 1; q = None; n = len(script)
        while i < n and depth:
            c = script[i]
            if q:
                if c == "\\": i += 1
                elif c == q: q = None
            elif c in "'\"`": q = c
            elif c == "(": depth += 1
            elif c == ")": depth -= 1
            i += 1
        yield script[:m.start()].count("\n") + 1, script[m.start():i]


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    tool = data.get("tool_name"); inp = data.get("tool_input") or {}

    if tool == "Agent":
        st = str(inp.get("subagent_type") or "")
        lean = st.startswith(LEAN)
        if not inp.get("model") and not lean:
            deny("Agent call has no `model`. Rule: pin model on every spawn (sonnet for mechanical, haiku for sweeps, "
                 "opus for judgment); omitting it inherits the session model. Better: use a lean-* subagent_type, "
                 f"which carries its model, a turn cap and no MCP tool schemas: {TYPES}.")
        optout = OPTOUT.search(str(inp.get("prompt") or "")) or OPTOUT.search(str(inp.get("description") or ""))
        if not lean and st not in ALLOW and not optout:
            deny(f"subagent_type '{st or 'general-purpose'}' starts every turn at ~54k tokens; the lean types start at "
                 f"16-20k. Use one: {TYPES}. A `model` you pass still overrides the type's model. If this lane truly "
                 "needs MCP or browser tools, add the line `lean-optout: <reason>` to the prompt and call again.")
        return

    if tool == "Workflow":
        if inp.get("resumeFromRunId"):
            deny("resumeFromRunId re-runs the whole fleet live. Harvest journal.jsonl offline and launch a fresh, "
                 "pinned finisher.")
        script = inp.get("script") or ""
        sp = inp.get("scriptPath")
        if not script and sp and os.path.exists(os.path.expanduser(sp)):
            try: script = open(os.path.expanduser(sp)).read()
            except Exception: script = ""
        if not script:
            return
        no_model, no_lean = [], []
        for line, span in agent_spans(script):
            lean = re.search(r"""\bagentType\s*:\s*["'`]lean-""", span)
            builtin = re.search(r"""\bagentType\s*:\s*["'`](%s)["'`]""" % "|".join(map(re.escape, ALLOW)), span)
            if not lean and not re.search(r"""\bmodel\s*:|["']model["']\s*:""", span):
                no_model.append(line)
            if not lean and not builtin and not OPTOUT.search(span):
                no_lean.append(line)
        if no_model:
            deny(f"Workflow script has agent() calls without `model:` at script line(s) {no_model[:12]}. HARD "
                 "PREFLIGHT RULE: every agent() names its model (or agentType: 'lean-*'); an unpinned call inherits "
                 "the session model. Fix and relaunch.")
        if no_lean:
            deny(f"Workflow script has agent() calls without a lean agentType at script line(s) {no_lean[:12]}. "
                 f"Write agentType as a literal string in each call: {TYPES}. A `model:` in the call still overrides "
                 "the type's model. For a lane that truly needs MCP or browser tools, put the comment "
                 "/* lean-optout: reason */ inside that agent( ... ) call, with no quote marks in the reason.")


try:
    main()
except SystemExit:
    raise
except Exception:
    pass  # fail open: a guard bug must never block work
sys.exit(0)

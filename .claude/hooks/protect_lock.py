#!/usr/bin/env python3
"""Claude Code PreToolUse hook: stop Claude from editing the lock itself or running ad-hoc queries on locked seasons.
Reads the tool call as JSON on stdin; prints a deny decision to block. Exit 0 always."""
import json, os, re, sys

PROTECTED = ("validation/ratings/FREEZE", "validation/ratings/locked_approvals.json", "validation/ratings/lockguard.py",
             ".claude/hooks/", ".claude/settings")
READ_ONLY = ("cat ", "ls ", "grep ", "head ", "tail ", "wc ", "git diff", "git log", "git show", "git status", "sed -n")
LOCKED = re.compile(r"20(22-23|23-24|24-25|25-26)")
ADHOC_SQL = re.compile(r"psql|select\s|from\s+player_", re.I)

call = json.load(sys.stdin)
tool, inp = call.get("tool_name", ""), call.get("tool_input", {})
frozen = os.path.exists("validation/ratings/FREEZE")


def deny(why):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": why}}))
    sys.exit(0)


if tool in ("Write", "Edit", "MultiEdit") and any(p in inp.get("file_path", "") for p in PROTECTED):
    deny("Protected: the locked-set guard can only be changed by Xavier.")
if tool == "Bash":
    cmd = inp.get("command", "")
    if any(p in cmd for p in PROTECTED) and not cmd.lstrip().startswith(READ_ONLY):
        deny("Protected: the locked-set guard can only be changed by Xavier.")
    if not frozen and LOCKED.search(cmd) and ADHOC_SQL.search(cmd):
        deny("Locked seasons are closed until the FREEZE file exists; ad-hoc queries on them are blocked.")

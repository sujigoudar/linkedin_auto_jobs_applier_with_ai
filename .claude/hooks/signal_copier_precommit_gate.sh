#!/usr/bin/env bash
# PreToolUse hook on Bash: before a `git commit` runs, prove
# signal-copier is sound (lint + CI-scoped typecheck) and BLOCK the
# commit if either fails. Mirrors the "before you push, prove the
# change is sound" discipline this session enforced by hand in every
# agent dispatch (see docs/agents/TOKEN_COST_AUDIT.md finding #1).
#
# Only runs when the command actually looks like a git commit AND
# signal-copier/ exists in this checkout, so it never fires for
# unrelated commits (e.g. a signal-portfolio-commercial-only change) or
# outside this monorepo. It intentionally does NOT run the full test
# suite here -- that's `make audit`/`make test`, too slow for a
# per-commit gate; lint+typecheck are the fast, deterministic checks
# worth gating every commit on.
#
# Blocking convention: prints a JSON hookSpecificOutput with
# permissionDecision: "deny" AND exits 2 (the command-hook blocking exit
# code), so the commit is refused either way the harness reads it.

set -uo pipefail

input="$(cat)"
command="$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)"

case "$command" in
  *git\ commit*) ;;
  *) exit 0 ;;
esac

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
sc_root="$repo_root/signal-copier"

if [ ! -d "$sc_root" ] || [ ! -f "$sc_root/Makefile" ]; then
  # Nothing to gate -- signal-copier isn't part of this checkout/branch.
  exit 0
fi

lint_output="$(cd "$sc_root" && make lint 2>&1)"
lint_status=$?

typecheck_output=""
typecheck_status=0
if [ "$lint_status" -eq 0 ]; then
  typecheck_output="$(cd "$sc_root" && make typecheck 2>&1)"
  typecheck_status=$?
fi

if [ "$lint_status" -ne 0 ] || [ "$typecheck_status" -ne 0 ]; then
  REASON="signal-copier pre-commit gate failed.

make lint (exit $lint_status):
$lint_output

make typecheck (exit $typecheck_status):
$typecheck_output

Fix the above (or run 'cd signal-copier && make lint && make typecheck' yourself) before committing." \
  python3 -c '
import json, os
reason = os.environ["REASON"]
print(json.dumps({
    "decision": "block",
    "reason": reason,
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    },
}))
'
  exit 2
fi

exit 0

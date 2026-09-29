#!/usr/bin/env bash
# PostToolUse hook: after Edit/Write touches a signal-copier/**/*.py file,
# run `ruff check` on just that one file for fast, targeted feedback --
# not a full-repo lint on every keystroke-adjacent edit. Non-blocking:
# reports ruff's findings back into context but never fails the tool
# call itself (a lint issue on partial/in-progress code is common and
# shouldn't stop the edit that revealed it).
#
# See docs/agents/TOKEN_COST_AUDIT.md finding #1 (deterministic-check
# instructions re-typed in full, per agent -- this hook removes the need
# for an agent to remember to lint after every signal-copier edit).
#
# Reads the PreToolUse/PostToolUse hook input JSON on stdin:
#   {"tool_name": "...", "tool_input": {"file_path": "..."}, ...}

set -uo pipefail

input="$(cat)"
file_path="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"

# Only act on real .py paths under a signal-copier/ directory anywhere in
# the given path (handles both absolute and repo-relative paths).
case "$file_path" in
  *signal-copier/*.py) ;;
  *) exit 0 ;;
esac

if [ ! -f "$file_path" ]; then
  exit 0
fi

# Path relative to signal-copier/ (strip everything up to and including
# the last "signal-copier/" segment), since ruff must run with
# signal-copier/ as its cwd to pick up its pyproject.toml scoped ruleset.
rel="${file_path##*signal-copier/}"

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
sc_root="$repo_root/signal-copier"

if [ ! -d "$sc_root" ]; then
  exit 0
fi

output="$(cd "$sc_root" && python -m ruff check "$rel" 2>&1)"
status=$?

if [ "$status" -ne 0 ]; then
  # Non-blocking: surface the finding as additional context via valid
  # JSON, don't fail the edit itself. Building the JSON in Python avoids
  # hand-rolled shell string escaping.
  REL="$rel" OUTPUT="$output" python3 -c '
import json, os
msg = "ruff check " + os.environ["REL"] + " reported issues:\n" + os.environ["OUTPUT"]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": msg,
    }
}))
' 2>/dev/null || true
fi

exit 0

#!/bin/bash

# PreToolUse hook: intercept git push and ask for user confirmation via a dialog

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('tool_input', {}).get('command', ''))" 2>/dev/null)

# Only intercept git push commands
if echo "$COMMAND" | grep -qE '^\s*git push'; then
  RESULT=$(osascript -e 'button returned of (display dialog "Claude wants to run:\n\ngit push\n\nAllow this push?" buttons {"Cancel", "Allow"} default button "Allow" with title "Claude Code — Confirm Push")')
  if [ "$RESULT" != "Allow" ]; then
    echo '{"reason": "Push cancelled by user."}'
    exit 2
  fi
fi

exit 0

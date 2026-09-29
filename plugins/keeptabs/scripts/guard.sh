#!/bin/sh
# UserPromptSubmit ("prompt") and PreToolUse: run the POC's guard.py from
# ~/.claude/keeptabs. On each prompt, and whenever the home is missing (plugin
# installed mid-session, before any SessionStart), make sure the home and the
# collector are in place first. That step must not read the hook's stdin.
HOME_DIR="$HOME/.claude/keeptabs"
if [ "$1" = prompt ] || [ ! -f "$HOME_DIR/guard.py" ]; then
  python3 "$(dirname "$0")/home.py" ensure </dev/null >/dev/null 2>&1
fi
exec python3 "$HOME_DIR/guard.py"

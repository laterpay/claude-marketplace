#!/bin/sh
# UserPromptSubmit ("prompt") and PreToolUse: run keeptabs' guard.py. On each
# prompt, first make sure the data folder and the collector are in place (this
# also covers a plugin installed mid-session, before any SessionStart). That
# step must not read the hook's stdin.
DIR="$(dirname "$0")"
if [ "$1" = prompt ]; then
  python3 "$DIR/home.py" ensure </dev/null >/dev/null 2>&1
fi
exec python3 "$DIR/../keeptabs/guard.py"

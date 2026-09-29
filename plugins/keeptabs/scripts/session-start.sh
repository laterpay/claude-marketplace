#!/bin/sh
# SessionStart: copy the POC into ~/.claude/keeptabs and lazy-start the collector.
exec python3 "$(dirname "$0")/home.py" session-start

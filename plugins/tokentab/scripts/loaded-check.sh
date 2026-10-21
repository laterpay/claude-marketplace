#!/bin/sh
# UserPromptSubmit: the version gate. TokenTab's hooks module writes state/loaded/<session-id>
# at session start; Claude Code before 2.1.287 does not load modules, and nothing then
# tracks this session. Said once per session, on the first prompt without a marker. The
# data folder is the one the module builds from CLAUDE_CONFIG_DIR or HOME, not
# ${CLAUDE_PLUGIN_DATA}, so the two agree however the plugin was loaded.
SID=$(sed -n 's/.*"session_id" *: *"\([^"]*\)".*/\1/p' | head -1)
[ -n "$SID" ] || exit 0
DATA="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/data/tokentab-supertab"
[ -e "$DATA/state/loaded/$SID" ] && exit 0
[ -e "$DATA/state/gate/$SID" ] && exit 0
mkdir -p "$DATA/state/gate" && : > "$DATA/state/gate/$SID"
printf '%s\n' '{"systemMessage": "TokenTab: its hooks module did not load, so nothing is tracked in this session. Claude Code 2.1.304 or later is needed; `claude --version` shows yours. Update, then start a new session."}'

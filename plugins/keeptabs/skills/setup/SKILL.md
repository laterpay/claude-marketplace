---
name: setup
description: Switch on full keeptabs tracking by pointing Claude Code's telemetry at the local keeptabs collector (edits the env block of ~/.claude/settings.json, needs a restart). Pass "undo" to remove it again.
argument-hint: "[undo]"
disable-model-invocation: true
---

# keeptabs setup

keeptabs already works without this step: the guard reads transcripts and marks its
figures INCOMPLETE. This step adds Claude Code's own telemetry, which also covers
background calls, web searches and helpers. A plugin cannot switch telemetry on by
itself, so it takes one change to `~/.claude/settings.json` and a restart.

The script for every step is:

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/telemetry.py" <plan|apply|revert>

Arguments: $ARGUMENTS

## If the argument is "undo"

1. Run `telemetry.py revert` and show the user its output.
2. Tell them to restart Claude Code. Their ledger stays until the plugin is uninstalled.
   Stop here.

## Otherwise

1. Run `telemetry.py plan`. It changes nothing.
2. If it reports "Already set up", say so, remind the user that sessions started before
   setup do not send telemetry, and suggest `/keeptabs:status` to check. Stop here.
3. Show the user the plan: every setting that will change, the "What this means"
   points, the auto-update line if there is one, and any WARNING about replacing existing values, in full. Do not shorten
   the consent points.
4. Ask the user to confirm, and wait for an explicit yes. Anything else means do not
   apply. This is their global Claude Code configuration, so do not treat having typed
   the command as consent.
5. On yes, run `telemetry.py apply` and show where the settings backup went.
6. Finish with the next step in plain words: quit Claude Code (every open session) and
   start it again. If the plan mentioned auto-update, add that it only runs in sessions
   started from the terminal CLI; the Claude Desktop app disables it, and updates are
   then pulled with `claude plugin marketplace update supertab`. Open sessions keep running without telemetry. In the new session,
   `/keeptabs:status` should report "collector: OK" once Claude has replied at least
   once. To undo later: `/keeptabs:setup undo`.

Do not edit `~/.claude/settings.json` by any other means, and do not touch hooks there:
the plugin registers its own.

---
name: uninstall
description: Prepare to remove keeptabs — take its telemetry settings out of ~/.claude/settings.json and stop the collector — then show the one command that uninstalls the plugin.
disable-model-invocation: true
---

# keeptabs uninstall

A plugin cannot uninstall itself from inside a session, and two things keeptabs set up
live outside the plugin: the telemetry settings in `~/.claude/settings.json` (if the user
ran `/keeptabs:setup`) and the collector process. This command undoes both, then hands
the user the uninstall command.

The scripts are:

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/telemetry.py" revert

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/home.py" stop-collector

1. Tell the user in two lines what this does: remove keeptabs' telemetry settings from
   `~/.claude/settings.json` (restoring any value setup replaced) and stop the collector.
   Nothing is deleted yet. Ask them to confirm and wait for an explicit yes; anything
   else means stop here.
2. Run `telemetry.py revert` and show its output. "No keeptabs telemetry settings found"
   is fine: setup was never run, or was already undone.
3. Run `home.py stop-collector` and show its output. The collector stays stopped in
   this session; the next session start would bring it back, which is why the user
   uninstalls now.
4. Give the user the next steps, in plain words:
   - Run this in a terminal (not through you). It deletes keeptabs' data: ledger, raw
     events, guard state and `budget.json`. `--keep-data` keeps it.

         claude plugin uninstall keeptabs@supertab

   - Then quit and restart Claude Code, every open session.
   - Optional, to remove the marketplace too: `claude plugin marketplace remove supertab`.

Do not run the uninstall command yourself: the plugin's hooks are running in this
session, and its data folder is deleted under them. Do not edit `~/.claude/settings.json`
by any other means.

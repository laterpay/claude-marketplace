---
name: status
description: Show what this Claude Code session has spent and whether keeptabs tracking is complete. Use when the user asks about keeptabs, their spend, cost so far, budgets, or whether usage tracking is working.
---

# keeptabs status

Run these as two separate Bash calls, each exactly as written. keeptabs never blocks
these two commands, even during a budget stop, but only when each is run on its own:
do not chain them with `&&`, pipes or redirects.

    python3 ~/.claude/keeptabs/keeptabs.py --once

    python3 ~/.claude/keeptabs/health.py

The first prints a snapshot of the active session: cost by rate, requests, budget bars
and cost per tool. It contains terminal colour codes; leave those out when you relay it.
The second says whether telemetry is flowing ("collector: OK") or what is wrong.

Report the session cost, the budget lines and the health verdict in a few lines.

If health reports a problem:
- "telemetry is not switched on": the user has not run `/keeptabs:setup` yet. Figures
  come from transcripts only and miss background calls and web searches.
- "sessions started before setup" or "this session sends no telemetry": setup is done
  but this session predates it. Restarting Claude Code fixes it.
- "the collector is not running": starting a new session starts it again. The
  collector's log is `~/.claude/keeptabs/state/collector.log`.
- Health's own text may say "Run setup.py". With the plugin, that means `/keeptabs:setup`.

Budgets are set in `~/.claude/keeptabs/budget.json`. Do not edit that file, or anything
else in `~/.claude/keeptabs`, unless the user explicitly asks you to.

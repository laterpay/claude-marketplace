---
name: budget
description: Show or change keeptabs' spending limits (session, daily, per request, job budgets, warnings, hard stop). User-invoked; pass what to change, e.g. "daily 20" or "hard stop on".
argument-hint: "[what to change]"
disable-model-invocation: true
---

# keeptabs budget

The limits live in one file:

    ${CLAUDE_PLUGIN_DATA}/budget.json

It belongs to the user. Plugin updates never change it; uninstalling the plugin deletes
it together with the rest of keeptabs' data.

Arguments: $ARGUMENTS

## No arguments: show the limits

Read the file and show, in a short list: session, daily and per-request limits (USD and
tokens, `null` means off), `warn_at_percent`, `hard_stop` (true blocks at 100%, false only
warns), and the job budget settings (`job.ask_over_usd`, `job.hard_stop`, the word scale).
Say that a job budget is set by starting a prompt with, for example, `$2:` or `beer:`,
and cleared with `nobudget:`. Give the file path, and mention that the user can ask for a
change with `/keeptabs:budget <what to change>`.

## With arguments: change the limits

1. Work out exactly which keys change and to what, and show the change (old → new)
   before writing. If the request is ambiguous, ask.
2. Change only those keys and keep the file valid JSON, including its `_comment` keys.
3. Show the new values. They apply from the next prompt; no restart is needed.

keeptabs tells Claude never to raise a limit on its own. That rule is about Claude
acting unasked; here the user asked. If keeptabs blocks the edit anyway (for example
during a hard stop), give the user the path so they can edit the file themselves.

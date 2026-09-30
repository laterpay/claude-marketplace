# keeptabs (local prototype)

> This is the standalone POC's README. Installed as the plugin, keeptabs is set up,
> updated and removed as described in the [plugin README](../README.md): `setup.py` is
> not shipped, there is no launchd job, and the plugin's `home.py` starts the collector.
> The parts and file formats below apply to both.

Shows what Claude Code is spending, stops it at limits you set, and gives the agent
the numbers it needs to stay inside a budget. Standard library Python only, no
dependencies. macOS (standalone: the collector runs under launchd).

Three parts:

| Part | File | Job |
|---|---|---|
| Collector | `collector.py` | Receives Claude Code's telemetry and writes one ledger line per API call |
| Guard | `guard.py` | Claude Code hook: warns, stops, asks for budgets, sends budget hints |
| Live view | `keeptabs.py` | Terminal view of spend: session, requests, budgets, tools |

Plus `health.py` (is telemetry flowing?), `setup.py` (install) and `gate/` (a
gateway prototype, see the end).

## Install

1. Unzip so the folder sits at **`~/.claude/keeptabs`**. `guard.py` looks for its
   files there, so any other location will not work.

       cd ~/.claude && unzip ~/Downloads/keeptabs.zip

2. Set your own limits in `budget.json` (see Budgets below). The file in the zip
   carries someone else's limits.

3. Run setup. It is safe to re-run, and it backs up `~/.claude/settings.json` first.

       python3 ~/.claude/keeptabs/setup.py

   It does three things:
   - adds the telemetry env vars to `~/.claude/settings.json`
     (`CLAUDE_CODE_ENABLE_TELEMETRY=1`, OTLP over HTTP/JSON to `127.0.0.1:4318`)
   - registers `guard.py` as a `UserPromptSubmit` and `PreToolUse` hook
   - registers `collector.py` with launchd (`co.supertab.keeptabs.collector`), so it
     runs at login and restarts if it dies. Log: `state/collector.log`

4. **Start a new Claude Code session.** Sessions that were already open do not send
   telemetry. Then check it:

       python3 ~/.claude/keeptabs/health.py

   Three checks: telemetry is switched on in settings, the collector's heartbeat is
   fresh, and Claude has not replied since the collector last heard anything.

To remove everything setup added (env vars, hooks, launchd job):

    python3 ~/.claude/keeptabs/setup.py uninstall

## Live view

    python3 ~/.claude/keeptabs/keeptabs.py            # live, follows the active session
    python3 ~/.claude/keeptabs/keeptabs.py --once     # one snapshot
    python3 ~/.claude/keeptabs/keeptabs.py --json     # machine-readable
    python3 ~/.claude/keeptabs/keeptabs.py --session <id>

Shows: the session's cost by rate (input, cache write, cache read, output, web search
fees), spend by call source (main loop, prompt suggestions, web search, helpers),
**requests** (latest, previous, average: one prompt and everything it set off,
including the prompt suggestion after it), budget bars (session, today, job), the
context size and what each call costs to re-read it, and cost per tool.

`--json` includes `requests` (last 5) and `by_source`.

## Where the numbers come from

- **Ledger first.** Telemetry (`ledger/YYYY-MM-DD.jsonl`) covers every API call,
  including ones transcripts never show: prompt suggestions, web search, web fetch,
  and helpers (subagents), which arrive under the parent session as `agent:*`.
- **Transcripts as the fallback.** `~/.claude/projects/**`, including each session's
  `subagents/*.jsonl`. They miss background calls, so when the collector is down the
  figures are marked INCOMPLETE.
- The guard uses **the larger of the two**, so switching source never lowers a total.
- **Prices** come from `prices.json` (published per-token rates; `_verified: true`).
  Five rates matter: input, output, cache write 5m, cache write 1h, cache read.
- **Fees.** Web search is billed per search ($0.01), not per token. The collector
  records it as `fees_usd`: whatever Claude Code's own cost figure has on top of the
  token price. `cost_usd` stays tokens only. Older ledger lines are corrected on read.

Correctness rules, each learned the hard way:
- Deduplicate by `requestId`. One request is logged on several lines while its output
  streams in; count it once and let later lines add only what grew. Counting every
  line double counts; counting only the first misses output.
- A resumed session repeats its parent's history with the same request ids. Those
  are counted once, by the session they came from.
- Tail by byte offset and hold back a partial last line, so reading a file that is
  being written is safe.

## Budgets

All in `budget.json`, in USD, tokens, or both. Whichever is closest to its limit
governs. `null` turns one off.

| Scope | Keys | Resets |
|---|---|---|
| Session | `session_limit_usd`, `session_limit_tokens` | new session |
| Today | `daily_limit_usd`, `daily_limit_tokens` | midnight, all sessions and projects |
| Request | `turn_limit_usd`, `turn_limit_tokens` | every prompt |
| Job | set from the prompt (below) | when you set a new one |

`warn_at_percent` (default 70) starts warnings, once per 10% band. `hard_stop: true`
blocks at 100%; `false` only warns. Job budgets have their own `job.hard_stop`.

### Job budgets

Start a prompt with a budget and a colon:

    $2: find me a comparable plan
    beer: tidy up this module
    200k: summarise these docs
    nobudget: carry on without one

- Counted from the moment it is set, across prompts, until you set a new one.
- Words map to the scale in `job.scale` (water $0.50, beer $6, pizza $20, wine $50,
  champagne $100). Other words before a colon (`note:`, `todo:`) are ignored.
- A prompt that is only pasted text still picks up its prefix.
- At 100% (with `job.hard_stop`) tool calls are refused and Claude reports what is
  done, what is left, and a realistic budget for the rest.

### Asking for a budget

- **First prompt of a session, no job budget:** Claude is asked to estimate the job
  in dollars and on the scale. Under `job.ask_over_usd` (default $1) it states the
  estimate and proceeds; over it, it asks first.
- **Mid-request check-in** (`job.ask_mid_request`): when one request passes
  `ask_over_usd` with no job budget, every tool call is refused, main conversation
  and helpers alike, until you send a new message. Claude says what is done, what is
  left and what the rest would cost. A helper is told to stop and report back, and
  that only the user can set a budget.
- Estimates start from real history: each request's cost is kept in state.

### Budget hints

The guard gives Claude numbers, not tactics. We do not yet have the data to say which
method is cheapest for a given job, so it does not recommend one.

- With every prompt while a job budget runs:
  `$0.80 spent, $1.20 left. Each call here costs at least $0.022 just to re-read this
  context, so at most about 53 more calls fit.` The per-call figure is a true floor
  (the re-read alone), so the call count is a ceiling, not a guess.
- Warnings are always shown in dollars.

### New session check

Every call re-reads the whole context, so context size is the running cost per call.
With every prompt, once the context is at least `new_session.min_ratio` (default 2)
times a fresh session, Claude gets the context size, the cost per call and the
break-even point for starting over. Claude decides whether the new prompt needs this
conversation; if not, it suggests a new session and offers a handoff note.

### Context size and cold resume

- `context_warn_tokens` (e.g. `[200000, 350000]`): warns once per threshold, re-arms
  after a compact.
- `cold_resume`: the prompt cache lives for an hour (or 5 minutes) from the **start**
  of the last request that touched it. Once it expires, the next call re-writes the
  whole context at the cache-write rate. `UserPromptSubmit` runs before anything is
  sent, so the guard **blocks** that prompt once, even in warn-only mode, with the
  cost and the cost of a new session. Sending again within `confirm_seconds` goes
  ahead. `/compact` does not avoid it: compacting reads the whole context once too.

### Helpers (subagents)

A helper's tool calls carry `agent_id`, so the guard can tell them apart.
- Hard stops apply to helpers; warnings meant for the main conversation do not.
- Helpers may not start other agents or message them (`helpers_may_delegate: false`).
  Each handoff loses instructions and adds its own start-up cost.
- Helper spend counts toward every budget, from telemetry or their transcripts.

### Never blocked

Reading keeptabs' own numbers (`python3 .../keeptabs.py` or `health.py` with plain
flags) always goes through, even during a pause or a hard stop.

## Findings so far

- **Prompt suggestions cost money you did not ask for.** After each turn Claude Code
  makes a call to suggest your next prompt. It re-reads the whole context: about 2¢
  in a short chat, about 5¢ in a long one.
- **A $1+ job usually starts mid-session**, not on the first prompt. The mid-request
  check-in exists because of that.
- **A cheaper model is not automatically cheaper.** On one browser-form job a Haiku
  helper made 4x the calls of Opus, took longer and did not finish. One site is not a
  rule, which is why the hints carry numbers only.

## Known limits

- **Granularity is one tool call.** Nothing stops a response mid-generation, so the
  real ceiling is the limit plus whatever the in-flight call costs.
- **Claude Code only.** Claude chat runs in Anthropic's cloud and sends no telemetry.
- **Claude can remove the hook.** It has shell access and `settings.json` is
  user-writable. This is a cooperative control until the config lives where the agent
  cannot write (managed settings, or a wrapper process).
- **Guard state** lives in `state/<session_id>.json`. It is a cache: delete it and the
  guard recomputes the same totals from the transcript.
- **Debugging:** `touch state/debug` logs each hook's input (without prompts or tool
  input) to `state/payloads.log`. Remove the file to stop.

## Threat model

### The claim

> keeptabs binds a spend limit for an agent running as an unprivileged user on a
> machine where the enforcement chain is root-owned and the usage record cannot be
> rewritten. It does not bind an agent that can escalate privilege.

### What has to be true for that to hold

1. `guard.py`, `budget.json`, `prices.json` **and the directory containing them** are
   owned by root. The directory matters: without it the agent moves a file aside and
   writes a replacement.
2. The hook is registered in **managed settings** with `allowManagedHooksOnly: true`,
   so a user-writable `settings.json` cannot unhook it, plus `disableSideloadFlags`
   to close the `--settings` override.
3. The agent has no sudo, does not run as root, and has no passwordless escalation.
4. The agent cannot launch a different harness or SDK process outside managed settings.
5. The usage record is tamper-evident (below) or, better, held off the machine.
6. `state/` stays user-writable. That is fine: it is a cache, not the truth.

### Point 6 is a design rule, not an accident

**Derive the total from an append-only log. Never keep a counter.** Delete every state
file and the guard recomputes the same answer from the transcript. A counter would
have been a one-line bypass. Same reason a ledger derives balances from entries.

### The transcript is inside the trust boundary

Claude Code must write `~/.claude/projects/`, so it cannot be root-owned, so the agent
can write it too. Truncating the transcript would otherwise lower the total with no
privilege and nothing in the enforcement chain touched.

Mitigation: the total is **monotonic**. A recomputed figure below the recorded
high-water mark is treated as tampering, not rotation. The guard keeps the larger
number, refuses, appends to `state/tamper.log`, and **stays refused** until a human
clears `state/<session>.json`.

This detects rather than prevents, and the flag lives in a user-writable file, so an
agent that truncates the transcript *and* deletes state gets a clean slate. Closing
that properly means the running total lives off the machine, where the thing being
measured cannot reach it. **That is the strongest argument for a remote counter: not
fleet policy, but that a local record is forgeable by the agent it measures.**

### Out of scope

An agent that persuades its user to run something with sudo. That is social
engineering of the principal and no technical control here addresses it.

### What has actually been demonstrated

- 2026-09-23: the `PreToolUse` hook denied a tool call mid-turn at `$170.24 of
  $170.00` and the agent stopped without cooperating in the stop. Overshoot was one
  tool call, the documented granularity limit.
- 2026-09-24: the mid-request check-in paused a helper at $1.04 and held every agent
  until the user replied; a helper could not grant itself a budget.
- The tamper check is verified against synthetic truncation, not yet in the wild.

## gate/ (prototype)

`gate/gate.py` is a metering gateway for the Anthropic API. It holds the real API key,
gives each person their own gate key (`gate.json`), prices and records every call,
and refuses over-budget calls with HTTP 402 before anything is billed. It is the path
to agents other than Claude Code, and to a running total held off the machine.

    export ANTHROPIC_UPSTREAM_KEY=sk-ant-...      # the real key, gate only
    python3 gate/gate.py                           # listens on 127.0.0.1:8402

    # client side
    export ANTHROPIC_BASE_URL=http://127.0.0.1:8402
    export ANTHROPIC_API_KEY=gk-...                # a gate key from gate.json

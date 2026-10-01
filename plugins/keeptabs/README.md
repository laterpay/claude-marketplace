# keeptabs

Supertab's proof of concept that shows what Claude Code is spending, stops it at limits
you set, and gives the agent the numbers it needs to stay inside a budget. Packaged as
a plugin in [claude-marketplace](../../README.md). The POC's own documentation is
in [`keeptabs/README.md`](keeptabs/README.md).

macOS, Python 3 (standard library only).

## Install

The repo is private: you need access and working git credentials, see the
[marketplace README](../../README.md).

**In one step** (Claude Code v2.1.275 or later), inside a session. This adds the
marketplace and installs keeptabs; Claude Code asks you to confirm the source, then
to pick a scope (choose "Install for you" to have it in every project):

```
/plugin install keeptabs --marketplace laterpay/claude-marketplace
```

**In two steps** (any version):

```
/plugin marketplace add laterpay/claude-marketplace
/plugin install keeptabs@supertab
```

From a shell instead: `claude plugin marketplace add laterpay/claude-marketplace`,
then `claude plugin install keeptabs@supertab`. The shell has no one-step form.

**Start a new session** (or run `/reload-plugins`). keeptabs is working from that point:

- the budget guard runs on every prompt and tool call,
- the collector is started in the background,
- figures come from transcripts and are marked **INCOMPLETE**, because transcripts
  miss background calls, web searches and some helper calls.

Set your own limits with `/keeptabs:budget`. The defaults are the POC author's limits (warn-only, $25 per session, $40 per day, $2 per request, and a job
budget check-in at $1). See Budgets in the POC README.

## Full tracking: `/keeptabs:setup`

```
/keeptabs:setup
```

It says in a few lines what it will change and asks before changing anything (ask for details to see every setting and what gets recorded). On yes it adds
Claude Code's telemetry settings to the `env` block of `~/.claude/settings.json` (backup
first), pointing them at the local collector on `127.0.0.1:4318`. **Then quit and
restart Claude Code.** Telemetry settings are read only when Claude Code starts, and a
plugin cannot set them itself.

Check with `/keeptabs:status`.

`collector: OK` means telemetry is flowing and the INCOMPLETE marker is gone.

Setup also turns on auto-update for the marketplace. That only takes effect in sessions
started from the terminal CLI: the Claude Desktop app disables Claude Code's updater,
and plugin auto-update with it. From Desktop, update by hand (next section).

## Updates

Update as described in the [marketplace README](../../README.md) (from the Claude
Desktop app always by hand: `claude plugin marketplace update supertab`, since Desktop
disables auto-update), then start a new session. It runs the new version straight
away. The first prompt in a session running the new version stops a collector started
by an older version and starts its own. Sessions still running the old version swap it
back on their next prompt, so close those; the last version standing wins.

## Uninstall

```
/keeptabs:uninstall
```

It asks, then takes keeptabs' telemetry settings out of `~/.claude/settings.json`
(restoring any value setup replaced, for example a company OTel endpoint) and stops the
collector. Then run, in a terminal:

```bash
claude plugin uninstall keeptabs@supertab
```

This also **deletes keeptabs' data** (ledger, raw events, guard state and your
`budget.json`). Add `--keep-data` to keep it. Restart Claude Code afterwards.

By hand instead: remove `CLAUDE_CODE_ENABLE_TELEMETRY`, `OTEL_LOGS_EXPORTER`,
`OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_EXPORTER_OTLP_ENDPOINT` and
`OTEL_LOGS_EXPORT_INTERVAL` from the `env` block of `~/.claude/settings.json` (setup left
a backup: `~/.claude/settings.json.keeptabs-<date>.bak`), stop the collector with
`pkill -f keeptabs/collector.py`, and uninstall. Stop the collector first: uninstalling
deletes its data folder while it runs, and until it notices (15s) telemetry from open
sessions re-creates `ledger/` and `raw/`.

## Where things are

| What | Where |
|---|---|
| Code | The plugin folder, `${CLAUDE_PLUGIN_ROOT}` (`~/.claude/plugins/cache/supertab/keeptabs/<version>/`; the version is the git commit sha) |
| Data: `budget.json`, `ledger/`, `raw/`, `state/` | The plugin's data folder, `${CLAUDE_PLUGIN_DATA}` (`~/.claude/plugins/data/keeptabs-supertab/`). Kept across updates, deleted on uninstall |
| Telemetry settings | The `env` block of `~/.claude/settings.json`, after `/keeptabs:setup` |

Nothing else is written outside Claude Code's plugin folders.

To watch the live view in a separate terminal (it runs until Ctrl-C):

```bash
python3 ~/.claude/plugins/marketplaces/supertab/plugins/keeptabs/keeptabs/keeptabs.py
```

## How the POC is packaged

`keeptabs/` holds the POC (`guard.py`, `collector.py`, `keeptabs.py`, `health.py`,
`budget.json`, `prices.json`, `README.md`) and runs in place, from the plugin folder.
Its `setup.py` (replaced by the plugin) and `gate/` are left out. The plugin copies
only `budget.json` into the data folder, once, as your starting limits.

**The patch to the POC.** The POC expected its code and data together in
`~/.claude/keeptabs`. A small patch to four files splits them, each change marked
`# plugin: ...`:
- All four: data goes to the plugin's data folder (`CLAUDE_PLUGIN_DATA`, or
  `~/.claude/plugins/data/keeptabs-supertab` when run outside a hook).
- `guard.py`, `keeptabs.py`: `prices.json` (and, for the guard, `health`) come from the
  script's own folder.
- `guard.py`, `health.py`: messages point to `/keeptabs:budget` and `/keeptabs:setup`
  instead of the old paths and `setup.py`.

To see the whole patch (from the parent project):

```bash
diff -ru eric-poc marketplace/plugins/keeptabs/keeptabs -x gate -x setup.py
```

**Plugin parts:**

| Part | What it does |
|---|---|
| `hooks/hooks.json` | `SessionStart` → `session-start.sh`; `UserPromptSubmit` and `PreToolUse` (all tools) → `guard.sh` → `keeptabs/guard.py` |
| `scripts/home.py` | Prepares the data folder, lazy-starts the collector, reports first-run state |
| `scripts/telemetry.py` | Plan / apply / revert of the telemetry env vars (behind `/keeptabs:setup`) |
| `skills/setup` | `/keeptabs:setup`, user-invoked only |
| `skills/uninstall` | `/keeptabs:uninstall`: undo setup and stop the collector, user-invoked only |
| `skills/status` | `/keeptabs:status`: spend snapshot and health |
| `skills/budget` | `/keeptabs:budget`: show or change the limits, user-invoked only |

**Collector without launchd.** At session start (and before each prompt, so a crash
heals itself), `home.py` checks for a running collector. It asks the collector's
`/health` endpoint, then falls back to `state/collector.pid`. If none is running, or
the running one comes from another plugin version, writes to another data folder, or
answers but has not written its heartbeat for a minute, it stops that one and starts a
new one detached from the current version, under a file lock so sessions starting
together start only one. Port 4318 itself also guarantees a single instance. The
collector keeps running after Claude Code exits; after a reboot, the first session
starts it again. It is not an MCP server, because that would mean one per session and
a port clash.

The heartbeat check matters on reinstall: Claude Code deletes the plugin's data folder
on uninstall while the collector keeps running. The collector re-creates `state/` if
only that is missing, and exits within 15s once the data folder itself was deleted
(or deleted and re-created by a reinstall). Until it does, it still answers on the
port, which is why answering alone does not count as healthy.

**One workaround, outside the POC code.** Python's `http.server` looks up the machine's
full hostname before serving anything. On macOS that reverse lookup of `127.0.0.1` can
trigger the Local Network privacy prompt for Python and hang for 20+ seconds until
answered. Telemetry sent meanwhile can be lost, and `health.py` reports the collector
as down. The plugin starts `collector.py` with that lookup stubbed out (`LAUNCH` in
`home.py`); the name is only used for display.

## Troubleshooting

**`/keeptabs:status` says the collector is stuck.** It answers on port 4318 but writes
no heartbeat. This is what a collector looks like after its data folder was deleted
under it (an uninstall and reinstall of the same version, before this was handled):
on `curl 127.0.0.1:4318/health`, `alive_at` is frozen while `last_event_at` keeps
moving. Send a prompt in any session: the guard replaces it, and status is OK again.
Collectors from the current version exit on their own in that state.

## Notes for the POC author

- The `http.server` hostname lookup above: overriding `server_bind` (or setting
  `server_name` without `getfqdn`) in `collector.py` would remove the workaround.
- `setup.py` is not shipped. If its hooks are still in `~/.claude/settings.json` the
  guard runs twice, and its launchd job would keep an outdated collector alive; the
  plugin detects both at session start and says how to remove them.

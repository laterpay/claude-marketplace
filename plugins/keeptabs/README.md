# keeptabs

Supertab's proof of concept that shows what Claude Code is spending, stops it at limits
you set, and gives the agent the numbers it needs to stay inside a budget. Packaged as
a plugin in [supertab-claude-marketplace](../../README.md). The POC's own documentation is
in [`keeptabs/README.md`](keeptabs/README.md).

macOS, Python 3 (standard library only).

## Install

Add the marketplace first (see the [marketplace README](../../README.md) for access to
the private repo), then:

```
/plugin install keeptabs@supertab
```

**Start a new session** (or run `/reload-plugins`). keeptabs is working from that point:

- the budget guard runs on every prompt and tool call,
- the collector is started in the background,
- figures come from transcripts and are marked **INCOMPLETE**, because transcripts
  miss background calls, web searches and some helper calls.

Set your own limits in `~/.claude/keeptabs/budget.json`. The default carries the POC
author's limits (warn-only, $25 per session, $40 per day, $2 per request, and a job
budget check-in at $1). See Budgets in the POC README.

## Full tracking: `/keeptabs:setup`

```
/keeptabs:setup
```

It shows exactly what it will change and asks before changing anything. On yes it adds
Claude Code's telemetry settings to the `env` block of `~/.claude/settings.json` (backup
first), pointing them at the local collector on `127.0.0.1:4318`. **Then quit and
restart Claude Code.** Telemetry settings are read only when Claude Code starts, and a
plugin cannot set them itself.

Check with `/keeptabs:status`, or directly:

```bash
python3 ~/.claude/keeptabs/health.py
```

`collector: OK` means telemetry is flowing and the INCOMPLETE marker is gone.

## Updates

Update as described in the [marketplace README](../../README.md), then start a new
session. It runs the new version straight away, and the collector restarts itself
if it was started by an older version.

## Uninstall

1. Undo the telemetry settings, if you ran setup: `/keeptabs:setup undo` (while the
   plugin is still installed). Alternatively, remove these keys from the `env` block of
   `~/.claude/settings.json` by hand: `CLAUDE_CODE_ENABLE_TELEMETRY`, `OTEL_LOGS_EXPORTER`,
   `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_EXPORTER_OTLP_ENDPOINT`,
   `OTEL_LOGS_EXPORT_INTERVAL`. Setup kept a backup of the file before it changed
   anything: `~/.claude/settings.json.keeptabs-<date>.bak`. The undo restores any
   value setup replaced (for example, a company OTel endpoint).
2. Stop the collector:

   ```bash
   python3 ~/.claude/plugins/marketplaces/supertab/plugins/keeptabs/scripts/home.py stop-collector
   ```

   (Or `pkill -f keeptabs/collector.py`. It is not registered anywhere, so it stays
   stopped.)
3. `claude plugin uninstall keeptabs@supertab`, and optionally
   `claude plugin marketplace remove supertab`.
4. Restart Claude Code.
5. Your data stays in `~/.claude/keeptabs` (ledger, raw events, guard state, your
   `budget.json`). Delete that folder if you want it gone.

## How the POC is packaged

`keeptabs/` holds the POC (`guard.py`, `collector.py`, `keeptabs.py`, `health.py`,
`budget.json`, `prices.json`, `README.md`) and runs **in place**, from the plugin folder.
Its `setup.py` (replaced by the plugin) and `gate/` are left out.

**Code and data are split.** The code is in the plugin; the data stays in
`~/.claude/keeptabs`, where the guard's messages point:
- `budget.json`: put there once if missing, then it is yours; updates never touch it.
- `ledger/`, `raw/`, `state/`: written by the POC code.
- `keeptabs.py`, `health.py`: two small launchers the plugin keeps pointing at the
  installed version, so `python3 ~/.claude/keeptabs/keeptabs.py` (live view) and
  `health.py` work as the POC README describes, and the guard still never blocks them.

The data lives there rather than in `${CLAUDE_PLUGIN_DATA}` because it must survive
uninstalling the plugin, and because the live view runs outside Claude Code, where that
variable is not set.

**The patch to the POC.** The POC originally expected its code and data in one folder.
Four files are changed to split them, about a dozen lines in all, each marked
`# plugin: data in CFG, shipped files next to this script`:
- `collector.py`, `health.py`: ledger, raw events and heartbeat go to
  `~/.claude/keeptabs` instead of the script's folder.
- `keeptabs.py`: reads the ledger and guard state from `~/.claude/keeptabs`, and
  `prices.json` from its own folder.
- `guard.py`: reads `prices.json` and imports `health` from its own folder.

To see the whole patch (from the parent project):

```bash
diff -ru eric-poc marketplace/plugins/keeptabs/keeptabs -x gate -x setup.py
```

**Plugin parts:**

| Part | What it does |
|---|---|
| `hooks/hooks.json` | `SessionStart` → `session-start.sh`; `UserPromptSubmit` and `PreToolUse` (all tools) → `guard.sh` → `keeptabs/guard.py` |
| `scripts/home.py` | Prepares `~/.claude/keeptabs`, lazy-starts the collector, reports first-run state |
| `scripts/telemetry.py` | Plan / apply / revert of the telemetry env vars (behind `/keeptabs:setup`) |
| `skills/setup` | `/keeptabs:setup`, user-invoked only |
| `skills/status` | `/keeptabs:status`: spend snapshot and health |

**Collector without launchd.** At session start (and before each prompt, so a crash
heals itself), `home.py` checks for a running collector. It asks the collector's
`/health` endpoint, then falls back to `state/collector.pid`. If none is running, or
the running one comes from another plugin version, it starts one detached from the
current version, under a file lock so sessions starting together start only one. Port
4318 itself also guarantees a single instance. The collector keeps running after
Claude Code exits; after a reboot, the first session starts it again. It is not an MCP
server, because that would mean one per session and a port clash.

**One workaround, outside the POC code.** Python's `http.server` looks up the machine's
full hostname before serving anything. On macOS that reverse lookup of `127.0.0.1` can
trigger the Local Network privacy prompt for Python and hang for 20+ seconds until
answered. Telemetry sent meanwhile can be lost, and `health.py` reports the collector
as down. The plugin starts `collector.py` with that lookup stubbed out (`LAUNCH` in
`home.py`); the name is only used for display.

## Notes for the POC author

- `health.py` says "Run setup.py" when telemetry is off. With the plugin that means
  `/keeptabs:setup` (the plugin's session-start message and `/keeptabs:status` say so).
- The `http.server` hostname lookup above: overriding `server_bind` (or setting
  `server_name` without `getfqdn`) in `collector.py` would remove the workaround.
- `setup.py` is not shipped. If its hooks are still in `~/.claude/settings.json` the
  guard runs twice, and its launchd job would keep an outdated collector alive; the
  plugin detects both at session start and says how to remove them.

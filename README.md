# keeptabs marketplace

A private Claude Code plugin marketplace with one plugin, **keeptabs**: Supertab's
proof of concept that shows what Claude Code is spending, stops it at limits you set,
and gives the agent the numbers it needs to stay inside a budget. The POC's own
documentation is in [`plugins/keeptabs/poc/README.md`](plugins/keeptabs/poc/README.md).

macOS, Python 3 (standard library only).

## Install

This repo is private, so Claude Code fetches it with your own git credentials. Either
an SSH key GitHub knows, or the GitHub CLI:

```bash
gh auth login && gh auth setup-git
```

(With HTTPS only, also set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` to skip the SSH attempt.)

Then, in Claude Code:

```
/plugin marketplace add jmcodingde/keeptabs-marketplace
/plugin install keeptabs@keeptabs-marketplace
```

or from a shell: `claude plugin marketplace add jmcodingde/keeptabs-marketplace` and
`claude plugin install keeptabs@keeptabs-marketplace`.

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

No `version` is set, so every commit to `main` is a new version. To pull one:

```bash
claude plugin marketplace update keeptabs-marketplace
```

```bash
claude plugin update keeptabs@keeptabs-marketplace
```

Then start a new session (or `/reload-plugins`). The new code is copied into
`~/.claude/keeptabs` at session start, and the collector restarts itself if
`collector.py` changed.

Automatic updates are off by default for custom marketplaces. To turn them on: `/plugin`
→ Marketplaces → keeptabs-marketplace → Enable auto-update. Claude Code then updates at
startup, and the update applies to the session after that.

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
   python3 ~/.claude/plugins/marketplaces/keeptabs-marketplace/plugins/keeptabs/scripts/home.py stop-collector
   ```

   (Or `pkill -f keeptabs/collector.py`. It is not registered anywhere, so it stays
   stopped.)
3. `claude plugin uninstall keeptabs@keeptabs-marketplace`, and optionally
   `claude plugin marketplace remove keeptabs-marketplace`.
4. Restart Claude Code.
5. Your data stays in `~/.claude/keeptabs` (ledger, raw events, guard state, your
   `budget.json`). Delete that folder if you want it gone.

## How the POC is packaged

`plugins/keeptabs/poc/` is a verbatim copy of the POC (`guard.py`, `collector.py`,
`keeptabs.py`, `health.py`, `budget.json`, `prices.json`, `README.md`). Its `setup.py`
(replaced by the plugin) and `gate/` are left out. **The POC code is not modified.**
To check that the copy is still verbatim (from the parent project):

```bash
for f in guard.py collector.py keeptabs.py health.py budget.json prices.json README.md; do cmp eric-poc/$f marketplace/plugins/keeptabs/poc/$f; done
```

**Why `~/.claude/keeptabs` is still the home.** The POC expects its code and its data
in one folder, `~/.claude/keeptabs`:
- `guard.py` and `keeptabs.py` hardcode that path for config, state and ledger.
- `collector.py`, `health.py` and parts of `keeptabs.py` use the folder their own file
  is in.
- The guard's "never block keeptabs' own view" rule matches
  `keeptabs/keeptabs.py` and `keeptabs/health.py`.
- Every user-facing message says "~/.claude/keeptabs/budget.json".

Patching path resolution (an env var) would touch four files. Symlinks do not work
either: Python resolves a symlinked script's folder when it imports `health`, so
`health.py` would look for the collector's heartbeat inside the plugin cache. So the
plugin **copies** the POC into `~/.claude/keeptabs` (option (a) of the brief) and runs
it from there. The data therefore lives in `~/.claude/keeptabs`, not in
`${CLAUDE_PLUGIN_DATA}`, and survives plugin updates and uninstalls.

Copy rules (`scripts/home.py`, run at every session start and before each prompt):
- **Code and `prices.json`**: copied in; replaced when the plugin updates. A file
  edited by hand (or left by an older manual install) is kept instead, with a
  message naming it. `.plugin-sync.json` records what the plugin put there.
- **`budget.json`**: copied once if missing, then it is yours; updates never touch it.
- **`ledger/`, `raw/`, `state/`**: never touched.

An existing `~/.claude/keeptabs` from a manual install is adopted as is.

**Plugin parts** (`plugins/keeptabs/`):

| Part | What it does |
|---|---|
| `hooks/hooks.json` | `SessionStart` → `session-start.sh`; `UserPromptSubmit` and `PreToolUse` (all tools) → `guard.sh` → the POC's `guard.py` |
| `scripts/home.py` | Copies the POC into place, lazy-starts the collector, reports first-run state |
| `scripts/telemetry.py` | Plan / apply / revert of the telemetry env vars (behind `/keeptabs:setup`) |
| `skills/setup` | `/keeptabs:setup`, user-invoked only |
| `skills/status` | `/keeptabs:status`: spend snapshot and health |

**Collector without launchd.** At session start (and before each prompt, so a crash
heals itself), `home.py` checks for a running collector. It asks the collector's
`/health` endpoint, then falls back to `state/collector.pid`. If none is running, it
starts one detached, under a file lock so sessions starting together start only one.
Port 4318 itself also guarantees a single instance. The collector keeps running after
Claude Code exits; after a reboot, the first session starts it again. It is not an MCP
server, because that would mean one per session and a port clash.

**One workaround, outside the POC code.** Python's `http.server` looks up the machine's
full hostname before serving anything. On macOS that reverse lookup of `127.0.0.1` can
trigger the Local Network privacy prompt for Python and hang for 20+ seconds until
answered. Telemetry sent meanwhile can be lost, and `health.py` reports the collector
as down. The plugin starts `collector.py` with that lookup stubbed out (`LAUNCH` in
`home.py`); the name is only used for display. Upstream, a one-line fix in
`collector.py` would make the stub unnecessary.

## Notes for the POC author

- `health.py` says "Run setup.py" when telemetry is off. With the plugin that means
  `/keeptabs:setup` (the plugin's session-start message and `/keeptabs:status` say so).
  Suggest making the text neutral upstream.
- The `http.server` hostname lookup above: overriding `server_bind` (or setting
  `server_name` without `getfqdn`) in `collector.py` would remove the workaround.
- `setup.py` is not shipped. If someone still has its hooks in `~/.claude/settings.json`,
  the guard would run twice; the plugin detects that at session start and says how to
  remove them. A leftover launchd collector is harmless: it is the same collector in
  the same folder, and the plugin uses it.

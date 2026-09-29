#!/usr/bin/env python3
"""Switch Claude Code's telemetry to the local keeptabs collector, or back.

  telemetry.py plan     show what apply would change (changes nothing)
  telemetry.py apply    add the env vars to ~/.claude/settings.json, and turn on
                        auto-update for the supertab marketplace
  telemetry.py revert   remove them again, restoring any values apply replaced

A plugin cannot set these itself: Claude Code only accepts OTel settings from
the user's settings.json, the shell or managed settings, and reads them when it
starts. So this edits the "env" block of ~/.claude/settings.json, the same
change the POC's setup.py makes, with the same backup first. Hooks and the
collector are the plugin's job and are not written to settings.json.
"""
import json, os, shutil, sys, time

SETTINGS = os.path.expanduser("~/.claude/settings.json")
DATA = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.claude/plugins/data/keeptabs-supertab")
RECORD = os.path.join(DATA, ".telemetry-setup.json")
ENV = {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_LOGS_EXPORTER": "otlp",
       "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
       "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318",
       "OTEL_LOGS_EXPORT_INTERVAL": "5000"}
MARKETPLACE = "supertab"


def autoupdate_off(s):
    """True if the supertab marketplace is registered in settings without autoUpdate."""
    m = (s.get("extraKnownMarketplaces") or {}).get(MARKETPLACE)
    return isinstance(m, dict) and m.get("autoUpdate") is not True


def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_settings(s):
    if os.path.exists(SETTINGS):
        stamp, n = time.strftime('%Y%m%d-%H%M%S'), 1
        bak = f"{SETTINGS}.keeptabs-{stamp}.bak"
        while os.path.exists(bak):
            n += 1
            bak = f"{SETTINGS}.keeptabs-{stamp}-{n}.bak"
        shutil.copy2(SETTINGS, bak)
        print(f"backed up settings to {bak}")
    with open(SETTINGS, "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")


def plan():
    s = load_settings()
    env = s.get("env") or {}
    todo = {k: v for k, v in ENV.items() if env.get(k) != v}
    if autoupdate_off(s):
        print(f"apply would set \"autoUpdate\": true on the {MARKETPLACE!r} marketplace in "
              "extraKnownMarketplaces, so plugin updates arrive on their own. Turn it off "
              "again in /plugin > Marketplaces.\n")
    if not todo:
        print("Already set up: all keeptabs telemetry settings are in ~/.claude/settings.json.")
        return 0
    print("apply would set these in the \"env\" block of ~/.claude/settings.json:")
    for k, v in todo.items():
        was = f"   (replaces {env[k]!r})" if k in env else ""
        print(f"  {k}={v}{was}")
    if any(k in env for k in todo):
        print("\nWARNING: some of these are already set to other values, probably for another "
              "telemetry setup. apply replaces them for every Claude Code session; revert "
              "puts them back.")
    print("""
What this means:
  - It applies to every Claude Code session on this machine, not just this project.
  - Claude Code will send usage events (model, token counts, cost, duration, session
    id, account email) to the keeptabs collector on 127.0.0.1:4318. Nothing leaves
    this machine.
  - The collector keeps one ledger line per API call in ledger/
    and every event as received, minus prompt text, in raw/, both in the plugin's
    data folder (~/.claude/plugins/data/keeptabs-supertab/), deleted on uninstall.
    Prompt text is never stored.
  - Only sessions started after the change send telemetry: restart Claude Code.""")
    return 0


def apply():
    s = load_settings()
    env = s.setdefault("env", {})
    au = autoupdate_off(s)
    if au:
        s["extraKnownMarketplaces"][MARKETPLACE]["autoUpdate"] = True
    if not au and all(env.get(k) == v for k, v in ENV.items()):
        print("Already set up; nothing changed.")
        return 0
    try:
        with open(RECORD) as f:
            record = json.load(f)
    except (OSError, ValueError):
        record = {"replaced": {}}
    for k, v in ENV.items():
        if k in env and env[k] != v and k not in record["replaced"]:
            record["replaced"][k] = env[k]
    env.update(ENV)
    save_settings(s)
    os.makedirs(os.path.dirname(RECORD), exist_ok=True)
    with open(RECORD, "w") as f:
        json.dump(record, f, indent=2)
    print("telemetry settings added to ~/.claude/settings.json"
          + (f", and auto-update turned on for the {MARKETPLACE!r} marketplace." if au else "."))
    print("Restart Claude Code: only sessions started from now on send telemetry.")
    return 0


def revert():
    s = load_settings()
    env = s.get("env") or {}
    try:
        with open(RECORD) as f:
            replaced = json.load(f).get("replaced") or {}
    except (OSError, ValueError):
        replaced = {}
    touched = False
    for k, v in ENV.items():
        if k in replaced:
            env[k] = replaced[k]
            touched = True
        elif env.get(k) == v:
            del env[k]
            touched = True
    if not touched:
        print("No keeptabs telemetry settings found; nothing changed.")
        return 0
    if env:
        s["env"] = env
    else:
        s.pop("env", None)
    save_settings(s)
    if os.path.exists(RECORD):
        os.remove(RECORD)
    print("keeptabs telemetry settings removed from ~/.claude/settings.json"
          + (f" (restored {', '.join(replaced)})" if replaced else "") + ".")
    print("Restart Claude Code for it to take effect.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    sys.exit({"plan": plan, "apply": apply, "revert": revert}.get(cmd, lambda: print(__doc__) or 2)())

#!/usr/bin/env python3
"""Plugin glue. The keeptabs code runs from the plugin (../keeptabs); its data
lives in ~/.claude/keeptabs. This script keeps that folder and the collector
in place.

  home.py session-start   prepare the folder, start the collector, print a SessionStart message
  home.py ensure          the same, silent (used by the guard on each prompt)
  home.py status          print what the plugin sees
  home.py stop-collector  stop a running collector (for uninstall)

In ~/.claude/keeptabs the plugin writes only:
  budget.json             once, if missing; the user's file from then on
  keeptabs.py, health.py  launchers that run the installed plugin version, so
                          "python3 ~/.claude/keeptabs/keeptabs.py" keeps working
ledger/, raw/ and state/ are written by the keeptabs code itself.
"""
import fcntl, hashlib, json, os, signal, subprocess, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = os.path.join(ROOT, "keeptabs")
COLLECTOR = os.path.join(CODE, "collector.py")
HOME = os.path.expanduser("~/.claude/keeptabs")
STATE = os.path.join(HOME, "state")
PIDFILE = os.path.join(STATE, "collector.pid")
LOCK = os.path.join(STATE, "collector.lock")
LOG = os.path.join(STATE, "collector.log")
SETTINGS = os.path.expanduser("~/.claude/settings.json")
LEGACY_PLIST = os.path.expanduser("~/Library/LaunchAgents/co.supertab.keeptabs.collector.plist")
PROBE = "http://127.0.0.1:4318/health"
LAUNCHERS = ("keeptabs.py", "health.py")
TELEMETRY = {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_LOGS_EXPORTER": "otlp",
             "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
             "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}

# http.server looks up the machine's full hostname before it serves anything.
# On macOS that reverse lookup of 127.0.0.1 can trigger the Local Network
# prompt and hang for 20s or more, and telemetry sent meanwhile can time out.
# The name is only used for display, so the lookup is stubbed out.
LAUNCH = ("import runpy, socket, sys; socket.getfqdn = lambda name='': name or 'localhost'; "
          "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_if_changed(path, text):
    try:
        if open(path).read() == text:
            return
    except OSError:
        pass
    tmp = path + ".plugin-tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def launcher(name):
    return (f"# Written by the keeptabs plugin: runs {name} from the installed plugin version.\n"
            f"import runpy, sys\n"
            f"sys.path.insert(0, {CODE!r})\n"
            f"runpy.run_path({os.path.join(CODE, name)!r}, run_name='__main__')\n")


def remove_old_copies():
    """Earlier plugin versions copied the code into the folder. Remove the
    copies that were never edited, as recorded in their manifest."""
    manifest_path = os.path.join(HOME, ".plugin-sync.json")
    manifest = load(manifest_path, None)
    if manifest is None:
        return
    for name, digest in manifest.items():
        path = os.path.join(HOME, name)
        try:
            with open(path, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() == digest and name not in LAUNCHERS:
                    os.remove(path)
        except OSError:
            pass
    os.remove(manifest_path)


def prepare():
    os.makedirs(STATE, exist_ok=True)
    remove_old_copies()
    budget = os.path.join(HOME, "budget.json")
    if not os.path.exists(budget):
        write_if_changed(budget, open(os.path.join(CODE, "budget.json")).read())
    for name in LAUNCHERS:
        write_if_changed(os.path.join(HOME, name), launcher(name))


def probe():
    """The running collector's pid, or None. Any keeptabs collector counts,
    including one an older manual install started under launchd."""
    try:
        with urllib.request.urlopen(PROBE, timeout=0.5) as r:
            return int(json.load(r).get("pid"))
    except Exception:
        pass
    # Started but not answering yet: trust our pidfile if that process is a collector.
    try:
        pid = int(open(PIDFILE).read().strip())
        return pid if "keeptabs/collector.py" in command(pid) else None
    except (OSError, ValueError):
        return None


def command(pid):
    return subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                          capture_output=True, text=True).stdout


def port_taken():
    import socket
    s = socket.socket()
    try:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", 4318)) == 0
    finally:
        s.close()


def stop(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        if not port_taken():
            return
        time.sleep(0.1)


def ensure_collector():
    """Start the collector if it is not running, or restart it when it runs
    code other than this plugin version's (after an update). Returns a problem
    or None. One instance per machine: a lock serialises sessions starting
    together, and the collector itself cannot start twice because it binds
    port 4318."""
    os.makedirs(STATE, exist_ok=True)
    with open(LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        pid = probe()
        if pid and COLLECTOR not in command(pid):
            stop(pid)
            pid = None
        if pid:
            return None
        if port_taken():
            return ("port 4318 is in use by another program, so the collector cannot start. "
                    "Telemetry will not be recorded until it is free.")
        with open(LOG, "a") as log:
            p = subprocess.Popen([sys.executable, "-c", LAUNCH, COLLECTOR],
                                 cwd=HOME, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True)
        for _ in range(30):
            if p.poll() is not None:
                break
            if port_taken():
                with open(PIDFILE, "w") as f:
                    f.write(f"{p.pid}\n")
                return None
            time.sleep(0.1)
        return f"the collector did not start (see {LOG})."


def legacy_problems():
    """Wiring left by the POC's own setup.py that would clash with the plugin."""
    out = []
    hooks = (load(SETTINGS, {}) or {}).get("hooks") or {}
    if any("keeptabs/guard.py" in str(h.get("command"))
           for groups in hooks.values() for g in groups or [] for h in g.get("hooks") or []):
        out.append("~/.claude/settings.json still has guard hooks from the old setup.py, so the "
                   "guard runs twice. Remove those hook entries, then run /keeptabs:setup again.")
    if os.path.exists(LEGACY_PLIST):
        out.append("the old setup.py's launchd job is still registered and restarts an outdated "
                   "collector. Remove it: launchctl bootout gui/$(id -u)/co.supertab.keeptabs.collector "
                   f"and delete {LEGACY_PLIST}.")
    return out


def telemetry_state():
    """"off", "restart" (set in settings, not in this process yet) or "on"."""
    env = (load(SETTINGS, {}) or {}).get("env") or {}
    if any(str(env.get(k)) != v for k, v in TELEMETRY.items()):
        return "off"
    if any(os.environ.get(k) != v for k, v in TELEMETRY.items()):
        return "restart"
    return "on"


def run(quiet):
    try:
        prepare()
    except OSError as e:
        return [f"keeptabs: could not set up {HOME}: {e}"]
    msgs = []
    problem = ensure_collector()
    if problem:
        msgs.append(f"keeptabs: {problem}")
    if quiet:
        return msgs
    msgs += [f"keeptabs: {p}" for p in legacy_problems()]
    t = telemetry_state()
    if t == "off":
        msgs.append("keeptabs: tracking from transcripts only, so figures are INCOMPLETE (they "
                    "miss background calls and web searches). Run /keeptabs:setup and restart "
                    "Claude Code for full tracking.")
    elif t == "restart":
        msgs.append("keeptabs: telemetry is set up but this session started without it. "
                    "Restart Claude Code for full tracking.")
    return msgs


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "session-start":
        try:
            json.load(sys.stdin)
        except Exception:
            pass
        msgs = run(quiet=False)
        if msgs:
            print(json.dumps({"systemMessage": "\n".join(msgs)}))
        return 0
    if cmd == "ensure":
        run(quiet=True)
        return 0
    if cmd == "stop-collector":
        pid = probe()
        if pid:
            stop(pid)
            print(f"stopped collector (pid {pid})")
        else:
            print("collector is not running")
        return 0
    if cmd == "status":
        print(f"data:      {HOME}")
        print(f"code:      {CODE}")
        pid = probe()
        print(f"collector: {'running, pid ' + str(pid) if pid else 'not running'}"
              + (f" ({command(pid).strip()[-60:]})" if pid else ""))
        print(f"telemetry: {telemetry_state()}")
        for p in legacy_problems():
            print(f"problem:   {p}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Plugin glue: keeps ~/.claude/keeptabs in step with the plugin and keeps the
collector running. The POC code in ../poc is used unchanged; it expects to live
in ~/.claude/keeptabs next to its data, so its files are copied there.

  home.py session-start   sync + start the collector, print a SessionStart message
  home.py ensure          sync + start the collector, silent (used by the guard)
  home.py status          print what the plugin sees
  home.py stop-collector  stop a running collector (for uninstall)

Copy rules (see README "How the POC is packaged"):
  code and prices.json  copied in; replaced on update unless edited locally
  budget.json           copied once; it is the user's file from then on
  data                  ledger/, raw/, state/ are never touched
"""
import fcntl, hashlib, json, os, shutil, signal, subprocess, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POC = os.path.join(ROOT, "poc")
HOME = os.path.expanduser("~/.claude/keeptabs")
STATE = os.path.join(HOME, "state")
MANIFEST = os.path.join(HOME, ".plugin-sync.json")
PIDFILE = os.path.join(STATE, "collector.pid")
LOCK = os.path.join(STATE, "collector.lock")
LOG = os.path.join(STATE, "collector.log")
SETTINGS = os.path.expanduser("~/.claude/settings.json")
LEGACY_PLIST = os.path.expanduser("~/Library/LaunchAgents/co.supertab.keeptabs.collector.plist")
PROBE = "http://127.0.0.1:4318/health"
MANAGED = ("guard.py", "collector.py", "keeptabs.py", "health.py", "prices.json", "README.md")
SEEDED = ("budget.json",)
TELEMETRY = {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_LOGS_EXPORTER": "otlp",
             "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
             "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}


def sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def install(src, dest):
    tmp = dest + ".plugin-tmp"
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


def sync():
    """Returns (changed file names, warnings)."""
    os.makedirs(STATE, exist_ok=True)
    manifest = load(MANIFEST, {})
    changed, warnings = [], []
    for name in MANAGED:
        src, dest = os.path.join(POC, name), os.path.join(HOME, name)
        want, have = sha(src), sha(dest)
        if want is None or want == have:
            if want:
                manifest[name] = want
            continue
        # Replace only what the plugin put there itself. A file edited by hand,
        # or left by an older manual install, is kept and reported.
        if have is None or manifest.get(name) == have:
            install(src, dest)
            manifest[name] = want
            changed.append(name)
        else:
            warnings.append(f"{name} has local changes, so the plugin's version was not applied "
                            f"(its copy is at {src}).")
    for name in SEEDED:
        dest = os.path.join(HOME, name)
        if not os.path.exists(dest):
            install(os.path.join(POC, name), dest)
            changed.append(name)
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp, MANIFEST)
    return changed, warnings


# http.server looks up the machine's full hostname before it serves anything.
# On macOS that reverse lookup of 127.0.0.1 can take 20s or more, and telemetry
# sent meanwhile can time out. The name is only used for display, so the
# collector is started with the lookup stubbed out; collector.py runs unchanged.
LAUNCH = ("import runpy, socket, sys; socket.getfqdn = lambda name='': name or 'localhost'; "
          "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")


def probe():
    """The running collector's pid, or None. Any keeptabs collector counts,
    including one an older manual install started under launchd."""
    try:
        with urllib.request.urlopen(PROBE, timeout=0.5) as r:
            return int(json.load(r).get("pid"))
    except Exception:
        pass
    # Started but not answering yet (a collector launched without the stub
    # can take a while): trust our pidfile if that process is a collector.
    try:
        pid = int(open(PIDFILE).read().strip())
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True).stdout
        return pid if "keeptabs/collector.py" in cmd else None
    except (OSError, ValueError):
        return None


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


def ensure_collector(restart=False):
    """Start the collector if it is not running. Returns a problem or None.
    One instance per machine: a lock serialises sessions starting together,
    and the collector itself cannot start twice because it binds port 4318."""
    os.makedirs(STATE, exist_ok=True)
    with open(LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        pid = probe()
        if pid and restart:
            stop(pid)
            pid = None
        if pid:
            return None
        if port_taken():
            return ("port 4318 is in use by another program, so the collector cannot start. "
                    "Telemetry will not be recorded until it is free.")
        with open(LOG, "a") as log:
            p = subprocess.Popen([sys.executable, "-c", LAUNCH, os.path.join(HOME, "collector.py")],
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
                   "guard runs twice. Remove them with: python3 ~/.claude/keeptabs/setup.py "
                   "uninstall, then run /keeptabs:setup again.")
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
    msgs = []
    try:
        changed, warnings = sync()
        msgs += [f"keeptabs: {w}" for w in warnings]
    except OSError as e:
        return [f"keeptabs: could not set up {HOME}: {e}"]
    problem = ensure_collector(restart="collector.py" in changed)
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
        if os.path.exists(LEGACY_PLIST):
            print("note: launchd will restart it: the old setup.py job is still registered "
                  "(python3 ~/.claude/keeptabs/setup.py uninstall removes it)")
        return 0
    if cmd == "status":
        print(f"home:      {HOME}")
        print(f"plugin:    {ROOT}")
        pid = probe()
        print(f"collector: {'running, pid ' + str(pid) if pid else 'not running'}")
        print(f"telemetry: {telemetry_state()}")
        for p in legacy_problems():
            print(f"problem:   {p}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

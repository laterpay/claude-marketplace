#!/usr/bin/env python3
"""
health.py: is the collector actually receiving Claude Code telemetry?

Three checks, each with a plain reason when it fails:
  1. settings: telemetry is switched on in ~/.claude/settings.json
  2. alive:    the collector's heartbeat is fresh
  3. flowing:  Claude has replied since the collector last heard anything
               (compares the newest transcript reply with the last event)

Run directly for a report:  python3 health.py
"""
import glob, json, os, sys, urllib.request
from datetime import datetime, timezone

CFG = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.claude/plugins/data/keeptabs-supertab")  # plugin: data in the plugin's data folder, shipped files next to this script
HEART = os.path.join(CFG, "state", "collector.json")
SETTINGS = os.path.expanduser("~/.claude/settings.json")
PROJECTS = os.path.expanduser("~/.claude/projects")
NEED = {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
PROBE = "http://127.0.0.1:4318/health"
STALE_HEARTBEAT = 60      # seconds
FLOW_SLACK = 120          # a reply this much newer than the last event is a gap


def parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def answering():
    """Pid of a collector answering on the port, or None. A collector can
    answer and still write no heartbeat: its data folder was deleted under it
    (Claude Code does that on uninstall). Its own heartbeat then goes nowhere,
    and it is the plugin's job (home.py) to replace it at the next prompt."""
    try:
        with urllib.request.urlopen(PROBE, timeout=0.5) as r:
            return int(json.load(r).get("pid"))
    except Exception:
        return None


def stuck(when):
    pid = answering()
    if pid:
        return (f"the collector is stuck: pid {pid} answers on port 4318 but its heartbeat "
                f"stopped ({when}). Send a prompt in any session to replace it.")
    return None


def last_reply(sid=None):
    """Timestamp of the newest assistant line, in one session or the most recent ones."""
    pat = f"{sid}.jsonl" if sid else "*.jsonl"
    files = sorted(glob.glob(os.path.join(PROJECTS, "*", pat)),
                   key=os.path.getmtime, reverse=True)[:3]
    best = None
    for p in files:
        try:
            with open(p, "rb") as f:
                f.seek(max(0, os.path.getsize(p) - 262144))
                tail = f.read().decode("utf-8", "ignore").splitlines()
        except OSError:
            continue
        for line in reversed(tail):
            if '"type":"assistant"' not in line.replace(" ", ""):
                continue
            try:
                ts = parse(json.loads(line).get("timestamp"))
            except ValueError:
                continue
            if ts and (best is None or ts > best):
                best = ts
            break
    return best


def last_ledger(sid):
    """Timestamp of the newest ledger line for a session, or None."""
    best = None
    for p in sorted(glob.glob(os.path.join(CFG, "ledger", "*.jsonl")))[-2:]:
        for line in open(p):
            if sid not in line:
                continue
            try:
                ts = parse(json.loads(line).get("ts"))
            except ValueError:
                continue
            if ts and (best is None or ts > best):
                best = ts
    return best


def check(now=None, sid=None):
    """Returns (ok, problem). problem is None when ok. With sid, the flow
    check is about that session only, so an old session that predates setup
    does not make a new one look broken."""
    now = now or datetime.now(timezone.utc)
    try:
        env = (json.load(open(SETTINGS)).get("env") or {})
    except (OSError, ValueError):
        env = {}
    missing = [k for k, v in NEED.items() if str(env.get(k)) != v]
    if missing:
        return False, ("telemetry is not switched on in ~/.claude/settings.json "
                       f"(missing {', '.join(missing)}). Run /keeptabs:setup.")
    try:
        hb = json.load(open(HEART))
    except (OSError, ValueError):
        return False, (stuck("no heartbeat file")
                       or "the collector has never run. Start a new Claude Code session to start it.")
    alive = parse(hb.get("alive_at"))
    if not alive or (now - alive).total_seconds() > STALE_HEARTBEAT:
        when = alive.astimezone().strftime("%H:%M") if alive else "unknown"
        return False, (stuck(f"last heartbeat {when}")
                       or f"the collector is not running (last heartbeat {when}).")
    if sid:
        seen = last_ledger(sid)
        reply = last_reply(sid)
        if seen is None:
            # Nothing can have arrived before the session's first reply, and the
            # exporter batches for a few seconds after it. Only a reply that is
            # well past that with still no ledger line means the session does
            # not send telemetry. The hook's environment cannot tell: Claude
            # Code does not pass the OTel settings on to hooks or child processes.
            if reply is None or (now - reply).total_seconds() < FLOW_SLACK:
                return True, None
            return False, (f"this session sends no telemetry (it replied at "
                           f"{reply.astimezone().strftime('%H:%M')}, nothing arrived). Sessions "
                           "started before setup do not: restart Claude Code.")
        if reply and (reply - seen).total_seconds() > FLOW_SLACK:
            return False, (f"this session's telemetry stopped arriving (last at "
                           f"{seen.astimezone().strftime('%H:%M')}).")
        return True, None
    last_ev = parse(hb.get("last_event_at"))
    started = parse(hb.get("started_at"))
    reply = last_reply()
    if started and (now - started).total_seconds() < FLOW_SLACK:
        return True, None   # just (re)started: a gap before its first event is expected
    if reply and (last_ev is None or (reply - last_ev).total_seconds() > FLOW_SLACK):
        when = last_ev.astimezone().strftime("%H:%M") if last_ev else "never"
        return False, (f"the collector is running but not receiving telemetry (last event "
                       f"{when}). Sessions started before setup do not send it: restart them.")
    return True, None


if __name__ == "__main__":
    ok, why = check(sid=sys.argv[1] if len(sys.argv) > 1 else None)
    print("collector: OK" if ok else f"collector: PROBLEM: {why}")
    try:
        print(json.dumps(json.load(open(HEART)), indent=2))
    except (OSError, ValueError):
        pass
    sys.exit(0 if ok else 1)

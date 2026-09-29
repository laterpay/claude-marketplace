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
import glob, json, os, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
HEART = os.path.join(HERE, "state", "collector.json")
SETTINGS = os.path.expanduser("~/.claude/settings.json")
PROJECTS = os.path.expanduser("~/.claude/projects")
NEED = {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
STALE_HEARTBEAT = 60      # seconds
FLOW_SLACK = 120          # a reply this much newer than the last event is a gap


def parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
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
    for p in sorted(glob.glob(os.path.join(HERE, "ledger", "*.jsonl")))[-2:]:
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
                       f"(missing {', '.join(missing)}). Run setup.py.")
    try:
        hb = json.load(open(HEART))
    except (OSError, ValueError):
        return False, "the collector has never run. Run setup.py, or start collector.py."
    alive = parse(hb.get("alive_at"))
    if not alive or (now - alive).total_seconds() > STALE_HEARTBEAT:
        when = alive.astimezone().strftime("%H:%M") if alive else "unknown"
        return False, f"the collector is not running (last heartbeat {when})."
    if sid:
        seen = last_ledger(sid)
        if seen is None:
            return False, ("this session sends no telemetry. Sessions started before setup "
                           "do not: start a new one.")
        reply = last_reply(sid)
        if reply and (reply - seen).total_seconds() > FLOW_SLACK:
            return False, (f"this session's telemetry stopped arriving (last at "
                           f"{seen.astimezone().strftime('%H:%M')}).")
        return True, None
    last_ev = parse(hb.get("last_event_at"))
    reply = last_reply()
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

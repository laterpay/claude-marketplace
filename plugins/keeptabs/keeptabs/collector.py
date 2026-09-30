#!/usr/bin/env python3
"""
collector.py: receives Claude Code telemetry (OTLP over HTTP, JSON) and writes
one ledger line per API call. Standard library only.

Claude Code sends it when these are set (setup.py adds them to settings.json):
  CLAUDE_CODE_ENABLE_TELEMETRY=1
  OTEL_LOGS_EXPORTER=otlp
  OTEL_EXPORTER_OTLP_PROTOCOL=http/json
  OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318

Files (all in the plugin's data folder, ~/.claude/plugins/data/keeptabs-supertab):
  ledger/YYYY-MM-DD.jsonl   one line per API call, the record keeptabs trusts
  raw/YYYY-MM-DD.jsonl      every event as received, for checking field names
  state/collector.json      heartbeat, read by health.py

Telemetry covers calls that never reach the transcripts: background Haiku
calls, web search, subagents. Prompts are not logged unless the user sets
OTEL_LOG_USER_PROMPTS=1, and setup.py does not.
"""
import json, os, sys, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.claude/plugins/data/keeptabs-supertab")  # plugin: data in the plugin's data folder, shipped files next to this script
LEDGER = os.path.join(CFG, "ledger")
RAW = os.path.join(CFG, "raw")
HEART = os.path.join(CFG, "state", "collector.json")
PRICES = os.path.join(HERE, "prices.json")
LISTEN = ("127.0.0.1", 4318)

lock = threading.Lock()
# "data" lets the plugin tell a collector writing elsewhere from its own.
stats = {"pid": os.getpid(), "data": CFG, "started_at": None, "alive_at": None,
         "last_event_at": None, "last_api_request_at": None,
         "events": 0, "api_requests": 0, "errors": 0, "last_error": None}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def val(v):
    """Unwrap an OTLP AnyValue."""
    for k in ("stringValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        return int(v["intValue"])
    if "arrayValue" in v:
        return [val(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return attrs(v["kvlistValue"].get("values", []))
    return None


def attrs(lst):
    return {a["key"]: val(a.get("value") or {}) for a in lst or []}


def num(a, *keys):
    for k in keys:
        if a.get(k) is not None:
            try:
                return float(a[k])
            except (TypeError, ValueError):
                pass
    return 0.0


def rate(prices, model, key):
    m = prices.get("models", {}).get(model) or prices.get("default", {})
    return float(m.get(key, 0.0))


def append(folder, day, obj):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{day}.jsonl"), "a") as f:
        f.write(json.dumps(obj) + "\n")


def record(ev):
    """Turn one api_request event into a ledger line."""
    a = ev["attrs"]
    model = a.get("model") or "unknown"
    toks = {"input": int(num(a, "input_tokens")),
            "output": int(num(a, "output_tokens")),
            "cache_read": int(num(a, "cache_read_tokens", "cache_read_input_tokens")),
            "cache_write": int(num(a, "cache_creation_tokens", "cache_creation_input_tokens"))}
    try:
        with open(PRICES) as f:
            prices = json.load(f)
    except (OSError, ValueError):
        prices = {}
    # Telemetry does not say whether a cache write was 5m or 1h. Claude Code
    # uses 1h by default, so that rate is applied; cost_usd_cc is Claude
    # Code's own figure for comparison.
    ours = (toks["input"] * rate(prices, model, "input")
            + toks["output"] * rate(prices, model, "output")
            + toks["cache_read"] * rate(prices, model, "cache_read")
            + toks["cache_write"] * rate(prices, model, "cache_write_1h")) / 1_000_000.0
    return {"ts": ev["ts"], "source": "otel", "session_id": a.get("session.id"),
            "user": a.get("user.email") or a.get("user.account_uuid"),
            "model": model, "tokens": toks,
            "cost_usd": round(ours, 6),
            "cost_usd_cc": num(a, "cost_usd") if a.get("cost_usd") is not None else None,
            # Non-token charges (web search is billed per search): whatever Claude
            # Code's figure has on top of the token price. cost_usd stays tokens only.
            "fees_usd": (round(max(0.0, num(a, "cost_usd") - ours), 6)
                         if a.get("cost_usd") is not None and num(a, "cost_usd") - ours > 0.0005 else 0.0),
            "duration_ms": int(num(a, "duration_ms")),
            # which part of Claude Code made the call (main loop, subagent,
            # background title/summary calls): this is how background use shows up
            "query_source": a.get("query_source"), "request_id": a.get("request_id"), "client_request_id": a.get("client_request_id"),
            "prices_verified": bool(prices.get("_verified"))}


def events(payload):
    """Flatten an OTLP logs payload into {name, ts, attrs} dicts."""
    for rl in payload.get("resourceLogs", []):
        res = attrs((rl.get("resource") or {}).get("attributes"))
        for sl in rl.get("scopeLogs", []):
            for lr in sl.get("logRecords", []):
                a = dict(res)
                a.update(attrs(lr.get("attributes")))
                body = val(lr.get("body") or {})
                name = a.get("event.name") or (body if isinstance(body, str) else "")
                ns = int(lr.get("timeUnixNano") or lr.get("observedTimeUnixNano") or 0)
                ts = (datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat(timespec="seconds")
                      if ns else now_iso())
                yield {"name": name, "ts": ts, "attrs": a}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ok(self):
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_GET(self):
        # Health probe: curl http://127.0.0.1:4318/health
        body = json.dumps(stats).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        data = self.rfile.read(n) if n else b""
        day = datetime.now().strftime("%Y-%m-%d")
        try:
            if self.path.startswith("/v1/logs"):
                payload = json.loads(data or b"{}")
                with lock:
                    for ev in events(payload):
                        ev["attrs"].pop("prompt", None)   # never store prompt text
                        append(RAW, day, ev)
                        stats["events"] += 1
                        stats["last_event_at"] = now_iso()
                        if ev["name"].endswith("api_request"):
                            append(LEDGER, day, record(ev))
                            stats["api_requests"] += 1
                            stats["last_api_request_at"] = now_iso()
            elif self.path.startswith("/v1/metrics"):
                with lock:
                    stats["last_event_at"] = now_iso()   # metrics are not ledgered
        except Exception as e:  # never make Claude Code retry forever
            with lock:
                stats["errors"] += 1
                stats["last_error"] = f"{now_iso()} {type(e).__name__}: {e}"
        self._ok()


def heartbeat():
    """Write the heartbeat every 15s. The plugin's data folder can disappear
    under a running collector (Claude Code deletes it on uninstall). If only
    state/ is missing it is re-created. If the data folder itself was deleted,
    or deleted and re-created (a reinstall; same path, new inode), the
    collector exits: it belongs to a plugin that is no longer installed, and
    the next session starts a fresh one. The inode is compared because the
    collector's own writes, and this one, would otherwise re-create the folder
    and hide the deletion."""
    os.makedirs(CFG, exist_ok=True)
    home = os.stat(CFG).st_ino
    while True:
        try:
            if not os.path.isdir(CFG) or os.stat(CFG).st_ino != home:
                print(f"collector: data folder {CFG} was deleted, exiting", file=sys.stderr)
                os._exit(0)
            os.makedirs(os.path.dirname(HEART), exist_ok=True)
            with lock:
                tmp = HEART + ".tmp"
                stats["alive_at"] = now_iso()
                with open(tmp, "w") as f:
                    json.dump(stats, f)
                os.replace(tmp, HEART)
        except OSError as e:
            print(f"collector: heartbeat failed: {e}", file=sys.stderr)
        time.sleep(15)


def main():
    stats["started_at"] = now_iso()
    try:   # keep the last-seen times across restarts, or health reports a false gap
        old = json.load(open(HEART))
        for k in ("last_event_at", "last_api_request_at"):
            stats[k] = old.get(k)
    except (OSError, ValueError):
        pass
    try:
        srv = ThreadingHTTPServer(LISTEN, H)
    except OSError as e:
        print(f"collector: cannot listen on {LISTEN[0]}:{LISTEN[1]}: {e}", file=sys.stderr)
        return 1
    threading.Thread(target=heartbeat, daemon=True).start()
    print(f"collector listening on http://{LISTEN[0]}:{LISTEN[1]}", file=sys.stderr)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())

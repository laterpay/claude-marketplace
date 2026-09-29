#!/usr/bin/env python3
"""keeptabs - live token and cost monitor for Claude Code sessions.

Reads the JSONL transcripts Claude Code writes under ~/.claude/projects and
shows cumulative usage and cost as it happens. No dependencies.

  keeptabs.py                 live view of the most recently active session
  keeptabs.py --session ID    pin to one session
  keeptabs.py --once          print one snapshot and exit
  keeptabs.py --json          machine-readable snapshot (for hooks)
  keeptabs.py --all           live view across every project
"""
import argparse, bisect, glob, json, os, sys, time
from datetime import datetime, timezone
HERE = os.path.dirname(os.path.abspath(__file__))

ROOT = os.path.expanduser("~/.claude/projects")
CFG  = os.path.expanduser("~/.claude/keeptabs")
PRICES_PATH = os.path.join(HERE, "prices.json")  # plugin: data in CFG, shipped files next to this script
BUDGET_PATH = os.path.join(CFG, "budget.json")

# ---------- pricing ----------

def load_prices():
    with open(PRICES_PATH) as fh:
        return json.load(fh)

def rate(prices, model, key):
    m = prices.get("models", {}).get(model)
    if m is None:
        m = prices.get("default", {})
    return float(m.get(key, 0.0))

def weighted(prices, model, i, w5, w1, r, o):
    """Tokens as budget.json counts them, same rule as guard.py: cache reads
    at their price relative to fresh input for that model."""
    ri = rate(prices, model, "input")
    return i + w5 + w1 + o + (int(r * rate(prices, model, "cache_read") / ri) if ri else r)

# ---------- accumulation ----------

class Bucket:
    __slots__ = ("inp","cw5","cw1h","cr","out","think","cost","msgs","models","first","last",
                 "tools","pending","cost_by","by_source","wtok","ctx","ctx_model","ttl","calls")
    def __init__(self):
        self.inp = self.cw5 = self.cw1h = self.cr = self.out = self.think = 0
        self.wtok = 0         # budget tokens, weighted like guard.py (see weighted())
        self.ctx, self.ctx_model, self.ttl = 0, None, "1h"   # the latest call
        self.cost = 0.0
        self.msgs = 0
        self.models = {}
        self.first = None
        self.last = None
        self.tools = {}       # name -> {calls, ask, result, rtokens}
        self.pending = []     # tools invoked by the previous message
        self.cost_by = {}     # rate key -> USD, priced per model (mixed models are right)
        self.by_source = {}   # ledger only: query_source -> [calls, USD]
        self.calls = []       # (epoch, USD) per call, for request totals

    def _tool(self, name):
        return self.tools.setdefault(name, {"calls": 0, "ask": 0.0, "result": 0.0, "rtokens": 0})

    def add(self, u, model, prices, ts, content=None, dup=False, source=None):
        # dup: a later log line for a request already counted. u then holds
        # only the usage delta, and the message is not counted twice.
        cc = u.get("cache_creation") or {}
        w1 = int(cc.get("ephemeral_1h_input_tokens") or 0)
        w5 = int(cc.get("ephemeral_5m_input_tokens") or 0)
        if not (w1 or w5):
            w5 = int(u.get("cache_creation_input_tokens") or 0)
        i  = int(u.get("input_tokens") or 0)
        r  = int(u.get("cache_read_input_tokens") or 0)
        o  = int(u.get("output_tokens") or 0)
        th = int(((u.get("output_tokens_details") or {}).get("thinking_tokens")) or 0)

        self.inp += i; self.cw5 += w5; self.cw1h += w1; self.cr += r
        self.out += o; self.think += th; self.msgs += 0 if dup else 1
        self.wtok += weighted(prices, model, i, w5, w1, r, o)
        before = self.cost
        for key, n in (("input", i), ("cache_write_5m", w5), ("cache_write_1h", w1),
                       ("cache_read", r), ("output", o)):
            self.cost_by[key] = self.cost_by.get(key, 0.0) + n * rate(prices, model, key) / 1_000_000.0
        self.cost += (i  * rate(prices, model, "input")
                    + w5 * rate(prices, model, "cache_write_5m")
                    + w1 * rate(prices, model, "cache_write_1h")
                    + r  * rate(prices, model, "cache_read")
                    + o  * rate(prices, model, "output")) / 1_000_000.0
        if source is not None:
            e = self.by_source.setdefault(source, [0, 0.0])
            e[0] += 1; e[1] += self.cost - before
        if not dup:
            self.models[model] = self.models.get(model, 0) + 1

        # Attribution. Asking for a tool costs the output tokens that emit the
        # call. The answer costs whatever it adds to context, which shows up as
        # the cache write on the NEXT message. That second part is usually the
        # larger of the two and is the one people are surprised by.
        write_cost = (w5 * rate(prices, model, "cache_write_5m")
                    + w1 * rate(prices, model, "cache_write_1h")) / 1_000_000.0
        if not dup and self.pending and (w5 + w1):
            share_c, share_t = write_cost / len(self.pending), (w5 + w1) // len(self.pending)
            for nm in self.pending:
                e = self._tool(nm); e["result"] += share_c; e["rtokens"] += share_t
        if not dup:
            self.pending = []
        if content:
            names = [b.get("name") for b in content
                     if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")]
            if names:
                ask = o * rate(prices, model, "output") / 1_000_000.0 / len(names)
                for nm in names:
                    e = self._tool(nm); e["calls"] += 1; e["ask"] += ask
                self.pending = self.pending + names if dup else names

        if ts:
            if self.first is None or ts < self.first: self.first = ts
            if self.last  is None or ts > self.last:  self.last  = ts

    @property
    def tokens(self):
        return self.inp + self.cw5 + self.cw1h + self.cr + self.out


def usage_delta(prev, cur):
    """Usage in cur beyond prev, never negative."""
    def g(d, k): return int(d.get(k) or 0)
    out = {k: max(0, g(cur, k) - g(prev, k)) for k in
           ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")}
    pc, cc = prev.get("cache_creation") or {}, cur.get("cache_creation") or {}
    out["cache_creation"] = {k: max(0, g(cc, k) - g(pc, k)) for k in
                             ("ephemeral_1h_input_tokens", "ephemeral_5m_input_tokens")}
    pt = (prev.get("output_tokens_details") or {}); ct = (cur.get("output_tokens_details") or {})
    out["output_tokens_details"] = {"thinking_tokens": max(0, g(ct, "thinking_tokens") - g(pt, "thinking_tokens"))}
    return out


class Store:
    """Incrementally tails every transcript and keeps per-session buckets."""
    def __init__(self, prices):
        self.prices = prices
        self.offsets = {}          # path -> byte offset already consumed
        self.sessions = {}         # session_id -> Bucket
        self.proj_of = {}          # session_id -> project slug
        self.seen = {}             # dedupe key -> usage already counted
        self.events = []           # (epoch_seconds, cost) for rate windows
        self.prompts = {}          # session_id -> epochs of the user's prompts

    def scan(self):
        if not os.path.isdir(ROOT):
            return
        for proj in os.listdir(ROOT):
            pdir = os.path.join(ROOT, proj)
            if not os.path.isdir(pdir):
                continue
            # Helpers' transcripts sit under <session>/subagents and carry the
            # parent's sessionId, so they land in the parent's bucket.
            for name in os.listdir(pdir) + [os.path.relpath(x, pdir) for x in
                                            glob.glob(os.path.join(pdir, "*", "subagents", "*.jsonl"))]:
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(pdir, name)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                off = self.offsets.get(path, 0)
                if size < off:            # file was truncated or rotated
                    off = 0
                if size == off:
                    continue
                try:
                    with open(path, "r", errors="replace") as fh:
                        fh.seek(off)
                        chunk = fh.read()
                        self.offsets[path] = fh.tell()
                except OSError:
                    continue
                # a partial trailing line is left for the next pass
                if chunk and not chunk.endswith("\n"):
                    cut = chunk.rfind("\n")
                    if cut == -1:
                        self.offsets[path] = off
                        continue
                    self.offsets[path] = off + len(chunk[:cut+1].encode("utf-8", "replace"))
                    chunk = chunk[:cut+1]
                self._consume(chunk, proj)

    def _consume(self, chunk, proj):
        for line in chunk.splitlines():
            if '"type":"user"' in line:
                self._prompt(line)
                continue
            if '"usage"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") != "assistant":
                continue
            msg = o.get("message") or {}
            u = msg.get("usage")
            if not isinstance(u, dict) or msg.get("model") == "<synthetic>":
                continue                  # placeholder replies are not API calls
            # One request can span several log lines. Count it once, and let
            # later lines add only what grew (usually output), so the final
            # count wins instead of the first partial one.
            key = o.get("requestId") or o.get("uuid")
            full = u
            dup = False
            if key:
                prev = self.seen.get(key)
                self.seen[key] = u
                if prev is not None:
                    dup = True
                    u = usage_delta(prev, u)
            sid = o.get("sessionId") or "unknown"
            ts = parse_ts(o.get("timestamp"))
            b = self.sessions.setdefault(sid, Bucket())
            before, wbefore = b.cost, b.wtok
            b.add(u, msg.get("model") or "unknown", self.prices, ts, msg.get("content"), dup)
            if ts and ts >= b.last:
                cc = full.get("cache_creation") or {}
                w1 = int(cc.get("ephemeral_1h_input_tokens") or 0)
                b.ctx = (int(full.get("input_tokens") or 0) + int(full.get("cache_read_input_tokens") or 0)
                         + int(full.get("cache_creation_input_tokens") or 0))
                b.ctx_model = msg.get("model") or "unknown"
                b.ttl = "1h" if w1 or not full.get("cache_creation_input_tokens") else "5m"
            self.proj_of[sid] = proj
            t = (ts or datetime.now(timezone.utc)).timestamp()
            self.events.append((t, b.cost - before, b.wtok - wbefore))
            b.calls.append((t, b.cost - before))

    def _prompt(self, line):
        """Record when the user sent a prompt. Tool results and meta lines are
        also type "user" in the transcript, so only real text counts."""
        try:
            o = json.loads(line)
        except Exception:
            return
        if o.get("type") != "user" or o.get("isMeta") or o.get("isSidechain"):
            return
        c = (o.get("message") or {}).get("content")
        if isinstance(c, list):
            if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                return
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
        if not isinstance(c, str) or not c.strip() or c.lstrip().startswith("<local-command"):
            return
        ts = parse_ts(o.get("timestamp"))
        if ts:
            self.prompts.setdefault(o.get("sessionId") or "unknown", []).append(ts.timestamp())

    def window_cost(self, seconds):
        cutoff = time.time() - seconds
        return sum(c for t, c, _ in self.events if t >= cutoff)

    def day_total(self):
        return day_total(self.events)

    def newest_session(self):
        best, best_ts = None, None
        for sid, b in self.sessions.items():
            if b.last and (best_ts is None or b.last > best_ts):
                best, best_ts = sid, b.last
        return best


def fee(e):
    """Charges that are not tokens (web search is billed per search). Taken from
    Claude Code's own cost figure, which includes them: older ledger lines have no
    fees_usd, so it is worked out from cost_usd_cc for those."""
    if e.get("fees_usd") is not None:
        return float(e["fees_usd"])
    cc = e.get("cost_usd_cc")
    d = float(cc) - float(e.get("cost_usd") or 0.0) if cc is not None else 0.0
    return d if d > 0.0005 else 0.0

class LedgerStore:
    """Same shape as Store, built from the collector's ledger (telemetry).
    Covers every API call, including background ones that transcripts miss."""
    def __init__(self, prices):
        self.prices = prices
        self.offsets = {}
        self.sessions = {}
        self.events = []

    def scan(self):
        for p in sorted(glob.glob(os.path.join(CFG, "ledger", "*.jsonl"))):
            try:
                with open(p, "rb") as f:
                    f.seek(self.offsets.get(p, 0))
                    chunk = f.read()
            except OSError:
                continue
            end = chunk.rfind(b"\n") + 1          # only whole lines
            self.offsets[p] = self.offsets.get(p, 0) + end
            for line in chunk[:end].splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                t = e.get("tokens") or {}
                u = {"input_tokens": t.get("input"), "output_tokens": t.get("output"),
                     "cache_read_input_tokens": t.get("cache_read"),
                     "cache_creation": {"ephemeral_1h_input_tokens": t.get("cache_write")}}
                ts = parse_ts(e.get("ts"))
                b = self.sessions.setdefault(e.get("session_id") or "unknown", Bucket())
                before, wbefore = b.cost, b.wtok
                src = e.get("query_source") or "unknown"
                b.add(u, e.get("model") or "unknown", self.prices, ts, source=src)
                f = fee(e)
                if f:
                    b.cost += f
                    b.cost_by["fees"] = b.cost_by.get("fees", 0.0) + f
                    b.by_source[src][1] += f
                t = (ts or datetime.now(timezone.utc)).timestamp()
                self.events.append((t, b.cost - before, b.wtok - wbefore))
                b.calls.append((t, b.cost - before))

    def window_cost(self, seconds):
        cut = time.time() - seconds
        return sum(c for t, c, _ in self.events if t >= cut)

    def day_total(self):
        return day_total(self.events)


def requests(calls, prompts):
    """Cost of each request: one prompt and every call until the next prompt.
    That includes the prompt suggestion Claude Code makes after the reply.
    Returns [(start_epoch, USD, calls)], oldest first."""
    starts = sorted(prompts)
    out = [[t, 0.0, 0] for t in starts]
    for t, c in calls:
        i = bisect.bisect_right(starts, t) - 1
        if i >= 0:
            out[i][1] += c
            out[i][2] += 1
    return [tuple(x) for x in out]


def day_total(events):
    """(USD, budget tokens) spent today, local time, across every session."""
    today = datetime.now().astimezone().date()
    c = t = 0
    for ts, dc, dt in events:
        if datetime.fromtimestamp(ts).date() == today:
            c += dc; t += dt
    return c, t


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

# ---------- rendering ----------

def money(x):
    # Telemetry reports cost to the micro-dollar, so show 4 places, not cents.
    return f"${x:,.4f}"

def num(x):
    return f"{x:,}"

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
CYAN, YELLOW, RED, GREEN = "\033[36m", "\033[33m", "\033[31m", "\033[32m"

def bar(frac, width=28):
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    color = GREEN if frac < 0.7 else (YELLOW if frac < 1.0 else RED)
    return color + "█" * filled + DIM + "·" * (width - filled) + RESET

def budget_line(name, c, t, ulim, tlim):
    """One scope: bar at whichever limit is closest, then both figures."""
    parts, fracs = [], []
    if ulim:
        fracs.append(c / float(ulim)); parts.append(f"{money(c)} of ${float(ulim):,.2f}")
    if tlim:
        fracs.append(t / float(tlim)); parts.append(f"{num(t)} of {num(int(tlim))} tok")
    if not fracs:
        return None
    frac = max(fracs)
    over = f"  {RED}{BOLD}OVER{RESET}" if frac >= 1.0 else ""
    return (f"    {name:<9} {bar(frac, 20)} {frac*100:5.0f}%   "
            f"{DIM}{' · '.join(parts)}{RESET}{over}")


def pick(store, led, sid):
    """Which record to trust for this session, and a line saying so."""
    try:
        import health
        ok, why = health.check(sid=sid)
    except Exception as e:
        ok, why = False, f"health check failed: {e}"
    lb = led.sessions.get(sid) if led else None
    if ok and lb:
        return lb, led, f"{GREEN}data: ledger (telemetry, includes background calls){RESET}"
    if lb:
        return lb, led, (f"{YELLOW}data: ledger, but INCOMPLETE: {why}{RESET}")
    why = why or "this session sends no telemetry (it started before setup)."
    return store.sessions.get(sid), store, f"{YELLOW}data: transcripts only, INCOMPLETE: {why}{RESET}"


def render(store, sid, prices, budget, show_all, led=None):
    tb = store.sessions.get(sid)          # transcripts: only source of tool detail
    b, src, srcline = pick(store, led, sid)
    L = []
    warn = "" if prices.get("_verified") else f"  {YELLOW}prices unverified{RESET}"
    L.append(f"{BOLD}keeptabs{RESET} {DIM}· live{RESET}{warn}"
             f"{DIM}{datetime.now().strftime('%H:%M:%S').rjust(max(1, 62 - 22 - len(warn)))}{RESET}")
    L.append("")

    if not b:
        L.append(f"  {DIM}waiting for activity...{RESET}")
        return "\n".join(L)

    models = ", ".join(sorted(b.models))
    started = b.first.astimezone().strftime("%H:%M") if b.first else "?"
    L.append(f"  {BOLD}SESSION{RESET} {DIM}{sid[:8]} · {models} · since {started}{RESET}")
    L.append(f"  {srcline}")
    L.append("")
    rows = [
        ("input",            b.inp,  "input"),
        ("cache write 1h",   b.cw1h, "cache_write_1h"),
        ("cache write 5m",   b.cw5,  "cache_write_5m"),
        ("cache read",       b.cr,   "cache_read"),
        ("output",           b.out,  "output"),
        ("fees (web search)", 0,     "fees"),
    ]
    model = max(b.models, key=b.models.get) if b.models else "unknown"
    for label, toks, key in rows:
        if toks == 0 and (key.startswith("cache_write") or (key == "fees" and not b.cost_by.get("fees"))):
            continue
        c = b.cost_by.get(key, 0.0)
        share = (c / b.cost) if b.cost else 0
        L.append(f"    {label:<16}{num(toks):>14}{money(c):>12}   {DIM}{share*100:4.0f}%{RESET}")
    L.append(f"    {DIM}{'-'*44}{RESET}")
    L.append(f"    {BOLD}{'total':<16}{num(b.tokens):>14}{money(b.cost):>12}{RESET}"
             f"   {DIM}{b.msgs} msgs{RESET}")
    if b.think:
        L.append(f"    {DIM}{'of which thinking':<16}{num(b.think):>14}{RESET}")
    reqs = requests(b.calls, store.prompts.get(sid, []))
    if reqs:
        L.append("")
        L.append(f"  {BOLD}REQUESTS{RESET}  {DIM}one prompt and everything it set off{RESET}")
        for label, r in (("latest", reqs[-1]), ("previous", reqs[-2] if len(reqs) > 1 else None)):
            if r:
                at = datetime.fromtimestamp(r[0]).strftime("%H:%M")
                L.append(f"    {label:<16}{money(r[1]):>26}   {DIM}{r[2]} calls · {at}{RESET}")
        avg = sum(r[1] for r in reqs) / len(reqs)
        L.append(f"    {DIM}{'average':<16}{money(avg):>26}   of {len(reqs)} requests{RESET}")
    L.append("")
    if b.by_source:
        L.append("")
        L.append(f"  {BOLD}BY CALL SOURCE{RESET}  {DIM}what in Claude Code made the call{RESET}")
        for nm, (calls, c) in sorted(b.by_source.items(), key=lambda kv: -kv[1][1])[:8]:
            L.append(f"    {nm[:24]:<24}{calls:>5} {DIM}calls{RESET}{money(c):>11}"
                     f"   {DIM}{(c / b.cost * 100) if b.cost else 0:4.0f}%{RESET}")
    L.append("")
    L.append(f"    {DIM}all sessions · last 5 min{RESET} {money(src.window_cost(300)):>10}"
             f"    {DIM}last hour{RESET} {money(src.window_cost(3600)):>10}")

    # Today's spend only, like guard.py: the larger of the two records, so
    # switching source can never lower the figure.
    dc, dt = store.day_total()
    if led:
        lc, lt = led.day_total()
        dc, dt = max(dc, lc), max(dt, lt)

    if budget:
        cold = budget.get("cold_resume") or {}
        lines = [budget_line(name, c, t, budget.get(f"{key}_limit_usd"), budget.get(f"{key}_limit_tokens"))
                 for name, key, c, t in (("session", "session", b.cost, b.wtok),
                                         ("today", "daily", dc, dt))]
        lines = [x for x in lines if x]
        # The job budget lives in guard.py's state for this session.
        try:
            gs = json.load(open(os.path.join(CFG, "state", sid + ".json")))
        except Exception:
            gs = {}
        job = gs.get("job")
        if job:
            jl = budget_line("job", max(0.0, b.cost - float(job["base_cost"])),
                             max(0, b.wtok - int(job["base_tokens"])), job.get("usd"), job.get("tokens"))
            if jl:
                lines.append(jl + f"  {DIM}{job.get('label', '')}{RESET}")
        if gs.get("ask_fired"):
            lines.append(f"    {YELLOW}{'check-in':<9} paused: waiting for your budget reply{RESET}")
        tu, tt = budget.get("turn_limit_usd"), budget.get("turn_limit_tokens")
        if lines or tu or tt:
            L.append("")
            L.append(f"  {BOLD}BUDGET{RESET}  {DIM}{'hard stop' if budget.get('hard_stop') else 'warn only'}"
                     f" · warns at {budget.get('warn_at_percent', 70)}%{RESET}")
            L.extend(lines)
            if tb and tb.ctx:
                # Same rules as guard.py: re-read cost per call, and whether the
                # next prompt would hit an expired cache.
                idle = time.time() - max(x.last for x in (tb, b) if x.last).timestamp()
                lim = 60 * float(cold.get("idle_minutes_" + tb.ttl, 55 if tb.ttl == "1h" else 4))
                per = tb.ctx * rate(prices, tb.ctx_model, "cache_read") / 1e6
                marks = sorted(int(x) for x in (budget.get("context_warn_tokens") or []))
                color = RED if marks and tb.ctx >= marks[-1] else (YELLOW if marks and tb.ctx >= marks[0] else DIM)
                if idle >= lim:
                    cache = (f"{RED}cache COLD{RESET}{DIM}: next prompt re-writes it, "
                             f"~{money(tb.ctx * rate(prices, tb.ctx_model, 'cache_write_' + tb.ttl) / 1e6)}")
                else:
                    cache = f"cache warm, {int((lim - idle) // 60)}m left"
                L.append(f"    {'context':<9} {color}{num(tb.ctx)} tok{RESET}{DIM} · "
                         f"{money(per)}/call to re-read · {cache}{RESET}")
            if tu or tt:
                lim = " · ".join(x for x in ((f"${float(tu):,.2f}" if tu else ""),
                                             (f"{int(tt):,} tok" if tt else "")) if x)
                L.append(f"    {DIM}{'request':<9} {lim}, warned in the chat by guard.py{RESET}")

    if tb and tb.tools:
        rows = sorted(tb.tools.items(), key=lambda kv: -(kv[1]["ask"] + kv[1]["result"]))
        L.append("")
        L.append(f"  {BOLD}TOOLS{RESET}  {DIM}cost of asking + cost of the answer in context{RESET}")
        for nm, e in rows[:8]:
            tot = e["ask"] + e["result"]
            short = nm.split("__")[-1][:22]
            L.append(f"    {short:<24}{e['calls']:>5} {DIM}calls{RESET}"
                     f"{money(tot):>11}   {DIM}ask {money(e['ask'])} · ctx {money(e['result'])}"
                     f" ({num(e['rtokens'])} tok){RESET}")
        if len(rows) > 8:
            L.append(f"    {DIM}+{len(rows)-8} more{RESET}")

    if show_all:
        proj = store.proj_of.get(sid)
        same = sum(x.cost for s, x in store.sessions.items() if store.proj_of.get(s) == proj)
        allp = sum(x.cost for x in store.sessions.values())
        L.append("")
        L.append(f"  {BOLD}TOTALS{RESET}")
        L.append(f"    {'today, all projects':<26}{money(dc):>12}")
        L.append(f"    {'this project, all time':<26}{money(same):>12}")
        L.append(f"    {'all projects, all time':<26}{money(allp):>12}   {DIM}{len(store.sessions)} sessions{RESET}")

    L.append("")
    L.append(f"  {DIM}ctrl-c to quit{RESET}")
    return "\n".join(L)


def snapshot(store, sid, prices, led=None):
    b = pick(store, led, sid)[0] if led else store.sessions.get(sid)
    if not b:
        return {"session": sid, "cost_usd": 0.0, "tokens": 0, "messages": 0}
    return {
        "session": sid,
        "cost_usd": round(b.cost, 6),
        "tokens": b.tokens,
        "messages": b.msgs,
        "breakdown": {"input": b.inp, "cache_write_1h": b.cw1h, "cache_write_5m": b.cw5,
                      "cache_read": b.cr, "output": b.out, "thinking": b.think},
        "tools": {k: {"calls": v["calls"], "cost_usd": round(v["ask"] + v["result"], 6),
                      "context_tokens": v["rtokens"]}
                  for k, v in sorted(b.tools.items(),
                                     key=lambda kv: -(kv[1]["ask"] + kv[1]["result"]))},
        "by_source": {k: {"calls": n, "cost_usd": round(c, 6)}
                      for k, (n, c) in sorted(b.by_source.items(), key=lambda kv: -kv[1][1])},
        "prices_verified": bool(prices.get("_verified")),
        "all_sessions_cost_usd": round(sum(x.cost for x in store.sessions.values()), 6),
        "requests": [{"start": datetime.fromtimestamp(t, timezone.utc).isoformat(),
                      "cost_usd": round(c, 6), "calls": n}
                     for t, c, n in requests(b.calls, store.prompts.get(sid, []))[-5:]],
    }


def main():
    ap = argparse.ArgumentParser(description="Live token and cost monitor for Claude Code.")
    ap.add_argument("--session", help="session id to pin to (default: most recently active)")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--json", action="store_true", help="machine-readable snapshot, implies --once")
    ap.add_argument("--all", action="store_true", help="include cross-project totals")
    ap.add_argument("--interval", type=float, default=2.0, help="refresh seconds (default 2)")
    a = ap.parse_args()

    prices = load_prices()
    budget = {}
    if os.path.exists(BUDGET_PATH):
        try:
            budget = json.load(open(BUDGET_PATH))
        except Exception:
            budget = {}

    store = Store(prices)
    store.scan()
    led = LedgerStore(prices)
    led.scan()
    sid = a.session or store.newest_session()

    if a.json or a.once:
        if a.json:
            print(json.dumps(snapshot(store, sid, prices, led), indent=2))
        else:
            print(render(store, sid, prices, budget, True, led))
        return

    sys.stdout.write("\033[?25l")   # hide cursor
    try:
        while True:
            store.scan()
            led.scan()
            if not a.session:
                sid = store.newest_session() or sid
            out = render(store, sid, prices, budget, a.all or True, led)
            sys.stdout.write("\033[H\033[J" + out + "\n")
            sys.stdout.flush()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\n")   # show cursor


if __name__ == "__main__":
    main()

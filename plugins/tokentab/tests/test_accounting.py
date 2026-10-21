"""Regression fixture for TokenTab cost accounting.

Builds a synthetic transcript with (1) two models, (2) one assistant message that calls
several tools, (3) a compaction/summary turn, feeds it to tokentab.Store in two scans
(before / after the compaction, which also exercises incremental tailing), and asserts:
  sum(cost_by) == cost, per-model cost sums, per-tool fields <= the rate they draw from,
  every Sankey column (plus pass-through ribbons) sums to the session total, and node
  inflow == outflow. Step-specific checks run when the new fields exist.

No dependencies, no pytest; run from anywhere, exits non-zero on a failure:

  python3 plugins/tokentab/tests/test_accounting.py
"""
import json, os, sys, tempfile
from xml.dom import minidom

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PLUGIN, "tokentab"))
import tokentab as kt
import sankey

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5"
T = [0]


def ts():
    T[0] += 1
    return f"2026-10-13T10:{T[0] // 60:02d}:{T[0] % 60:02d}.000Z"


def usage(i=0, r=0, w1=0, w5=0, o=0, th=None):
    u = {"input_tokens": i, "cache_read_input_tokens": r, "cache_creation_input_tokens": w1 + w5,
         "cache_creation": {"ephemeral_1h_input_tokens": w1, "ephemeral_5m_input_tokens": w5},
         "output_tokens": o}
    if th is not None:
        u["output_tokens_details"] = {"thinking_tokens": th}
    return u


def asst(req, model, u, block, **kw):
    return dict(type="assistant", sessionId="fx", requestId=req, timestamp=ts(),
                message={"model": model, "usage": u, "content": [block]}, **kw)


def user(content, **kw):
    return dict(type="user", sessionId="fx", timestamp=ts(), message={"role": "user", "content": content}, **kw)


def hook(text):
    return dict(type="attachment", sessionId="fx", timestamp=ts(),
                attachment={"type": "hook_additional_context", "content": [text], "hookEvent": "UserPromptSubmit"})


def tu(i, name, inp):
    return {"type": "tool_use", "id": i, "name": name, "input": inp}


def tr(i, text):
    return {"type": "tool_result", "tool_use_id": i, "content": text}


uA = usage(i=10, w1=20000, o=1000, th=400)
part1 = [
    user("fix the bug"),
    hook("JOB CHECK (from TokenTab, not the user): " + "x" * 300),
    # (2) one message, several content lines, several tools; (1) model one
    asst("rA", OPUS, uA, {"type": "thinking", "thinking": "t" * 800}),
    asst("rA", OPUS, uA, {"type": "text", "text": "I'll look."}),
    asst("rA", OPUS, uA, tu("t1", "Bash", {"command": "ls -la"})),
    asst("rA", OPUS, uA, tu("t2", "Read", {"file_path": "/a/" + "p" * 200})),
    user([tr("t1", "a" * 4000), tr("t2", "b" * 1000)]),
    hook("BUDGET HINT (from TokenTab): " + "y" * 300),
    # (1) model two, mixed 1h/5m write
    asst("rB", SONNET, usage(i=5, r=20010, w1=1500, w5=500, o=300), tu("t3", "Grep", {"pattern": "foo"})),
    user([tr("t3", "c" * 2000)]),
    asst("rC", OPUS, usage(i=2, r=22000, w1=800, o=200), {"type": "text", "text": "Found it." * 20}),
    user("next"),
    asst("rD", OPUS, usage(i=2, r=22800, w1=100, o=150), tu("t4", "Bash", {"command": "make test"})),
    # streaming: a later line of the same request reports more output
    asst("rD", OPUS, usage(i=2, r=22800, w1=100, o=180), {"type": "text", "text": "running"}),
    user([tr("t4", "d" * 3000)]),
    asst("rE", OPUS, usage(i=2, r=22900, w1=900, o=100), {"type": "text", "text": "Tests pass."}),
]
part2 = [
    # (3) compaction: boundary marker + summary turn; context drops sharply
    dict(type="system", subtype="compact_boundary", sessionId="fx", timestamp=ts(), content="Conversation compacted"),
    user("This session is being continued from a previous conversation. " + "s" * 2000, isCompactSummary=True),
    asst("rF", OPUS, usage(i=3, r=4000, w1=3000, o=50), {"type": "text", "text": "Continuing."}),
    user("go on"),
    asst("rG", OPUS, usage(i=3, r=7000, w1=40, o=50), {"type": "text", "text": "Done."}),
]


def scan(root, lines):
    with open(os.path.join(root, "proj", "fx.jsonl"), "a") as f:
        for o in lines:
            f.write(json.dumps(o, separators=(",", ":")) + "\n")


def tool_fields(b):
    """name -> model -> fields, whatever shape the bucket has (old: name -> fields)."""
    out = {}
    for name, e in b.tools.items():
        out[name] = e if all(isinstance(v, dict) for v in e.values()) else {"?": e}
    return out


DRAWS = {"ask": "output", "think": "thinking", "result_1h": "cache_write_1h",
         "result_5m": "cache_write_5m", "read": "cache_read"}


def check(b, label, tb=None):
    errs = []
    near = lambda a, c: abs(a - c) < 1e-9
    if not near(sum(b.cost_by.values()), b.cost):
        errs.append(f"sum(cost_by)={sum(b.cost_by.values())} != cost={b.cost}")
    bm = getattr(b, "cost_bm", None)
    if bm is not None:
        for k in b.cost_by:
            if not near(sum(m.get(k, 0) for m in bm.values()), b.cost_by[k]):
                errs.append(f"cost_bm[*][{k}] does not sum to cost_by[{k}]")
        # per (model, rate): what tools (+ overhead) draw <= what the rate has
        draw = {}
        entries = [(n, m, f) for n, ms in tool_fields(b).items() for m, f in ms.items()]
        entries += [("overhead", m, f) for m, f in (getattr(b, "overhead", None) or {}).items()]
        for n, m, f in entries:
            for fld, r in DRAWS.items():
                draw[(m, r)] = draw.get((m, r), 0) + f.get(fld, 0)
        for (m, r), v in draw.items():
            if v > bm.get(m, {}).get(r, 0) + 1e-9:
                errs.append(f"tools draw {v:.6f} from {m}/{r} > {bm.get(m, {}).get(r, 0):.6f}")
    # Sankey: every column plus the ribbons passing through it sums to the total
    cols, links, _, _ = sankey.graph(b, tb)
    total = sum(cols[0].values())
    if not near(total, b.cost):
        errs.append(f"sankey total {total} != cost {b.cost}")
    ci = {n: i for i, c in enumerate(cols) for n in c}
    for i, c in enumerate(cols):
        through = sum(v for s, d, v in links if ci[s] < i < ci[d])
        if abs(sum(c.values()) + through - total) > 1e-9:
            errs.append(f"sankey column {i} sums to {sum(c.values()) + through:.6f}, not {total:.6f}")
    for n, v in ((n, v) for c in cols for n, v in c.items()):
        i_ = sum(x for s, d, x in links if d == n); o_ = sum(x for s, d, x in links if s == n)
        if i_ and abs(i_ - v) > 1e-9: errs.append(f"node {n}: in {i_} != {v}")
        if o_ and abs(o_ - v) > 1e-9: errs.append(f"node {n}: out {o_} != {v}")
    if any(v < -1e-12 for _, _, v in links):
        errs.append("negative link")
    # the SVG parses
    minidom.parseString(sankey.svg(b, tb))
    print(f"[{label}] cost ${b.cost:.6f}  " + ("OK" if not errs else "FAIL"))
    for e in errs:
        print("   ", e)
    return errs


def main():
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "proj"))
    kt.ROOT = root
    kt.CFG = os.path.join(root, "cfg")              # no ledger: transcript bucket only
    prices = kt.load_prices()
    store = kt.Store(prices)
    scan(root, part1); store.scan()
    b = store.sessions["fx"]
    errs = check(b, "before compaction")
    before = json.loads(json.dumps(tool_fields(b)))
    scan(root, part2); store.scan()
    errs += check(b, "after compaction")
    after = tool_fields(b)
    for n, ms in after.items():
        for m, f in ms.items():
            print(f"    {n:<6}{m:<18}" + "  ".join(f"{k}={v:.6f}" if isinstance(v, float) else f"{k}={v}"
                                                for k, v in f.items()))
    if hasattr(b, "overhead"):
        for m, f in b.overhead.items():
            print(f"    {'(kt)':<6}{m:<18}" + "  ".join(f"{k}={v:.6f}" if isinstance(v, float) else f"{k}={v}"
                                                 for k, v in f.items()))
    print("    cost_by", {k: round(v, 6) for k, v in b.cost_by.items()})
    # residents reset on compaction: nothing re-reads a pre-compaction tool afterwards
    for n, ms in after.items():
        for m, f in ms.items():
            if "read" in f and abs(f["read"] - before[n][m]["read"]) > 1e-12:
                errs.append(f"{n}/{m} kept collecting read after compaction")
    # the snapshot is JSON and its cost_by sums to cost_usd
    snap = json.loads(json.dumps(kt.snapshot(store, "fx", prices)))
    if abs(sum(snap.get("cost_by", {}).values()) - snap["cost_usd"]) > 1e-5:
        errs.append("snapshot cost_by does not sum to cost_usd")
    kt.render(store, "fx", prices, {}, True)
    errs += pane_check(store, prices)
    errs += series_check(b, "transcript", store)
    errs += ledger_case(root, store, prices)
    print("ALL OK" if not errs else f"{len(errs)} FAILURES")
    return errs


def pane_check(store, prices):
    """`--report`, what the pane draws: JSON, the session against its limit, the rates
    summing to the total, tools with the three draws; this month counts only this month."""
    errs = []
    budget = {"session_limit_usd": 0.0001, "session_limit_tokens": None, "warn_at_percent": 70}
    rep = json.loads(json.dumps(kt.pane_report(store, "fx", prices, budget)))
    s = rep["session"]
    if not s or s["limit_usd"] != 0.0001 or s["limit_tokens"] is not None or not s["frac"] or s["frac"] < 1:
        errs.append(f"pane report: session against its limit is wrong: {s}")
    if abs(sum(r["cost"] for r in rep["rates"]) - rep["total"]["cost"]) > 1e-6:
        errs.append("pane report: rates do not sum to the total")
    if not rep["tools"] or any(abs(t["ask"] + t["ctx"] + t["read"] - t["cost"]) > 1e-6 for t in rep["tools"]):
        errs.append("pane report: a tool's ask + ctx + re-read is not its cost")
    if kt.pane_report(store, "fx", prices, {})["session"]["frac"] is not None:
        errs.append("pane report: no limit should give no fraction")
    now = kt.datetime.now().timestamp()
    if abs(kt.month_total([(now, 1.5, 0), (now - 400 * 86400, 9.0, 0)]) - 1.5) > 1e-9:
        errs.append("month_total counts a day outside this month")
    return errs


def ledger_case(root, store, prices):
    """Telemetry has no thinking count: LedgerStore.reconcile takes it from the transcript
    by request id. A ledger line can come before its transcript line, a background call
    has none, and reconciling twice must change nothing."""
    errs = []
    os.makedirs(os.path.join(kt.CFG, "ledger", "2026-10-13"), exist_ok=True)
    def led_line(rid, model, u, src="sdk"):
        return {"ts": "2026-10-13T10:00:00+00:00", "session_id": "fx", "model": model, "request_id": rid,
                "query_source": src, "tokens": {"input": u["input_tokens"], "output": u["output_tokens"],
                "cache_read": u["cache_read_input_tokens"], "cache_write": u["cache_creation_input_tokens"]}}
    # a line written by a collector with a stale price table: cost_usd and fees_usd are
    # wrong, cost_usd_cc (Claude Code's own figure) is right and includes a real $0.01
    # web search fee. On read, the fee must be worked out against today's prices.
    uS = usage(i=5, r=100, o=0)
    tok = (uS["input_tokens"] * kt.rate(prices, OPUS, "input") + uS["cache_read_input_tokens"] * kt.rate(prices, OPUS, "cache_read")
           + uS["cache_creation_input_tokens"] * kt.rate(prices, OPUS, "cache_write_1h")) / 1e6
    stale = dict(led_line(None, OPUS, uS, "web_search"), cost_usd=tok * 0.6, fees_usd=tok * 0.4 + 0.01, cost_usd_cc=tok + 0.01)
    del stale["request_id"]
    with open(os.path.join(kt.CFG, "ledger", "2026-10-13", "fx.jsonl"), "w") as f:
        for e in (led_line("rA", OPUS, uA), led_line("rX", OPUS, usage(i=5, r=100, o=80), "prompt_suggestion"), stale):
            f.write(json.dumps(e) + "\n")
    led = kt.LedgerStore(prices); led.scan()
    lb = led.sessions["fx"]
    if abs(lb.cost_by.get("fees", 0) - 0.01) > 1e-9:
        errs.append(f"ledger: fees {lb.cost_by.get('fees', 0):.6f} from a stale-priced line, want 0.010000")
    cost0, out0 = lb.cost, lb.cost_by["output"]
    led.reconcile(kt.Store(prices))              # transcript not read yet: nothing moves
    if lb.cost_by.get("thinking", 0) or lb.think:
        errs.append("ledger: thinking filed without a transcript line")
    for _ in range(2):                           # then twice with the transcript
        led.reconcile(store)
    want = 400 * kt.rate(prices, OPUS, "output") / 1e6
    if abs(lb.cost_by.get("thinking", 0) - want) > 1e-12 or lb.think != 400:
        errs.append(f"ledger: thinking {lb.cost_by.get('thinking', 0):.6f} / {lb.think} tok, want {want:.6f} / 400")
    if abs(lb.cost - cost0) > 1e-12 or abs(lb.cost_by["output"] + lb.cost_by["thinking"] - out0) > 1e-12:
        errs.append("ledger: reconcile changed the total")
    if led.unmatched("fx", store) != 80:
        errs.append(f"ledger: unmatched output {led.unmatched('fx', store)}, want 80")
    errs += check(lb, "ledger + transcript tools", tb=store.sessions["fx"])
    errs += series_check(lb, "ledger", store)
    return errs


def series_check(b, label, store):
    """The cost-over-time chart: per-call entries sum to the rate totals (thinking moved
    by reconcile included), one entry per call, and the panel is well-formed SVG."""
    errs = []
    for k, v in b.cost_by.items():
        got = sum(e[1].get(k, 0.0) for e in b.series)
        if abs(got - v) > 1e-9:
            errs.append(f"{label}: series {k} sums to {got:.6f}, want {v:.6f}")
    if len(b.series) != b.msgs:
        errs.append(f"{label}: {len(b.series)} series entries for {b.msgs} calls")
    R = {"bucket": b, "prompts": store.prompts.get("fx", []),
         "rates": [{"key": k} for k, v in b.cost_by.items() if v]}
    out = sankey.timeline(R)
    try:
        minidom.parseString(f"<div>{out}</div>")
    except Exception as e:
        errs.append(f"{label}: chart is not well-formed: {e}")
    if out.count("<svg") != 2 or 'class="ln"' not in out:
        errs.append(f"{label}: chart is missing an axis or its lines")
    return errs


if __name__ == "__main__":
    sys.exit(1 if main() else 0)

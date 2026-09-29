#!/usr/bin/env python3
"""keeptabs budget guard - a Claude Code hook that warns, then stops.

Three scopes, most restrictive wins:
  turn    one prompt and everything it sets off. Resets on the next prompt.
  session this conversation.
  daily   every session today, across every project.

Plus a job budget, set by starting a prompt with "$2:", "beer:" or "200k:".
It runs until the next one, or "nobudget:" to clear it.

Wired to PreToolUse (the brake inside a turn) and UserPromptSubmit (the stop
between turns). Config: ~/.claude/keeptabs/budget.json
"""
import glob, json, os, re, sys, time
from datetime import datetime

CFG   = os.path.expanduser("~/.claude/keeptabs")
HERE  = os.path.dirname(os.path.abspath(__file__))  # plugin: data in CFG, shipped files next to this script
STATE = os.path.join(CFG, "state")

def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default

def rate(prices, model, key):
    m = prices.get("models", {}).get(model) or prices.get("default", {})
    return float(m.get(key, 0.0))

def local_date(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()
    except Exception:
        return datetime.now().astimezone().date().isoformat()

FORMAT = 2   # 2: requests another session already counted are skipped (see claims)

def claims(except_sid):
    """Request ids counted by other sessions. A resumed or forked session's
    transcript repeats its parent's history with the same request ids, so
    without this the daily total counts that history twice. Only current-
    format state files count: older ones never recorded what they skipped."""
    out = set()
    try:
        names = os.listdir(STATE)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json") or name.startswith("collector") or name == except_sid + ".json":
            continue
        o = load(os.path.join(STATE, name), {})
        if o.get("v") == FORMAT:
            out |= set(o.get("seen") or []) - set(o.get("dup") or [])
    return out

def tally(transcript, prices, st, others=frozenset()):
    """Incrementally sum usage, skipping requests in others (counted elsewhere).

    Returns (cost, tokens, state, tampered). Totals never decrease: the log is
    append-only in normal operation, so a fall means it was rewritten.
    """
    # State keeps RAW token counts per date and model. Cost and weighted tokens
    # are derived from them at the current prices every time, so a price change
    # re-prices history. The tamper check runs on raw counts, which only grow.
    keep = {k: st.get(k) for k in ("band_session", "band_today", "ctx_band", "cold_block_at",
                                   "turn_base_cost", "turn_base_tokens",
                                   "turn_band", "health_warned", "health_warned_at",
                                   "job", "band_job", "recent", "prompted", "ask_fired", "subs")}
    if "raw" not in st or st.get("v") != FORMAT:   # old format: re-read the transcript once
        st = {"offset": 0, "seen": [], "dup": [], "raw": {}, "hwm_raw": 0,
              "tampered": bool(st.get("tampered"))}
    hwm = int(st.get("hwm_raw", 0))
    raw = st.get("raw") or {}
    try:
        size = os.path.getsize(transcript)
    except OSError:
        return (*price_raw(raw, prices), st, bool(st.get("tampered")))
    off = st.get("offset", 0)
    seen = set(st.get("seen", []))
    dup = set(st.get("dup", []))
    counted = st.get("counted") or {}   # request id -> fields already counted
    if size < off:                      # truncated, rotated, or rewritten
        off, seen, dup, raw, counted = 0, set(), set(), {}, {}
    data, new_off = "", off
    if size > off:
        with open(transcript, "r", errors="replace") as fh:
            fh.seek(off)
            data = fh.read()
            new_off = fh.tell()
        if data and not data.endswith("\n"):        # hold back a partial line
            cut = data.rfind("\n")
            data = data[:cut+1] if cut != -1 else ""
            new_off = off + len(data.encode("utf-8", "replace"))

    for line in data.splitlines():
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
        if not isinstance(u, dict):
            continue
        key = o.get("requestId") or o.get("uuid")
        if key:
            if key in others:           # already counted by the session it came from
                seen.add(key)
                dup.add(key)
                continue
            if key in seen and key not in counted:
                continue                # counted by an older state format: leave it
            seen.add(key)
        model = msg.get("model") or "unknown"
        cc = u.get("cache_creation") or {}
        w1 = int(cc.get("ephemeral_1h_input_tokens") or 0)
        w5 = int(cc.get("ephemeral_5m_input_tokens") or 0)
        if not (w1 or w5):
            w5 = int(u.get("cache_creation_input_tokens") or 0)
        now = {"input": int(u.get("input_tokens") or 0), "cache_write_5m": w5, "cache_write_1h": w1,
               "cache_read": int(u.get("cache_read_input_tokens") or 0),
               "output": int(u.get("output_tokens") or 0)}
        # One request can be logged on several lines while its output streams in.
        # Count it once and let later lines add only what grew, so the final
        # figure wins instead of the first partial one.
        before = counted.get(key) or {} if key else {}
        d = raw.setdefault(local_date(o.get("timestamp")), {}).setdefault(
            model, {"input": 0, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "output": 0})
        for f, n in now.items():
            d[f] += max(0, n - int(before.get(f, 0)))
        if key:
            counted[key] = {f: max(n, int(before.get(f, 0))) for f, n in now.items()}

    total_raw = sum(n for day in raw.values() for m in day.values() for n in m.values())
    tampered = bool(st.get("tampered")) or total_raw < hwm
    st = {"v": FORMAT, "offset": new_off, "seen": sorted(seen), "dup": sorted(dup), "raw": raw,
          "counted": counted,
          "hwm_raw": max(total_raw, hwm), "tampered": tampered}
    st.update({k: v for k, v in keep.items() if v is not None})
    cost, toks = price_raw(raw, prices)
    return cost, toks, st, tampered


def price_raw(raw, prices, only_date=None):
    """(USD, weighted tokens) for raw counts {date: {model: {key: n}}}, at
    current prices. Cache reads count toward tokens at their price relative
    to fresh input for that model (0.05x on Opus 5.5, 0.1x elsewhere)."""
    cost, toks = 0.0, 0
    for day, models in raw.items():
        if only_date and day != only_date:
            continue
        for model, n in models.items():
            ri = rate(prices, model, "input")
            r = n.get("cache_read", 0)
            toks += (n.get("input", 0) + n.get("cache_write_5m", 0) + n.get("cache_write_1h", 0)
                     + n.get("output", 0) + (int(r * rate(prices, model, "cache_read") / ri) if ri else r))
            cost += sum(n.get(k, 0) * rate(prices, model, k) for k in
                        ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")) / 1_000_000.0
    return cost, toks

def day_totals(today, prices):
    """Today across every session's state file, priced now."""
    c, t = 0.0, 0
    try:
        names = os.listdir(STATE)
    except OSError:
        return c, t
    for name in names:
        if not name.endswith(".json") or name.startswith("collector"):
            continue
        s = load(os.path.join(STATE, name), {})
        for raw in [s.get("raw") or {}] + [(x or {}).get("raw") or {} for x in (s.get("subs") or {}).values()]:
            dc, dt = price_raw(raw, prices, only_date=today)
            c += dc; t += dt
    return c, t

def fee(e):
    """Charges that are not tokens (web search is billed per search). Taken from
    Claude Code's own cost figure, which includes them: older ledger lines have no
    fees_usd, so it is worked out from cost_usd_cc for those."""
    if e.get("fees_usd") is not None:
        return float(e["fees_usd"])
    cc = e.get("cost_usd_cc")
    d = float(cc) - float(e.get("cost_usd") or 0.0) if cc is not None else 0.0
    return d if d > 0.0005 else 0.0

def ledger_totals(sid, today, prices):
    """(session cost, session tokens, day cost, day tokens) from the collector's
    ledger. Tokens are weighted like tally(): cache reads at their price
    relative to input. Returns zeros when there is no ledger."""
    sc = dc = 0.0; st_ = dt = 0
    for p in sorted(glob.glob(os.path.join(CFG, "ledger", "*.jsonl")))[-2:]:
        try:
            lines = open(p).read().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            m, t = e.get("model") or "unknown", e.get("tokens") or {}
            ri = rate(prices, m, "input")
            r = int(t.get("cache_read") or 0)
            n = (int(t.get("input") or 0) + int(t.get("cache_write") or 0) + int(t.get("output") or 0)
                 + (int(r * rate(prices, m, "cache_read") / ri) if ri else r))
            c = float(e.get("cost_usd") or 0.0) + fee(e)
            if e.get("session_id") == sid:
                sc += c; st_ += n
            try:
                d = datetime.fromisoformat(e["ts"].replace("Z", "+00:00")).astimezone().date().isoformat()
            except (KeyError, ValueError):
                d = None
            if d == today:
                dc += c; dt += n
    return sc, st_, dc, dt

def last_call(transcript, sid):
    """(epoch the last API call started, context tokens, model, cache TTL) for
    this session, or None. Context is everything the call sent: fresh input plus
    cache read plus cache write. The transcript gives size and model; the ledger,
    when it has a later call (prompt suggestions run after each turn and read the
    same cache), gives the time. A cache entry's hour runs from the START of the
    last request that touched it, so the ledger's duration is subtracted."""
    found = None
    try:
        size = os.path.getsize(transcript)
        with open(transcript, "rb") as fh:
            for span in (512 * 1024, 8 * 1024 * 1024):   # tool results can be large
                fh.seek(max(0, size - span))
                for line in reversed(fh.read().splitlines()):
                    if b'"usage"' not in line or b'"assistant"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    u = (o.get("message") or {}).get("usage")
                    if o.get("type") != "assistant" or not isinstance(u, dict):
                        continue
                    # Claude Code writes placeholder replies ("No response requested.",
                    # model <synthetic>, all-zero usage), e.g. on resume. They are not
                    # API calls; taking one as the last call hides a cold cache.
                    if (o["message"].get("model") == "<synthetic>"
                            or not any(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                       "cache_read_input_tokens", "cache_creation_input_tokens"))):
                        continue
                    cc = u.get("cache_creation") or {}
                    w1 = int(cc.get("ephemeral_1h_input_tokens") or 0)
                    w5 = int(cc.get("ephemeral_5m_input_tokens") or 0) or (
                        0 if w1 else int(u.get("cache_creation_input_tokens") or 0))
                    ctx = (int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                           + w1 + w5)
                    ts = datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00")).timestamp()
                    found = [ts, ctx, o["message"].get("model") or "unknown", "5m" if w5 and not w1 else "1h"]
                    break
                if found or span >= size:
                    break
    except (OSError, KeyError, ValueError):
        return None
    if not found:
        return None
    for p in sorted(glob.glob(os.path.join(CFG, "ledger", "*.jsonl")))[-2:]:
        try:
            lines = open(p).read().splitlines()
        except OSError:
            continue
        for line in lines:
            if sid not in line:
                continue
            try:
                e = json.loads(line)
                start = (datetime.fromisoformat(e["ts"].replace("Z", "+00:00")).timestamp()
                         - float(e.get("duration_ms") or 0) / 1000.0)
            except (ValueError, KeyError):
                continue
            if e.get("session_id") == sid and start > found[0]:
                found[0] = start
    return tuple(found)

def ago(seconds):
    m = int(seconds // 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"

def closest(usd_lim, tok_lim, c, t):
    f = []
    if usd_lim: f.append((c / float(usd_lim), f"${c:,.2f} of ${float(usd_lim):,.2f}"))
    if tok_lim: f.append((t / float(tok_lim), f"{t:,} of {int(tok_lim):,} tokens"))
    return max(f, key=lambda x: x[0]) if f else (0.0, "")

def deny(event, user_msg, model_msg):
    out = {"systemMessage": user_msg}
    if event == "UserPromptSubmit":
        out["decision"] = "block"
        out["reason"] = user_msg
    else:
        out["hookSpecificOutput"] = {"hookEventName": "PreToolUse",
                                     "permissionDecision": "deny",
                                     "permissionDecisionReason": model_msg}
    return out

def note(event, user_msg, model_msg):
    return {"systemMessage": user_msg,
            "hookSpecificOutput": ({"hookEventName": "UserPromptSubmit",
                                    "additionalContext": model_msg}
                                   if event == "UserPromptSubmit" else
                                   # No permissionDecision: "allow" would skip the
                                   # user's permission prompt just to deliver a warning.
                                   {"hookEventName": "PreToolUse",
                                    "additionalContext": model_msg})}

SCALE = {"water": 0.5, "beer": 6.0, "pizza": 20.0, "wine": 50.0, "champagne": 100.0}
JOB_RE = re.compile(r"^\s*(?:\$\s*(?P<usd>\d+(?:\.\d+)?)|(?P<tok>\d+(?:\.\d+)?)\s*(?P<unit>[km])\s*(?:tok(?:ens)?)?"
                    r"|(?:a\s+)?(?P<word>[a-z]+))\s*:", re.I)

def parse_job(prompt, scale):
    """A job budget at the start of a prompt. Returns ("set", usd, tokens, label),
    ("clear",), or None when the prompt does not start with one."""
    prompt = re.sub(r"^\s*<pasted_content[^>]*>\s*", "", prompt or "")
    m = JOB_RE.match(prompt)
    if not m:
        return None
    if m.group("usd"):
        return ("set", float(m.group("usd")), None, f"${float(m.group('usd')):,.2f}")
    if m.group("tok"):
        n = int(float(m.group("tok")) * (1000 if m.group("unit").lower() == "k" else 1000000))
        return ("set", None, n, f"{n:,} tokens")
    w = m.group("word").lower()
    if w in ("nobudget", "nojob"):
        return ("clear",)
    if w in scale:
        return ("set", float(scale[w]), None, f"a {w} (${float(scale[w]):,.2f})")
    return None                                   # "note:", "todo:" and so on are just text

def scale_line(scale):
    return ", ".join(f"{k} ${v:,.2f}" for k, v in sorted(scale.items(), key=lambda kv: kv[1]))

def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if os.environ.get("KEEPTABS_DEBUG") or os.path.exists(os.path.join(CFG, "state", "debug")):
        try:
            with open(os.path.join(CFG, "state", "payloads.log"), "a") as fh:
                fh.write(json.dumps({k: v for k, v in payload.items() if k not in ("tool_input", "prompt", "tool_response")}) + "\n")
        except Exception:
            pass

    bpath = os.path.join(CFG, "budget.json")
    b = load(bpath, None)
    if b is None:
        # A broken budget file must not look like "no limits". Say so every prompt.
        if os.path.exists(bpath) and payload.get("hook_event_name") == "UserPromptSubmit":
            try:
                json.load(open(bpath))
                why = "unreadable"
            except Exception as e:
                why = str(e)
            print(json.dumps({"systemMessage":
                f"keeptabs: budget.json cannot be read ({why}). No budgets are being "
                f"checked until it is fixed."}))
        return 0
    scopes_cfg = [
        ("session", b.get("session_limit_usd"), b.get("session_limit_tokens")),
        ("today",   b.get("daily_limit_usd"),   b.get("daily_limit_tokens")),
        ("request", b.get("turn_limit_usd"),    b.get("turn_limit_tokens")),
    ]
    if not any(u or t for _, u, t in scopes_cfg):
        return 0                                  # nothing configured, stay out of the way

    transcript = payload.get("transcript_path")
    # A new session's first prompt arrives before Claude Code has written the
    # transcript. Let it through with zero spend, so the job check still runs.
    fresh = not transcript or not os.path.exists(transcript)
    if fresh and payload.get("hook_event_name") != "UserPromptSubmit":
        return 0
    sid = payload.get("session_id") or os.path.basename(transcript or "").replace(".jsonl", "")
    if not sid:
        return 0
    event = payload.get("hook_event_name") or ""
    helper = payload.get("agent_type") if payload.get("agent_id") else None
    if event == "PreToolUse" and payload.get("tool_name") == "Bash":
        cmd = str((payload.get("tool_input") or {}).get("command") or "")
        if re.fullmatch(r"\s*python3\s+\S*keeptabs/(keeptabs|health)\.py(\s+--?[\w=-]+(\s+[\w-]+)?)*\s*", cmd):
            return 0

    prices = load(os.path.join(HERE, "prices.json"), {})
    os.makedirs(STATE, exist_ok=True)
    spath = os.path.join(STATE, sid + ".json")
    st = load(spath, {})
    cost, toks, st, tampered = (0.0, 0, st, False) if fresh else tally(transcript, prices, st, claims(sid))
    # Helpers write their own transcripts under <session>/subagents. Telemetry
    # covers them too, but this keeps them counted when the collector is down.
    if not fresh:
        subs = st.get("subs") or {}
        for sp in sorted(glob.glob(os.path.join(os.path.dirname(transcript), sid, "subagents", "*.jsonl"))):
            k = os.path.basename(sp)
            c2, t2, subs[k], tam2 = tally(sp, prices, subs.get(k) or {}, claims(sid))
            cost += c2; toks += t2; tampered = tampered or tam2
        st["subs"] = subs

    if tampered:
        msg = (f"keeptabs: the usage record for this session shrank, which cannot happen "
               f"in normal operation. Treating it as tampering, using the recorded "
               f"high-water mark, and refusing to continue. Clear state/{sid}.json to "
               f"reset once you know why.")
        try:
            with open(os.path.join(STATE, "tamper.log"), "a") as fh:
                fh.write(json.dumps({"session": sid, "transcript": transcript,
                                     "hwm_cost": cost, "hwm_tokens": toks}) + "\n")
            with open(spath, "w") as fh:
                json.dump(st, fh)
        except Exception:
            pass
        print(json.dumps(deny(event, msg,
            "The usage record was rewritten. Stop, tell the user the tamper check fired, "
            "and do not modify the transcript, the guard or the budget.")))
        return 0

    # Prefer the ledger (telemetry, which includes background calls) whenever it
    # is healthy for this session. max() keeps the transcript figure if the
    # ledger is behind, so switching source can never lower a total.
    sys.path.insert(0, HERE)
    try:
        import health
        led_ok, led_why = health.check(sid=sid)
    except Exception as e:
        led_ok, led_why = False, f"health check failed ({type(e).__name__}: {e})."
    today = datetime.now().astimezone().date().isoformat()
    l_sc, l_st, l_dc, l_dt = ledger_totals(sid, today, prices)
    if led_ok:
        cost, toks = max(cost, l_sc), max(toks, l_st)

    # A new prompt starts a new turn. Baseline resets here and nowhere else.
    # The request that just ended goes into a short history first, so the
    # job estimate can start from what this user's requests actually cost.
    if event == "UserPromptSubmit" and st.get("turn_base_cost") is not None:
        st["recent"] = (st.get("recent") or [])[-9:] + [round(max(0.0, cost - float(st["turn_base_cost"])), 6)]
    if event == "UserPromptSubmit" or st.get("turn_base_cost") is None:
        st["turn_base_cost"], st["turn_base_tokens"] = cost, toks
        st["turn_band"] = 0
        st.pop("ask_fired", None)

    # Job budget. Set from the prompt, measured from the moment it was set.
    jcfg = b.get("job") or {}
    scale = {k.lower(): float(v) for k, v in (jcfg.get("scale") or SCALE).items()}
    job_msgs = []                                  # (user message, model message)
    first_prompt = event == "UserPromptSubmit" and not st.get("prompted")
    if event == "UserPromptSubmit":
        st["prompted"] = True
        j = parse_job(payload.get("prompt"), scale)
        if j and j[0] == "clear":
            st.pop("job", None)
            job_msgs.append(("keeptabs: job budget cleared.",
                "JOB NOTICE: the user cleared the job budget. Ignore the \"nobudget:\" prefix."))
        elif j:
            _, ju, jt, label = j
            st["job"] = {"usd": ju, "tokens": jt, "label": label, "base_cost": cost,
                         "base_tokens": toks, "at": time.time()}
            st["band_job"] = 0
            job_msgs.append((f"keeptabs: job budget set to {label}.",
                f"JOB BUDGET: the user set this job's budget to {label}, counted from now, "
                f"across prompts until they set a new one. The prefix before the colon is the "
                f"budget, not part of the task. Plan the work to fit it and keep an eye on the "
                f"budget hints keeptabs sends. If the job clearly will not fit, say so early and "
                f"offer a smaller version rather than running out halfway."))
    job = st.get("job")

    # This session's fresh state is saved first so today's total includes it.
    try:
        with open(spath, "w") as fh:
            json.dump(st, fh)
    except Exception:
        pass
    day_c, day_t = day_totals(today, prices)
    # Other sessions today may send telemetry even if this one does not.
    day_c, day_t = max(day_c, l_dc), max(day_t, l_dt)

    measured = {
        "session": (cost, toks),
        "today":   (day_c, day_t),
        # "is None", not "or": a baseline of 0 (a session's first turn) is real.
        "request": (max(0.0, cost - float(cost if st.get("turn_base_cost") is None else st["turn_base_cost"])),
                    max(0,   toks - int(toks if st.get("turn_base_tokens") is None else st["turn_base_tokens"]))),
    }
    if job:
        measured["job"] = (max(0.0, cost - float(job["base_cost"])), max(0, toks - int(job["base_tokens"])))
        scopes_cfg.append(("job", job.get("usd"), job.get("tokens")))

    warn_at = float(b.get("warn_at_percent", 70)) / 100.0
    hard    = bool(b.get("hard_stop", True))

    # Each scope is judged on its own and keeps its own warn band, so a scope
    # that is already over (say, today) cannot hide another's warning (this
    # request). A new prompt resets the turn, so a request ceiling never blocks
    # the prompt itself.
    def judge(name, frac, detail):
        """("deny" | "note", user message, model message) for one scope, or None."""
        pct = int(frac * 100)
        band_key = "turn_band" if name == "request" else "band_" + name
        label = {"session": "session", "today": "today's", "request": "this request's",
                 "job": "this job's"}[name]
        hard_ = bool(jcfg.get("hard_stop", True)) if name == "job" else hard
        if frac >= 1.0 and hard_ and name == "job":
            st[band_key] = 100
            return ("deny",
                f"keeptabs: this job used its budget ({detail}). Start your next prompt with "
                f"a new budget, like \"$2:\" or \"beer:\", or \"nobudget:\" to carry on without one.",
                f"This job has spent its budget ({detail}). Stop now. Tell the user what is "
                f"done and what is left, and what a realistic budget for the rest would be. "
                f"Do not raise the budget or edit keeptabs files yourself.")
        if frac >= 1.0 and hard_:
            st[band_key] = 100
            if name == "request":
                return ("deny",
                    f"keeptabs: this request hit its own limit ({detail}, {pct}%). Send a "
                    f"new prompt to start a fresh turn, or raise turn_limit_* in "
                    f"~/.claude/keeptabs/budget.json.",
                    f"This single request has spent {detail} and reached its per-request "
                    f"ceiling. Stop now and report what you finished and what is left. The "
                    f"user can continue with a new prompt. Do not raise the limit yourself.")
            scope = "session" if name == "session" else "daily"
            return ("deny",
                f"keeptabs: {scope} budget exhausted. {detail} ({pct}%). Raise the "
                f"{scope} limit in ~/.claude/keeptabs/budget.json to continue.",
                f"The {scope} budget is exhausted: {detail}. No further tool calls are "
                f"permitted. Stop work, tell the user, and do not attempt to raise the "
                f"limit or edit the budget file yourself.")
        if frac < warn_at:
            return None
        band = 100 if frac >= 1.0 else min(90, int(frac * 10) * 10)
        if band <= int(st.get(band_key, 0)):
            return None                           # once per 10% band
        st[band_key] = band
        if not hard_:
            # Warn-only mode: say so, and never ask Claude to cut work short.
            head = f"{label} limit reached" if band == 100 else f"{pct}% of {label} budget used"
            return ("note",
                f"keeptabs: {head} ({detail}). Warning only, nothing is blocked.",
                f"BUDGET NOTICE: {head} ({detail}). This is a soft limit: nothing is "
                f"blocked. Mention it to the user in one line and keep going, using fewer "
                f"calls where you can: every call in this conversation re-reads its whole "
                f"context.")
        return ("note",
            f"keeptabs: {pct}% of {label} budget used ({detail}). "
            f"Claude has been asked to wrap up.",
            f"BUDGET NOTICE: {pct}% of {label} budget is gone ({detail}). Start "
            f"wrapping up. Prefer cheap actions, avoid re-reading large files or "
            f"taking screenshots, keep responses short, and finish what is in "
            f"flight rather than starting new work. At 100% tool calls stop.")

    verdicts = []
    last = last_call(transcript, sid)

    # Cold resume. UserPromptSubmit runs before anything is sent, so a block
    # here costs nothing. Once the cache has expired, the next call re-writes the
    # whole context at the cache-write rate. Stop once; sending again within the
    # confirm window goes ahead. This blocks even in warn-only mode: a warning
    # that arrives with the prompt arrives after the money is spent.
    cold = b.get("cold_resume") or {}
    if event == "UserPromptSubmit" and cold and last:
        started, ctx, model, ttl = last
        idle = time.time() - started
        limit = 60.0 * float(cold.get("idle_minutes_" + ttl, 55 if ttl == "1h" else 4))
        if idle >= limit and ctx >= int(cold.get("min_context_tokens", 100000)):
            blocked = st.get("cold_block_at")
            if blocked and time.time() - float(blocked) <= float(cold.get("confirm_seconds", 120)):
                st.pop("cold_block_at", None)            # confirmed: let it through
            else:
                wr = rate(prices, model, "cache_write_" + ttl)
                fresh = int(cold.get("fresh_session_tokens", 75000))
                st["cold_block_at"] = time.time()
                try:
                    with open(spath, "w") as fh:
                        json.dump(st, fh)
                except Exception:
                    pass
                msg = (f"keeptabs: this session's prompt cache has expired (idle {ago(idle)}, "
                       f"{ttl} cache). Sending now re-writes {ctx:,} tokens, about "
                       f"${ctx * wr / 1e6:,.2f}. A new session starts at about ${fresh * wr / 1e6:,.2f}. "
                       f"Nothing was sent. Send the prompt again within "
                       f"{int(cold.get('confirm_seconds', 120))}s to go ahead anyway. "
                       f"(/compact does not help here: it reads the whole context once too.)")
                print(json.dumps({"decision": "block", "reason": msg, "systemMessage": msg}))
                return 0

    # Context size. Every call re-reads all of it, so this is the running cost
    # per call. Warn once per threshold; a compact drops below and re-arms it.
    marks = sorted(int(x) for x in (b.get("context_warn_tokens") or []))
    if marks and last:
        _, ctx, model, _ = last
        hit = [m for m in marks if ctx >= m]
        if not hit:
            st["ctx_band"] = 0
        elif hit[-1] > int(st.get("ctx_band") or 0):
            st["ctx_band"] = hit[-1]
            per = ctx * rate(prices, model, "cache_read") / 1e6
            verdicts.append(("note",
                f"keeptabs: context is {ctx:,} tokens, so every call re-reads it for about "
                f"${per:,.3f}. If the next thing is a different task, a new session is cheaper.",
                f"CONTEXT NOTICE: this session's context is {ctx:,} tokens (about ${per:,.3f} per "
                f"call to re-read). Mention it to the user in one line, then carry on as normal."))

    # Context for Claude's own judgment, sent with every prompt. The hook has the
    # numbers; only Claude can tell whether the new prompt needs this context.
    hints = []
    ncfg = b.get("new_session") or {}
    if event == "UserPromptSubmit" and ncfg.get("enabled", True) and last:
        _, ctx, model, ttl = last
        fresh_tok = int(ncfg.get("fresh_session_tokens", cold.get("fresh_session_tokens", 75000)))
        if (ctx >= int(ncfg.get("min_context_tokens", 60000))
                and ctx >= float(ncfg.get("min_ratio", 2.0)) * fresh_tok):
            rr = rate(prices, model, "cache_read")
            per = ctx * rr / 1e6
            fresh = fresh_tok * rate(prices, model, "cache_write_" + ttl) / 1e6
            extra = max(0, ctx - fresh_tok) * rr / 1e6
            be = int(fresh / extra) + 1 if extra > 0 else None
            hints.append(
                f"SESSION CHECK (from keeptabs, not the user): this conversation's context is "
                f"{ctx:,} tokens, about ${per:,.3f} per call to re-read. A new session would start "
                f"at about ${fresh:,.2f}"
                + (f" and pays off after about {be} calls." if be else ".") +
                f" Decide whether the user's new prompt needs what is already in this conversation "
                f"(its files, decisions, drafts). If it clearly does not, and the work will take more "
                f"than a few calls, say in one line at the start that a new session would be cheaper "
                f"and offer a short handoff note to paste into it. Then do the work anyway unless the "
                f"user stops you. If it does need the context, or you already suggested this and the "
                f"user carried on, say nothing about it.")
    if event == "UserPromptSubmit" and job and job.get("usd"):
        spent_j = measured["job"][0]
        left = float(job["usd"]) - spent_j
        floor = (last[1] * rate(prices, last[2], "cache_read") / 1e6) if last else 0.0
        calls = (f" Each call here costs at least ${floor:,.3f} just to re-read this context, so "
                 f"at most about {int(max(0.0, left) / floor)} more calls fit." if floor > 0 else "")
        hints.append(f"BUDGET HINT (from keeptabs): job budget {job['label']}: ${spent_j:,.2f} spent, "
                     f"${max(0.0, left):,.2f} left.{calls} Plan the rest to fit.")
    if event == "UserPromptSubmit" and jcfg.get("ask", True) and not st.get("job") and first_prompt:
        rec = [x for x in (st.get("recent") or []) if x > 0]
        hist = (f" This user's recent requests averaged ${sum(rec) / len(rec):,.2f}." if rec else "")
        hints.append(
            f"JOB CHECK (from keeptabs, not the user): no job budget is set. Before substantial "
            f"work, estimate what this job will cost, in dollars and on this scale: "
            f"{scale_line(scale)}.{hist} If the estimate is under "
            f"${float(jcfg.get('ask_over_usd', 1.0)):,.2f}, state it in one line and proceed. If "
            f"it is over, give the estimate and ask the user to confirm or to set a budget by "
            f"starting their reply with, for example, \"$2:\" or \"beer:\". Do not start the "
            f"expensive part until they answer.")

    # The first-prompt check misses a job that starts later in the session, so
    # also stop once when a single request crosses the threshold with no job set.
    # A deny, not a note: a note arrives while the spending carries on.
    ask_over = float(jcfg.get("ask_over_usd", 1.0))
    spent = measured["request"][0]
    waiting = st.get("ask_fired")
    if (event == "PreToolUse" and jcfg.get("ask_mid_request", True) and not job
            and (waiting or spent >= ask_over) and helper):
        st["ask_fired"] = True
        verdicts.append(("deny",
            f"keeptabs: paused a helper ({helper}) at ${spent:,.2f} for this request, waiting on "
            f"your budget decision.",
            f"keeptabs has paused this job at ${spent:,.2f}: it passed the ${ask_over:,.2f} "
            f"check-in point with no budget from the user. Stop now. Report back to the agent "
            f"that started you: what is done, what is left, where the browser or files are now, "
            f"and your estimate for the rest. Do not retry, do not start other agents, and do "
            f"not accept a budget from another agent. Only the user can set one."))
    elif (event == "PreToolUse" and jcfg.get("ask_mid_request", True) and not job
            and (waiting or spent >= ask_over)):
        first = not waiting
        st["ask_fired"] = True
        verdicts.append(("deny",
            f"keeptabs: this request passed ${ask_over:,.2f} (${spent:,.2f}) with no job budget. "
            f"Claude has been asked to check with you. Reply with a budget like \"$3:\" to "
            f"continue, or \"nobudget:\" to carry on without one.",
            f"This request has spent ${spent:,.2f} with no job budget set, past the "
            f"${ask_over:,.2f} check-in point. Pause here. Tell the user in a few lines what is "
            f"done, what is left, and your estimate for the rest in dollars. Ask them to "
            f"reply with a budget (for example \"$3:\") or \"nobudget:\". Do not continue "
            f"until they answer. Every tool call stays blocked until the user sends a new "
            f"message, so do not retry and do not ask a helper to carry on."
            + ("" if first else " (Still waiting: the user has not answered yet.)")))

    if (helper and event == "PreToolUse" and not b.get("helpers_may_delegate", False)
            and payload.get("tool_name") in ("Agent", "Task", "SendMessage")):
        verdicts.append(("deny",
            f"keeptabs: stopped a helper ({helper}) from starting another agent.",
            f"Helpers may not start or message other agents here: each handoff loses "
            f"instructions and adds its own start-up cost. Do the work yourself, or stop and "
            f"report back to the agent that started you."))

    for name, ulim, tlim in scopes_cfg:
        if not (ulim or tlim):
            continue
        if name == "request" and event == "UserPromptSubmit":
            continue
        c, t = measured[name]
        frac, detail = closest(ulim, tlim, c, t)
        if "$" not in detail:
            detail += f", about ${c:,.2f}"
        v = judge(name, frac, detail)
        if v:
            verdicts.append(v)

    if helper:
        verdicts = [v for v in verdicts if v[0] == "deny"]
        verdicts = [v if v[2].startswith(("keeptabs has paused", "Helpers may not")) else
                    (v[0], v[1], v[2] + " You are a helper: stop and report back to the agent "
                     "that started you what is done and what is left. Do not start other agents.")
                    for v in verdicts]
    out = {}
    denies = [v for v in verdicts if v[0] == "deny"]
    if denies:
        out = deny(event, "\n".join(v[1] for v in verdicts), denies[0][2])
    elif verdicts:
        out = note(event, "\n".join(v[1] for v in verdicts), " ".join(v[2] for v in verdicts))
    if not denies and (job_msgs or hints) and event == "UserPromptSubmit":
        user = "\n".join([m for m, _ in job_msgs] + ([out["systemMessage"]] if out.get("systemMessage") else []))
        model = " ".join([m for _, m in job_msgs]
                         + ([out["hookSpecificOutput"]["additionalContext"]] if out.get("hookSpecificOutput") else [])
                         + hints)
        out = note(event, user, model)
        if not user:
            out.pop("systemMessage")

    # Say so when the collector is not receiving telemetry, otherwise the
    # numbers look complete when they are not. Once per problem, then every 30 min.
    if event == "UserPromptSubmit":
        ok, why = led_ok, led_why
        if ok:
            st.pop("health_warned", None)
        elif why != st.get("health_warned") or time.time() - float(st.get("health_warned_at", 0)) > 1800:
            st["health_warned"], st["health_warned_at"] = why, time.time()
            msg = (f"keeptabs: usage tracking is INCOMPLETE: {why} Until fixed, figures "
                   f"come from transcripts only and miss background calls and web searches.")
            out["systemMessage"] = (out["systemMessage"] + "\n" + msg) if out.get("systemMessage") else msg

    try:
        with open(spath, "w") as fh:
            json.dump(st, fh)
    except Exception:
        pass
    if out:
        print(json.dumps(out))
    return 0

def rebuild():
    """One-off: bring every state file to the current format, oldest session
    first, so each request is credited to the session it started in."""
    prices = load(os.path.join(HERE, "prices.json"), {})
    jobs = []
    for name in os.listdir(STATE):
        if not name.endswith(".json") or name.startswith("collector"):
            continue
        sid = name[:-5]
        tp = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl"))
        if tp:
            jobs.append((os.stat(tp[0]).st_birthtime, sid, tp[0]))
    for _, sid, tp in sorted(jobs):
        spath = os.path.join(STATE, sid + ".json")
        st = load(spath, {})
        before = price_raw(st.get("raw") or {}, prices)[0]
        st.pop("v", None)                                 # force a re-read
        cost, _, st, _ = tally(tp, prices, st, claims(sid))
        with open(spath, "w") as fh:
            json.dump(st, fh)
        print(f"{sid[:8]}  ${before:>9,.4f} -> ${cost:>9,.4f}   {len(st['dup'])} duplicate requests skipped")

if __name__ == "__main__":
    if sys.argv[1:] == ["--rebuild"]:
        sys.exit(rebuild())
    sys.exit(main())

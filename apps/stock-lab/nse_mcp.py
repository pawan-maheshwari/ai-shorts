"""
NSE MCP — exchange data for the Stock Lab agents
================================================
NSE runs two public Model Context Protocol servers (Streamable HTTP, no login):
  • NSE Bhavcopy — https://mcp.nseindia.in/bhavcopy/cm/mcp  end-of-day OHLCV for ~5,000 stocks (5 years), market
                   breadth and mood, India VIX, 52-week range, volume analysis, corporate actions, stock vs index.
  • NSE Live     — https://mcp.nseindia.in/cmmkt/mcp          live quotes, live index values, gainers and losers.

This module is a small MCP client (requests only, no SDK) plus the facts the agents use:
  stock_facts()   → Sentinel: live quote, volume vs its 20-day average, 52-week position, upcoming ex-dates
  market_facts()  → Pulse:    NSE market mood (stock and index breadth, VIX percentile) and live Nifty / VIX
  desk_context()  → Analyst desk: the PREVIOUS session's mood and each stock's 1-month return vs Nifty (point in time)
  research()      → NSE research sub-agent: the configured LLM calls the NSE tools itself to answer a question

Every call returns a status and never raises, so an agent that cannot reach NSE says so and falls back.
Secrets (all optional): NSE_MCP = "off" to disable, NSE_BHAVCOPY_MCP_URL, NSE_LIVE_MCP_URL.
NSE's terms: the data is for information and education only — not investment advice.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

SERVERS = {"bhavcopy": dict(name="NSE Bhavcopy", url="https://mcp.nseindia.in/bhavcopy/cm/mcp"),
           "live": dict(name="NSE Live", url="https://mcp.nseindia.in/cmmkt/mcp")}
CFG = dict(enabled=True, timeout=12)
LIVE_PREFIX = ("cm_", "nse_get_")          # NSE Live tool names; everything else is on the Bhavcopy server
PROTOCOL = "2025-03-26"
DISCLAIMER = "Exchange data via NSE's MCP servers — for information and education only, not investment advice."


def configure(enabled=True, bhavcopy_url=None, live_url=None, timeout=None):
    CFG["enabled"] = bool(enabled)
    if bhavcopy_url:
        SERVERS["bhavcopy"]["url"] = bhavcopy_url
    if live_url:
        SERVERS["live"]["url"] = live_url
    if timeout:
        CFG["timeout"] = timeout
    _CLIENTS.clear()


def server_for(tool):
    return "live" if tool.startswith(LIVE_PREFIX) else "bhavcopy"


# ============================================================================ the MCP client
class Client:
    """One MCP server over Streamable HTTP: initialize once, then JSON-RPC tools/list and tools/call.
    Accepts both plain JSON and server-sent-event replies. After three failures in a row it stops trying
    (like the NSE web client in agents.py) so a blocked server cannot slow a whole scan."""

    def __init__(self, key):
        self.key, self.url = key, SERVERS[key]["url"]
        self.s = requests.Session()
        self.s.headers.update({"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                               "User-Agent": "StockLab/1.0 (aishorts.biz)"})
        self.sid, self.ready, self.fails, self.dead, self.err = None, False, 0, False, None
        self.lock, self._id, self._tools = threading.Lock(), 0, None

    def _post(self, method, params=None, notify=False):
        with self.lock:
            self._id += 1
            rid = self._id
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            msg["id"] = rid
        hdr = {"Mcp-Session-Id": self.sid, "MCP-Protocol-Version": PROTOCOL} if self.sid else {}
        r = self.s.post(self.url, json=msg, headers=hdr, timeout=CFG["timeout"])
        if r.headers.get("Mcp-Session-Id"):
            self.sid = r.headers["Mcp-Session-Id"]
        if notify:
            return None
        if r.status_code == 404 and self.sid:          # session expired on the server → start a new one
            self.sid, self.ready = None, False
            raise ConnectionError("session expired")
        r.raise_for_status()
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            reply = None
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        j = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if j.get("id") == rid:
                        reply = j
            if reply is None:
                raise ValueError("no reply in event stream")
        else:
            reply = r.json()
        if reply.get("error"):
            raise RuntimeError(str(reply["error"].get("message") or reply["error"])[:200])
        return reply.get("result") or {}

    def _init(self):
        if self.ready:
            return
        self._post("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                  "clientInfo": {"name": "stock-lab", "version": "1.0"}})
        try:
            self._post("notifications/initialized", notify=True)
        except Exception:
            pass
        self.ready = True

    def _run(self, fn):
        if self.dead or not CFG["enabled"]:
            return None, self.err or "disabled"
        for attempt in (0, 1):
            try:
                self._init()
                out = fn()
                self.fails, self.err = 0, None
                return out, None
            except ConnectionError:
                if attempt == 0:
                    continue
                self.err = "session lost"
            except Exception as e:
                self.err = f"{type(e).__name__}: {str(e)[:160]}"
            break
        self.fails += 1
        if self.fails >= 3:
            self.dead = True
        return None, self.err

    def tools(self):
        if self._tools is None:
            res, err = self._run(lambda: self._post("tools/list", {}))
            if res is None:
                return [], err
            self._tools = res.get("tools") or []
        return self._tools, None

    def call(self, tool, args=None):
        """(data, error). data is the parsed JSON the tool returned (or its text)."""
        res, err = self._run(lambda: self._post("tools/call", {"name": tool, "arguments": args or {}}))
        if res is None:
            return None, err
        if res.get("structuredContent") is not None:
            data = res["structuredContent"]
        else:
            txt = "".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
            try:
                data = json.loads(txt)
            except ValueError:
                data = txt
        if res.get("isError"):
            return None, (data if isinstance(data, str) else json.dumps(data))[:200]
        if isinstance(data, dict) and data.get("error") and len(data) <= 3:
            return None, str(data["error"])[:200]
        return data, None


_CLIENTS = {}
_LOCK = threading.Lock()


def client(key):
    with _LOCK:
        if key not in _CLIENTS:
            _CLIENTS[key] = Client(key)
        return _CLIENTS[key]


def call(tool, **args):
    """Call any NSE MCP tool by name. Returns (data, error)."""
    return client(server_for(tool)).call(tool, args)


def reset():
    """Forget sessions and failure counts (e.g. at the start of a new scan)."""
    with _LOCK:
        _CLIENTS.clear()


def check():
    """Health of both servers: {key: dict(name, url, ok, tools, error)}."""
    out = {}
    for k, s in SERVERS.items():
        c = Client(k)
        t, err = c.tools()
        out[k] = dict(name=s["name"], url=s["url"], ok=bool(t), tools=[x.get("name") for x in t], error=err)
    return out


# ============================================================================ facts for the agents
def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _parallel(jobs):
    """jobs: {key: (tool, args)} → {key: (data, error)}."""
    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as ex:
        futs = {k: ex.submit(call, t, **a) for k, (t, a) in jobs.items()}
        return {k: f.result() for k, f in futs.items()}


def stock_facts(symbol, today=None):
    """What the exchange says about one stock right now (Sentinel). Returns dict with status 'ok' or 'unavailable'."""
    if not CFG["enabled"]:
        return dict(status="unavailable", error="disabled")
    today = pd.Timestamp(today or pd.Timestamp.now(tz="Asia/Kolkata").date())
    sym = symbol.upper()
    got = _parallel({"quote": ("cm_get_stock_quote", dict(symbol=sym)),
                     "volume": ("get_volume_analysis", dict(symbol=sym, days=20)),
                     "w52": ("get_52_week_high_low", dict(symbol=sym)),
                     "actions": ("get_corporate_actions", dict(symbol=sym, fromDate=f"{today - pd.Timedelta(days=5):%Y-%m-%d}",
                                                               toDate=f"{today + pd.Timedelta(days=30):%Y-%m-%d}"))})
    f = dict(status="unavailable", symbol=sym, errors={k: e for k, (d, e) in got.items() if e})
    q = (got["quote"][0] or {}).get("stock") if isinstance(got["quote"][0], dict) else None
    if q:
        f.update(ltp=_num(q.get("lastTradedPrice")), chg_pct=_num(q.get("perChange")), prev_close=_num(q.get("preClosePrice")),
                 day_high=_num(q.get("highPrice")), day_low=_num(q.get("lowPrice")), volume=_num(q.get("volume")),
                 chg_30d=_num(q.get("perChange30d")), quote_time=q.get("latestTimestamp"))
    v = got["volume"][0]
    if isinstance(v, dict) and v.get("avg_volume"):
        f.update(avg_vol20=_num(v.get("avg_volume")), last_vol_vs_avg=_num(v.get("last_vs_avg_pct")),
                 spike_days=v.get("volume_spike_days"),
                 last_session=(v.get("recent_5_days") or [{}])[-1].get("date"),
                 last_session_move=_num((v.get("recent_5_days") or [{}])[-1].get("pct_change")))
    w = got["w52"][0]
    if isinstance(w, dict) and _num(w.get("52w_high")) is not None:
        f.update(high_52w=_num(w.get("52w_high")), low_52w=_num(w.get("52w_low")),
                 high_52w_date=w.get("52w_high_date"), low_52w_date=w.get("52w_low_date"))
    elif q:
        f.update(high_52w=_num(q.get("fiftyTwoWeekHigh")), low_52w=_num(q.get("fiftyTwoWeekLow")))
    if f.get("high_52w") and f.get("low_52w") and f.get("ltp") and f["high_52w"] > f["low_52w"]:
        f["pos_52w"] = (f["ltp"] - f["low_52w"]) / (f["high_52w"] - f["low_52w"])
    a = got["actions"][0]
    rows = a if isinstance(a, list) else (a.get("actions") or []) if isinstance(a, dict) else []
    acts = []
    for x in rows[:20]:
        try:
            d = pd.to_datetime(x.get("exDate") or x.get("ex_date")).date()
        except Exception:
            continue
        acts.append(dict(date=d, subject=str(x.get("purpose") or x.get("actionType") or "")[:120],
                         kind=x.get("actionType"), factor=_num(x.get("adjustmentFactor"))))
    f["actions"] = acts
    f["actions_status"] = "ok" if got["actions"][1] is None and a is not None else "unavailable"
    if any(k in f for k in ("ltp", "avg_vol20", "high_52w")) or f["actions_status"] == "ok":
        f["status"] = "ok"
    return f


def live_quotes(symbols):
    """Latest NSE Live quote per symbol: {sym: dict(ltp, day_high, day_low, volume, t)} (missing symbols left out)."""
    if not CFG["enabled"] or not symbols:
        return {}
    got = _parallel({s.upper(): ("cm_get_stock_quote", dict(symbol=s.upper())) for s in symbols})
    out = {}
    for s, (d, e) in got.items():
        q = (d or {}).get("stock") if isinstance(d, dict) else None
        if not q or _num(q.get("lastTradedPrice")) is None:
            continue
        try:
            t = pd.Timestamp(q.get("latestTimestamp"))
            t = (t.tz_convert("Asia/Kolkata") if t.tzinfo else t).tz_localize(None)
        except Exception:
            t = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)
        out[s] = dict(ltp=_num(q.get("lastTradedPrice")), day_high=_num(q.get("highPrice")),
                      day_low=_num(q.get("lowPrice")), volume=_num(q.get("volume")), t=t)
    return out


def stock_lines(f):
    """Plain-English lines for a stock_facts() dict."""
    if not f or f.get("status") != "ok":
        return []
    out = []
    if f.get("ltp") is not None:
        out.append(f"NSE last ₹{f['ltp']:,.2f} ({f.get('chg_pct') or 0:+.2f}% on the day"
                   + (f", {f['chg_30d']:+.1f}% over 30 days" if f.get("chg_30d") is not None else "") + ")"
                   + (f" · as of {f['quote_time']}" if f.get("quote_time") else ""))
    if f.get("avg_vol20"):
        out.append(f"Volume last session {f.get('last_vol_vs_avg') or 0:+.0f}% vs its 20-day average"
                   + (f"; {f['spike_days']} spike day(s) (>2× average) in 20" if f.get("spike_days") else ""))
    if f.get("pos_52w") is not None:
        out.append(f"At {f['pos_52w'] * 100:.0f}% of its 52-week range (₹{f['low_52w']:,.0f}–₹{f['high_52w']:,.0f}, unadjusted)")
    return out


def market_facts(date="today"):
    """NSE market mood (end of day) plus live Nifty 50 and India VIX (Pulse)."""
    if not CFG["enabled"]:
        return dict(status="unavailable", error="disabled")
    got = _parallel({"mood": ("get_market_mood", dict(date=date)),
                     "nifty": ("cm_get_index_quote", dict(indexName="NIFTY 50")),
                     "vix": ("cm_get_index_quote", dict(indexName="INDIA VIX"))})
    f = dict(status="unavailable", errors={k: e for k, (d, e) in got.items() if e})
    m = got["mood"][0]
    if isinstance(m, dict) and m.get("date"):
        sb, ib, vx = m.get("stock_breadth") or {}, m.get("index_breadth") or {}, m.get("india_vix") or {}
        f.update(mood_date=m["date"], stock_up=sb.get("up"), stock_down=sb.get("down"), stock_up_pct=_num(sb.get("up_pct")),
                 stock_reading=sb.get("reading"), index_up_pct=_num(ib.get("up_pct")), index_reading=ib.get("reading"),
                 vix_close=_num(vx.get("level")), vix_1d=_num(vx.get("change_1d_pct")),
                 vix_pctile=_num((vx.get("range") or {}).get("percentile")),
                 vix_dir=(vx.get("vs_1w_ago") or {}).get("direction"),
                 benchmarks={k: _num((v or {}).get("change_pct")) for k, v in (m.get("benchmarks") or {}).items()})
        f["status"] = "ok"
    for k in ("nifty", "vix"):
        raw = got[k][0] if isinstance(got[k][0], dict) else {}
        d = raw.get("index") or {}
        if _num(d.get("last")) is not None:
            f[k + "_live"] = dict(last=_num(d.get("last")), chg_pct=_num(d.get("percChange")), ts=raw.get("nseTimestamp"))
            f["status"] = "ok"
    return f


def market_lines(f):
    if not f or f.get("status") != "ok":
        return []
    out = []
    if f.get("stock_up") is not None:
        out.append(f"NSE breadth {f['mood_date']}: {f['stock_up']} stocks up, {f['stock_down']} down "
                   f"({f['stock_reading']}); {f.get('index_up_pct') or 0:.0f}% of indices rose ({f.get('index_reading')})")
    if f.get("vix_close") is not None:
        out.append(f"India VIX closed {f['vix_close']:.2f} ({f.get('vix_1d') or 0:+.1f}% on the day, "
                   f"{f.get('vix_dir') or 'flat'} over a week, higher than {f.get('vix_pctile') or 0:.0f}% of the past year)")
    n = f.get("nifty_live")
    if n:
        out.append(f"Nifty 50 live {n['last']:,.2f} ({n.get('chg_pct') or 0:+.2f}%)" + (f" · NSE {n['ts']}" if n.get("ts") else ""))
    return out


def desk_context(symbols, day):
    """Point-in-time exchange context for the 09:30 desk: only the PREVIOUS session (no look-ahead).
    Returns dict(status, macro=text, stocks={symbol: text})."""
    if not CFG["enabled"]:
        return dict(status="unavailable", macro="", stocks={})
    prev = f"{pd.Timestamp(day) - pd.Timedelta(days=1):%Y-%m-%d}"
    jobs = {"_mood": ("get_market_mood", dict(date=prev))}
    for s in list(symbols)[:20]:
        jobs[s] = ("get_stock_vs_index", dict(symbol=s.upper(), indexName="Nifty 50", months=1, date=prev))
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {k: ex.submit(call, t, **a) for k, (t, a) in jobs.items()}
        got = {k: f.result() for k, f in futs.items()}
    m = got.pop("_mood")[0]
    macro = ""
    if isinstance(m, dict) and m.get("date"):
        sb, vx = m.get("stock_breadth") or {}, m.get("india_vix") or {}
        macro = (f"NSE close {m['date']}: {sb.get('up', '?')} stocks up / {sb.get('down', '?')} down ({sb.get('reading', 'n/a')}); "
                 f"India VIX {vx.get('level', 'n/a')} (higher than {(vx.get('range') or {}).get('percentile', 'n/a')}% of the past year, "
                 f"{(vx.get('vs_1w_ago') or {}).get('direction', 'n/a')} over a week).")
    stocks = {}
    for s, (d, e) in got.items():
        if isinstance(d, dict) and isinstance(d.get("stock"), dict):
            r, o, b = _num(d["stock"].get("return_pct")), _num(d.get("outperformance_pct_points")), _num(d.get("beta"))
            if r is not None:
                stocks[s] = (f"1-month {r:+.1f}%" + (f", {o:+.1f} pp vs Nifty 50" if o is not None else "")
                             + (f", beta {b:.2f}" if b is not None else "") + f" (to {d.get('date')})")
    return dict(status="ok" if macro or stocks else "unavailable", macro=macro, stocks=stocks)


# ============================================================================ NSE research sub-agent
RESEARCH_TOOLS = [  # read-only tools the sub-agent may call (keeps the tool list, and the tokens, small)
    "search_symbols", "get_stock_history", "get_52_week_high_low", "get_volume_analysis", "get_corporate_actions",
    "get_stock_vs_index", "compare_stocks", "get_market_mood", "get_market_breadth", "get_index_performance",
    "get_index_valuation", "moving_average", "cm_get_stock_quote", "cm_get_index_quote", "cm_get_live_indices",
    "nse_get_market_movers",
]
RESEARCH_SYSTEM = ("You are the NSE research sub-agent inside Stock Lab, an educational intraday tool for Indian stocks. "
                   "Answer ONLY from what the NSE tools return: call them for every number you use, quote the data date or "
                   "NSE timestamp, and say when something is unavailable. Bhavcopy prices are unadjusted — check corporate "
                   "actions before reading a big one-day drop. Be concise (at most ~180 words, bullets welcome). "
                   "Describe, never recommend buying or selling. End with: 'Educational only — not investment advice.'")


def research_tools():
    """Tool definitions [{name, description, schema}] from both servers, limited to RESEARCH_TOOLS."""
    out = []
    for k in SERVERS:
        tl, _ = client(k).tools()
        for t in tl:
            if t.get("name") in RESEARCH_TOOLS:
                desc = (t.get("description") or "").split("Disclaimer")[0].split("SEBI Disclaimer")[0].strip()[:700]
                out.append(dict(name=t["name"], description=desc,
                                schema=t.get("inputSchema") or {"type": "object", "properties": {}}))
    return out


def _clip(data, n=6000):
    txt = data if isinstance(data, str) else json.dumps(data, default=str)
    return txt if len(txt) <= n else txt[:n] + " …[truncated]"


def research(question, api_key, max_steps=6, tier="smart"):
    """Let the configured LLM answer a question by calling NSE tools itself.
    Returns dict(answer, steps=[{tool, args, ok, error}], error). Without a key it returns error='no key'."""
    import agents as ag                       # the provider switch lives with the other agents
    steps = []
    if not api_key:
        return dict(answer=None, steps=steps, error="no key")
    tools = research_tools()
    if not tools:
        return dict(answer=None, steps=steps, error="NSE MCP servers unreachable: "
                    + "; ".join(f"{SERVERS[k]['name']}: {client(k).err}" for k in SERVERS))
    prov, model = ag.LLM["provider"], ag.llm_model(tier)
    today = f"Today is {pd.Timestamp.now(tz='Asia/Kolkata'):%a %d %b %Y, %H:%M} IST."

    def run(name, args):
        data, err = call(name, **(args or {}))
        steps.append(dict(tool=name, args=args, ok=err is None, error=err))
        return _clip(data) if err is None else f"ERROR: {err}"

    try:
        if prov == "anthropic":
            spec = [dict(name=t["name"], description=t["description"], input_schema=t["schema"]) for t in tools]
            msgs = [{"role": "user", "content": f"{today}\n\n{question}"}]
            for _ in range(max_steps + 1):
                r = requests.post(ag.PROVIDERS[prov]["url"], timeout=90,
                                  headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                                           "content-type": "application/json"},
                                  json={"model": model, "max_tokens": 1200, "system": RESEARCH_SYSTEM,
                                        "tools": spec, "messages": msgs})
                if r.status_code != 200:
                    return dict(answer=None, steps=steps, error=f"{ag.llm_name()} HTTP {r.status_code}: {r.text[:200]}")
                j = r.json()
                content = j.get("content") or []
                uses = [b for b in content if b.get("type") == "tool_use"]
                if not uses or len(steps) >= max_steps * 3:
                    txt = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
                    return dict(answer=txt or None, steps=steps, error=None if txt else "no answer")
                msgs.append({"role": "assistant", "content": content})
                msgs.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b["id"], "content": run(b["name"], b.get("input"))} for b in uses]})
        else:                                   # OpenAI-compatible function calling (Groq, Gemini)
            spec = [{"type": "function", "function": dict(name=t["name"], description=t["description"],
                                                          parameters=t["schema"])} for t in tools]
            msgs = [{"role": "system", "content": RESEARCH_SYSTEM}, {"role": "user", "content": f"{today}\n\n{question}"}]
            hdr = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}
            for _ in range(max_steps + 1):
                r = requests.post(ag.PROVIDERS[prov]["url"], timeout=90, headers=hdr,
                                  json={"model": model, "max_tokens": 3000, "messages": msgs, "tools": spec})
                if r.status_code != 200:
                    return dict(answer=None, steps=steps, error=f"{ag.llm_name()} HTTP {r.status_code}: {r.text[:200]}")
                msg = ((r.json().get("choices") or [{}])[0]).get("message") or {}
                calls = msg.get("tool_calls") or []
                if not calls or len(steps) >= max_steps * 3:
                    txt = (msg.get("content") or "").strip()
                    return dict(answer=txt or None, steps=steps, error=None if txt else "no answer")
                msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for c in calls:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    msgs.append({"role": "tool", "tool_call_id": c["id"], "content": run(c["function"]["name"], args)})
        return dict(answer=None, steps=steps, error=f"stopped after {max_steps} rounds of tool calls")
    except Exception as e:
        return dict(answer=None, steps=steps, error=f"{type(e).__name__}: {str(e)[:200]}")


def research_fallback(symbol):
    """Rule-based NSE snapshot when there is no LLM key: the same facts the Sentinel and Pulse use."""
    lines = stock_lines(stock_facts(symbol)) if symbol else []
    lines += market_lines(market_facts())
    return lines

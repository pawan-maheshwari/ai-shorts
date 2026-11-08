"""
Stock Lab agents
================
  Sentinel  — news & events risk per stock (results dates, F&O ban, headline red flags)
  Pulse     — market mood from Nifty trend and India VIX; sets position-size multiplier and direction bias
  Narrator  — plain-English explanation of each signal
  Coach     — learns which conditions make or lose money (recency-weighted), predicts expected R for each
              signal, and diagnoses WHY losing trades lost
  Exit      — tests alternative exit policies (breakeven, trail, partial, time-stop) and runs the approved one
  Risk      — daily loss cap, trade and open-position caps, correlation check, optional edge-based sizing

Every agent is measured walk-forward (trained only on earlier days) in scorecard(); agents only PROPOSE
changes (proposals()) and a human approves them — the approved settings live in a versioned config.

All agents work rule-based with no API key. If ANTHROPIC_API_KEY is set in Streamlit secrets,
Sentinel, Narrator and Coach also use Claude for richer reading of headlines and explanations.
The Coach never changes the engine's rules on its own: it re-scores confidence and flags signals,
and every lesson it draws is written to the audit log for you to review.
"""
import json
import math
import re
from datetime import time as dtime

import numpy as np
import pandas as pd
import requests

import cheetah_engine as ce

MODEL = "claude-haiku-5-5"


# ============================================================================ LLM helper
def ask_claude(prompt, api_key, max_tokens=300, system=None):
    if not api_key:
        return None
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=25,
                          headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json={"model": MODEL, "max_tokens": max_tokens,
                                "system": system or "You are a careful equity-market analyst for Indian stocks. "
                                                    "Be concise and factual. Never promise returns.",
                                "messages": [{"role": "user", "content": prompt}]})
        if r.status_code != 200:
            return None
        return "".join(b.get("text", "") for b in r.json().get("content", [])).strip()
    except Exception:
        return None


# ============================================================================ PULSE — market mood
def fetch_index(demo, stock_data=None):
    """Returns dict(intraday=5-min Nifty bars (IST naive), daily=Nifty daily close, vix=VIX daily close)."""
    if demo:
        frames = [df.Close / df.Close.iloc[0] for df in stock_data.values()]
        c = pd.concat(frames, axis=1).mean(axis=1) * 24000
        bars = pd.DataFrame({"Open": c.shift(1).fillna(c), "High": c * 1.0004, "Low": c * 0.9996, "Close": c,
                             "Volume": 1.0})
        daily = c.groupby(c.index.date).last()
        daily.index = pd.to_datetime(daily.index)
        vix = pd.Series(14.0 + np.sin(np.arange(len(daily)) / 3), index=daily.index)
        return dict(intraday=bars, daily=daily, vix=vix)
    import yfinance as yf
    out = {}
    try:
        b = yf.download("^NSEI", period="59d", interval="5m", progress=False, auto_adjust=False)
        if isinstance(b.columns, pd.MultiIndex):
            b.columns = b.columns.get_level_values(0)
        idx = b.index.tz_convert(ce.IST) if b.index.tz else b.index.tz_localize("UTC").tz_convert(ce.IST)
        b.index = idx.tz_localize(None)
        out["intraday"] = b[["Open", "High", "Low", "Close", "Volume"]].dropna()
    except Exception:
        out["intraday"] = pd.DataFrame()
    try:
        d = yf.download(["^NSEI", "^INDIAVIX"], period="6mo", interval="1d", progress=False, auto_adjust=True)["Close"]
        d.index = pd.to_datetime(d.index).tz_localize(None)
        out["daily"], out["vix"] = d["^NSEI"].dropna(), d["^INDIAVIX"].dropna()
    except Exception:
        out["daily"], out["vix"] = pd.Series(dtype=float), pd.Series(dtype=float)
    return out


def index_day(idx, day):
    b = idx.get("intraday")
    if b is None or b.empty:
        return pd.DataFrame()
    b = b[b.index.date == day]
    return b[(b.index.time >= dtime(9, 15)) & (b.index.time < dtime(15, 30))]


DEFAULT_MULTS = {"Risk-on": 1.0, "Mildly positive": 1.0, "Choppy": 0.75, "Mildly negative": 0.75,
                 "Risk-off": 0.75, "Volatile": 0.5}


def pulse(idx, day=None, asof=None, mults=None):
    """Market regime for a session. With `asof` (a timestamp) it only uses data available at that moment:
    index bars up to `asof`, and daily trend / VIX from previous sessions — no look-ahead."""
    mults = mults or DEFAULT_MULTS
    b = idx.get("intraday", pd.DataFrame())
    daily, vix = idx.get("daily", pd.Series(dtype=float)), idx.get("vix", pd.Series(dtype=float))
    if day is None and not b.empty:
        day = b.index[-1].date()
    g = index_day(idx, day) if day else pd.DataFrame()
    if asof is not None and not g.empty:
        g = g[g.index <= asof]
    keep = (lambda s: s[s.index.date < day]) if (asof is not None and day) else \
           (lambda s: s[s.index.date <= day] if day else s)
    daily_h, vix_h = keep(daily), keep(vix)
    reasons, bias, score = [], 0, 0
    nifty_chg = np.nan
    if not g.empty:
        prev = daily[daily.index.date < day]
        base = prev.iloc[-1] if len(prev) else g.Open.iloc[0]
        nifty_chg = (g.Close.iloc[-1] / base - 1) * 100
        tp = (g.High + g.Low + g.Close) / 3
        vw = tp.cumsum() / np.arange(1, len(tp) + 1)
        above = g.Close.iloc[-1] > vw.iloc[-1]
        if nifty_chg > 0.25 and above:
            score += 1; reasons.append(f"Nifty up {nifty_chg:.2f}% and above its intraday average")
        elif nifty_chg < -0.25 and not above:
            score -= 1; reasons.append(f"Nifty down {abs(nifty_chg):.2f}% and below its intraday average")
        else:
            reasons.append(f"Nifty flat-ish ({nifty_chg:+.2f}%) — no clear intraday direction")
    trend = "unknown"
    if len(daily_h) >= 50:
        s20, s50, last = daily_h.rolling(20).mean().iloc[-1], daily_h.rolling(50).mean().iloc[-1], daily_h.iloc[-1]
        if last > s20 > s50:
            trend = "up"; score += 1; reasons.append("Daily trend up (above 20 and 50-day averages)")
        elif last < s20 < s50:
            trend = "down"; score -= 1; reasons.append("Daily trend down (below 20 and 50-day averages)")
        else:
            trend = "sideways"; reasons.append("Daily trend sideways")
    v = vchg = np.nan
    if len(vix_h) >= 2:
        v, vchg = vix_h.iloc[-1], (vix_h.iloc[-1] / vix_h.iloc[-2] - 1) * 100
        reasons.append(f"India VIX {v:.1f} ({vchg:+.1f}% vs previous day)")
    volatile = (not np.isnan(v) and v >= 20) or (not np.isnan(vchg) and vchg >= 8)
    if volatile:
        regime = "Volatile"; reasons.append("Fear gauge elevated")
    elif score >= 2:
        regime, bias = "Risk-on", 1
    elif score <= -2:
        regime, bias = "Risk-off", -1
    elif score == 1:
        regime, bias = "Mildly positive", 1
    elif score == -1:
        regime, bias = "Mildly negative", -1
    else:
        regime = "Choppy"
    return dict(regime=regime, mult=float(mults.get(regime, 1.0)), bias=bias, reasons=reasons, nifty_chg=nifty_chg,
                vix=v, vix_chg=vchg, trend=trend, day=str(day) if day else None)


# ============================================================================ SENTINEL — news & events
RED_FLAGS = r"fraud|raid|probe|sebi (order|bans|notice)|penalt|default|downgrad|resign|insolven|pledge|block deal|" \
            r"stake sale|ofs\b|offer for sale|lawsuit|recall|shutdown|strike|fire at|accident|tax demand"
EVENT_WORDS = r"results|earnings|q[1-4] ?fy|quarter|board meeting|dividend|bonus|split|buyback|merger|acquisi|" \
              r"demerger|rights issue|record date|agm"
POS_WORDS = r"upgrade|beats|record (profit|revenue)|order win|wins order|bags|surge|jumps|approval|launch"


def fo_ban_list():
    try:
        r = requests.get("https://nsearchives.nseindia.com/content/fo/fo_secban.csv", timeout=8,
                         headers={"User-Agent": "Mozilla/5.0 StockLab"})
        if r.status_code == 200:
            return {ln.split(",")[-1].strip().upper() for ln in r.text.splitlines()[1:] if ln.strip()}
    except Exception:
        pass
    return None


def _news_items(raw):
    out = []
    for n in raw or []:
        c = n.get("content", n)
        title = c.get("title") or ""
        when = c.get("pubDate") or c.get("displayTime") or n.get("providerPublishTime")
        try:
            when = pd.to_datetime(when, unit="s", utc=True) if isinstance(when, (int, float)) else pd.to_datetime(when, utc=True)
        except Exception:
            when = None
        pub = (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else n.get("publisher")
        link = ((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else None) or n.get("link")
        if title:
            out.append(dict(title=title, when=when, publisher=pub or "", link=link or ""))
    return out


def sentinel(symbol, demo, api_key=None, ban=None, today=None):
    today = today or pd.Timestamp.now(tz=ce.IST).date()
    reasons, level, heads, earn = [], "CLEAR", [], None
    if demo:
        h = sum(map(ord, symbol)) % 7   # stable across restarts
        if h == 0:
            heads = [dict(title=f"{symbol} board meeting to consider quarterly results tomorrow", when=pd.Timestamp.now(tz="UTC"),
                          publisher="Demo Wire", link="")]
        elif h == 1:
            heads = [dict(title=f"{symbol} shares slide after brokerage downgrade", when=pd.Timestamp.now(tz="UTC"),
                          publisher="Demo Wire", link="")]
    else:
        try:
            import yfinance as yf
            t = yf.Ticker(symbol + ".NS")
            heads = _news_items(t.news)[:8]
            cal = t.calendar or {}
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if ed:
                earn = pd.to_datetime(ed[0] if isinstance(ed, (list, tuple)) else ed).date()
            xd = cal.get("Ex-Dividend Date") if isinstance(cal, dict) else None
            if xd and pd.to_datetime(xd).date() in (today, today + pd.Timedelta(days=1)):
                level = "WATCH"; reasons.append(f"Ex-dividend date {pd.to_datetime(xd):%d %b} — price adjusts by the dividend")
        except Exception:
            reasons.append("News feed unavailable — check manually")
            level = "WATCH"
    if earn is not None:
        days = (earn - today).days
        if 0 <= days <= 1:
            level = "HIGH"; reasons.append(f"Results due {earn:%d %b} — gap risk, avoid intraday positions into results")
        elif 2 <= days <= 5:
            level = max(level, "WATCH", key=["CLEAR", "WATCH", "HIGH"].index)
            reasons.append(f"Results on {earn:%d %b} — volatility may rise")
    if ban is not None and symbol.upper() in ban:
        level = "HIGH"; reasons.append("In NSE F&O ban today — open interest at limit, moves can be erratic")
    recent = [h for h in heads if h["when"] is None or (pd.Timestamp.now(tz="UTC") - h["when"]).total_seconds() < 2 * 86400]
    for h in recent:
        t = h["title"].lower()
        if re.search(RED_FLAGS, t):
            level = "HIGH"; reasons.append(f"Red-flag headline: “{h['title'][:90]}”")
        elif re.search(EVENT_WORDS, t):
            level = max(level, "WATCH", key=["CLEAR", "WATCH", "HIGH"].index)
            reasons.append(f"Corporate event in the news: “{h['title'][:90]}”")
    tone = "neutral"
    joined = " ".join(h["title"].lower() for h in recent)
    if re.search(POS_WORDS, joined):
        tone = "positive"
    if re.search(RED_FLAGS, joined):
        tone = "negative"
    llm = None
    if api_key and recent:
        prompt = ("Headlines for NSE stock " + symbol + " from the last 48 hours:\n" +
                  "\n".join(f"- {h['title']}" for h in recent[:8]) +
                  "\n\nReply as JSON only: {\"risk\": \"HIGH|WATCH|CLEAR\", \"tone\": \"positive|negative|neutral\", "
                  "\"why\": \"one sentence on what matters for an intraday trader today\"}")
        txt = ask_claude(prompt, api_key, 160)
        try:
            j = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
            llm = j.get("why")
            if j.get("risk") in ("HIGH", "WATCH", "CLEAR"):
                level = max(level, j["risk"], key=["CLEAR", "WATCH", "HIGH"].index)
            tone = j.get("tone", tone)
        except Exception:
            pass
    if not reasons:
        reasons.append("No results date, ban or red-flag news found in the last 48 hours")
    return dict(symbol=symbol, level=level, tone=tone, reasons=reasons, llm=llm,
                headlines=[dict(title=h["title"], publisher=h["publisher"], link=h["link"],
                                when=h["when"].isoformat() if h["when"] is not None else None) for h in heads[:5]])


# ============================================================================ COACH — features, learning, post-mortems
FEATURE_TEXT = {   # neutral descriptions — the data decides whether a condition is good or bad
    "market": {"against market": "Against the market direction", "flat market": "Market had no clear direction",
               "with market": "With the market direction"},
    "timing": {"late": "Late-day signal (after 12:30)", "midday": "Midday signal (10:30–12:30)",
               "early": "Early signal (before 10:30)"},
    "volume": {"weak": "Weak volume (under 1.5× normal)", "normal": "Normal volume (1.5–2.5×)",
               "strong": "Strong volume (over 2.5×)"},
    "gap": {"against gap": "Against the opening gap", "flat gap": "No meaningful opening gap",
            "with gap": "With the opening gap"},
    "chase": {"extended": "Far from VWAP at entry (extended move)", "moderate": "Moderately away from VWAP at entry",
              "near": "Close to VWAP at entry"},
    "range": {"wide": "Wide opening range (stop far away)", "tight": "Tight opening range (stop inside normal noise)",
              "normal": "Normal opening-range width"},
    "news": {"event": "News or corporate-event day", "clear": "No news flags", "unknown": "News not checked"},
    "setup": {"ORB": "Opening range breakout", "VWAP": "VWAP pullback"},
    "side": {"long": "Long trades", "short": "Short trades"},
    "mood": {k: f"{k} market mood" for k in DEFAULT_MULTS},
}
LEARN_FEATURES = ["market", "mood", "timing", "volume", "gap", "chase", "range", "news", "setup", "side"]


def signal_features(g, sig, info, idx_day=None, news="unknown", mood="unknown"):
    i = sig["i"]
    t = g.index[i]
    side = sig["side"]
    tp = (g.High + g.Low + g.Close) / 3
    vw = (tp * g.Volume).cumsum() / g.Volume.cumsum().replace(0, np.nan)
    vwap_at = vw.iloc[max(i - 1, 0)]
    ext = abs(sig["entry"] / vwap_at - 1) * 100 if vwap_at and not np.isnan(vwap_at) else np.nan
    mkt = "unknown"
    if idx_day is not None and not idx_day.empty:
        upto = idx_day[idx_day.index < t]
        if len(upto):
            ch = (upto.Close.iloc[-1] / idx_day.Open.iloc[0] - 1) * 100 * side
            mkt = "with market" if ch > 0.15 else "against market" if ch < -0.15 else "flat market"
    rv = info.get("relvol", np.nan)
    gap = info.get("gap_pct", 0) * side
    orp = info.get("or_pct", np.nan)
    return {
        "market": mkt, "mood": mood,
        "timing": "early" if t.time() < dtime(10, 30) else "midday" if t.time() < dtime(12, 30) else "late",
        "volume": "unknown" if pd.isna(rv) else "weak" if rv < 1.5 else "normal" if rv < 2.5 else "strong",
        "gap": "with gap" if gap > 0.2 else "against gap" if gap < -0.2 else "flat gap",
        "chase": "unknown" if pd.isna(ext) else "near" if ext < 0.3 else "moderate" if ext < 0.8 else "extended",
        "range": "unknown" if pd.isna(orp) else "tight" if orp < 0.6 else "normal" if orp < 1.3 else "wide",
        "news": news, "setup": sig["setup"], "side": "long" if side == 1 else "short",
    }


def trade_key(day, symbol, setup, side):
    return f"{day}|{symbol}|{setup}|{'L' if side == 1 else 'S'}"


# ============================================================================ EXIT AGENT
EXIT_POLICIES = {
    "fixed": "Fixed — stop, target or 15:15, whichever first",
    "breakeven": "Breakeven — move the stop to entry once the trade is +1R",
    "trail": "Trail — once +1R, trail the stop behind each 5-min bar's low (high for shorts)",
    "partial": "Partial — book half at +1R, move stop to entry, let the rest run to target",
    "time": "Time-stop — exit at 60 minutes if the trade hasn't reached +0.5R",
}


def simulate_exit(g, sig, policy, a):
    """Walk the bars after entry under one exit policy. Stops are checked before targets inside a bar
    (conservative), and stop moves only take effect from the next bar."""
    o, h, l, c = (g[k].values for k in ["Open", "High", "Low", "Close"])
    t = g.index
    i0, side, entry = sig["i"], sig["side"], sig["entry"]
    stop, tgt = sig["stop"], sig["target"]
    risk = abs(entry - stop)
    if risk <= 0:
        return dict(exit=entry, t=t[i0], reason="close", r=0.0, stop_now=stop, note="")
    exit_t = dtime(*map(int, a.exit_time.split(":")))
    best, half, note = 0.0, False, ""
    px, tx, reason = c[-1], t[-1], "close"
    for j in range(i0, len(g)):
        if t[j].time() >= exit_t:
            px, tx, reason = o[j], t[j], "time"; break
        if (l[j] <= stop) if side == 1 else (h[j] >= stop):
            px = min(o[j], stop) if side == 1 else max(o[j], stop)
            tx, reason = t[j], "stop"; break
        if policy == "partial" and not half:
            lvl = entry + side * risk
            if (h[j] >= lvl) if side == 1 else (l[j] <= lvl):
                half, note = True, f"half booked at +1R {t[j]:%H:%M}"
        if (h[j] >= tgt) if side == 1 else (l[j] <= tgt):
            px = (max(o[j], tgt) if side == 1 else min(o[j], tgt)) if j > i0 else tgt
            tx, reason = t[j], "target"; break
        best = max(best, ((h[j] - entry) if side == 1 else (entry - l[j])) / risk)
        if policy in ("breakeven", "partial") and best >= 1:
            new = entry
            if (new > stop) if side == 1 else (new < stop):
                stop, note = new, (note + "; " if note else "") + f"stop to entry {t[j]:%H:%M}"
        if policy == "trail" and best >= 1:
            new = l[j] if side == 1 else h[j]
            if (new > stop) if side == 1 else (new < stop):
                stop, note = new, f"trailing since +1R (stop ₹{new:,.2f})"
        if policy == "time" and (t[j] - t[i0]) >= pd.Timedelta(minutes=60) and best < 0.5:
            px, tx, reason = c[j], t[j], "time-stop"; break
    move = (px - entry) * side / risk
    r = 0.5 * 1.0 + 0.5 * move if half else move
    r -= entry * a.cost_pct / 100 / risk
    return dict(exit=px, t=tx, reason=reason, r=float(r), stop_now=stop, note=note)


def exit_status(sim):
    lab = {"stop": "STOPPED", "target": "TARGET HIT", "time": "CLOSED (time)", "time-stop": "TIME-STOP"}
    return "OPEN" if sim["reason"] == "close" else f"{lab[sim['reason']]} {sim['t']:%H:%M}"


# ============================================================================ RISK AGENT
def corr_pairs(data, symbols, before_day, lookback=10):
    """Correlation of 5-minute returns over the sessions before `before_day` (no look-ahead)."""
    rets = {}
    for s in symbols:
        df = data.get(s)
        if df is None:
            continue
        d = df[df.index.date < before_day]
        days = sorted(set(d.index.date))[-lookback:]
        d = d[np.isin(d.index.date, days)]
        rets[s] = d.Close.pct_change()
    if len(rets) < 2:
        return {}
    m = pd.DataFrame(rets).corr(min_periods=50)
    return {(a_, b_): float(m.loc[a_, b_]) for a_ in m.index for b_ in m.columns if a_ != b_ and pd.notna(m.loc[a_, b_])}


def risk_check(c, book, cfg, corr):
    """c: candidate (symbol, side, setup, t, exp_r, sentinel). book: earlier ALLOWED trades today with
    t, t_exit (None while open) and r. Returns verdict ALLOW / REDUCE / BLOCK, size multiplier, reasons."""
    if not cfg.get("risk_enabled", True):
        return "ALLOW", 1.0, ["Risk agent switched off"]
    t = c["t"]
    reasons, mult = [], 1.0
    open_now = [b for b in book if b["t"] <= t and (b["t_exit"] is None or b["t_exit"] > t)]
    realized = sum(b["r"] for b in book if b["t_exit"] is not None and b["t_exit"] <= t)
    if c["setup"] in cfg.get("paused_setups", []):
        return "BLOCK", 0.0, [f"{c['setup']} setup is paused (approved by reviewer)"]
    if realized <= -cfg["daily_loss_R"]:
        return "BLOCK", 0.0, [f"Daily loss limit reached ({realized:+.1f}R vs −{cfg['daily_loss_R']:g}R cap)"]
    if len(book) >= cfg["max_trades"]:
        return "BLOCK", 0.0, [f"Already {len(book)} trades today (cap {cfg['max_trades']})"]
    if len(open_now) >= cfg["max_open"]:
        return "BLOCK", 0.0, [f"{len(open_now)} positions already open (cap {cfg['max_open']})"]
    if cfg.get("sentinel_block") and c.get("sentinel") == "HIGH":
        return "BLOCK", 0.0, ["Sentinel rated news risk HIGH"]
    twins = [(b["symbol"], corr.get((c["symbol"], b["symbol"]), 0)) for b in open_now if b["side"] == c["side"]]
    twins = [x for x in twins if x[1] >= cfg["corr_limit"]]
    if twins:
        mult *= 0.5
        reasons.append(f"Moves with open {twins[0][0]} (correlation {twins[0][1]:.2f}) — half size")
    if cfg.get("edge_sizing") and c.get("exp_r") is not None:
        m = float(np.clip(1 + cfg.get("edge_k", 1.0) * c["exp_r"], 0.5, 1.5))
        mult *= m
        reasons.append(f"Edge sizing ×{m:.2f} from Coach expected {c['exp_r']:+.2f}R")
    if not reasons:
        reasons.append(f"Within limits: {len(open_now)} open, {len(book)} taken, day {realized:+.1f}R")
    return ("REDUCE" if mult < 0.999 else "ALLOW"), mult, reasons


# ============================================================================ REPLAY (point-in-time history)
def replay(data, idx, a, mults=None):
    """Every recent signal with features known at signal time and the result under every exit policy."""
    rows, corr_by_day = [], {}
    for s, days in ce.prep_days(data).items():
        last_day = days[-1][0] if days else None
        for d, g, pc, avg in days[1:]:
            sigs, info = ce.day_signals(g, pc, avg, a)
            if not sigs:
                continue
            idd = index_day(idx, d)
            for sg in sigs:
                ex = {p: simulate_exit(g, sg, p, a) for p in EXIT_POLICIES}
                if ex["fixed"]["reason"] == "close" and d == last_day:
                    continue  # still running today
                mood = pulse(idx, d, asof=sg["time"], mults=mults)
                feats = signal_features(g, sg, info, idd, mood=mood["regime"])
                feats["_exits"] = {p: [round(v["r"], 4), v["t"].isoformat()] for p, v in ex.items()}
                feats["_bias"] = mood["bias"]
                rows.append(dict(key=trade_key(d, s, sg["setup"], sg["side"]), ts=sg["time"], day=d, source="backtest",
                                 symbol=s, setup=sg["setup"], side=sg["side"], entry=sg["entry"], stop=sg["stop"],
                                 target=sg["target"], exit=ex["fixed"]["exit"], r=ex["fixed"]["r"],
                                 status=ex["fixed"]["reason"], features=feats, reason=None))
    for d in sorted({r["day"] for r in rows}):
        syms = sorted({r["symbol"] for r in rows if r["day"] == d})
        if len(syms) > 1:
            corr_by_day[str(d)] = {f"{k[0]}|{k[1]}": v for k, v in corr_pairs(data, syms, d).items()}
    return rows, corr_by_day


history_trades = lambda data, idx, a, source="backtest": replay(data, idx, a)[0]  # backward compatible


def _feats(f):
    return f if isinstance(f, dict) else json.loads(f or "{}")


class Coach:
    """Learns, from recent trades weighted towards the latest sessions, which conditions make or lose money.
    Predicts expected R (profit per unit of risk after costs) and win odds for new signals,
    and explains losers by the conditions that historically lose most."""
    K = 20          # shrinkage: a condition needs ~20 (recency-weighted) trades before it moves the estimate much
    DAMP = 0.6      # dampens the sum of correlated conditions

    def __init__(self, trades, half_life=None, asof_day=None):
        t = trades.copy() if trades is not None else pd.DataFrame()
        if not t.empty:
            t = t[t.r.notna()].copy()
            t["features"] = t.features.map(_feats)
            t["win"] = (t.r > 0).astype(float)
            if half_life and asof_day is not None and len(t):
                age = (pd.Timestamp(asof_day) - pd.to_datetime(t.day)).dt.days.clip(lower=0)
                t["w"] = 0.5 ** (age / float(half_life))
            else:
                t["w"] = 1.0
        self.t = t
        self.n = len(t)
        self.n_eff = float(t.w.sum()) if self.n else 0.0
        self.p0 = float((t.w * t.win).sum() / t.w.sum()) if self.n else 0.45
        self.r0 = float((t.w * t.r).sum() / t.w.sum()) if self.n else 0.0
        self.table = self._learn()
        self._lut = {(r.feature, r.bucket): r for r in self.table.itertuples(index=False)}

    def _learn(self):
        cols = ["feature", "bucket", "n", "n_eff", "win_rate", "avg_r", "shr_p", "shr_r", "edge", "redge"]
        if not self.n:
            return pd.DataFrame(columns=cols)
        rows = []
        for f in LEARN_FEATURES:
            vals = self.t.features.map(lambda d: d.get(f, "unknown"))
            for b, grp in self.t.groupby(vals):
                if b == "unknown":
                    continue
                w = grp.w
                nw = float(w.sum())
                wins, rs = float((w * grp.win).sum()), float((w * grp.r).sum())
                shr_p = (wins + self.K * self.p0) / (nw + self.K)
                shr_r = (rs + self.K * self.r0) / (nw + self.K)
                rows.append(dict(feature=f, bucket=b, n=len(grp), n_eff=nw, win_rate=wins / nw if nw else np.nan,
                                 avg_r=rs / nw if nw else np.nan, shr_p=shr_p, shr_r=shr_r,
                                 edge=shr_p - self.p0, redge=shr_r - self.r0))
        return pd.DataFrame(rows, columns=cols).sort_values("redge")

    @staticmethod
    def _logit(p):
        p = min(max(p, 0.02), 0.98)
        return math.log(p / (1 - p))

    def _rows(self, feats):
        for f in LEARN_FEATURES:
            b = feats.get(f, "unknown")
            row = self._lut.get((f, b))
            if row is not None:
                yield f, b, row

    def score(self, feats, threshold=0.0):
        if not self.n:
            return dict(conf=None, base=None, exp_r=None, r0=None, verdict="Learning", drivers=[])
        z, er, drivers = self._logit(self.p0), self.r0, []
        for f, b, row in self._rows(feats):
            z += self.DAMP * (self._logit(row.shr_p) - self._logit(self.p0))
            er += self.DAMP * row.redge
            if abs(row.redge) >= 0.03:
                drivers.append((row.redge, FEATURE_TEXT.get(f, {}).get(b, f"{f}: {b}"), int(row.n), row.win_rate, row.avg_r))
        conf = 1 / (1 + math.exp(-z))
        drivers.sort(key=lambda x: x[0])
        verdict = "Caution" if er < threshold else "Favourable" if er >= max(self.r0 + 0.1, 0.1) else "Neutral"
        return dict(conf=conf, base=self.p0, exp_r=float(er), r0=self.r0, verdict=verdict, drivers=drivers)

    def postmortem(self, feats, r):
        """Why did this trade lose? The present conditions that historically lose the most."""
        if r is None or pd.isna(r) or r > 0:
            return None
        bad = sorted([(row.redge, f, b, int(row.n), row.win_rate, row.avg_r) for f, b, row in self._rows(feats)
                      if row.redge <= -0.05])
        if not bad:
            return dict(primary="Normal variance — conditions were average; a loss like this is expected sometimes",
                        factors=[], feature=None)
        _, f0, b0, n0, wr0, ar0 = bad[0]
        return dict(primary=f"{FEATURE_TEXT.get(f0, {}).get(b0, b0)} — averages {ar0:+.2f}R across {n0} recent trades "
                            f"(won {wr0 * 100:.0f}%) vs {self.r0:+.2f}R overall",
                    feature=f"{f0}={b0}",
                    factors=[dict(text=FEATURE_TEXT.get(f, {}).get(b, b), win_rate=round(wr * 100), avg_r=round(ar, 2), n=n)
                             for _, f, b, n, wr, ar in bad[:3]])

    def lessons(self, min_n=15):
        if self.table.empty:
            return []
        out = []
        for _, r in self.table[self.table.n >= min_n].iterrows():
            if r.redge <= -0.08:
                direction = "avoid" if r.avg_r < 0 else "weaker"
            elif r.redge >= 0.08:
                direction = "favour"
            else:
                continue
            out.append(dict(feature=r.feature, bucket=r.bucket, n=int(r.n), win_rate=r.win_rate, avg_r=r.avg_r,
                            text=FEATURE_TEXT.get(r.feature, {}).get(r.bucket, r.bucket), direction=direction))
        return sorted(out, key=lambda x: x["avg_r"])


# ============================================================================ CONFIG (human-approved settings)
DEFAULT_CONFIG = dict(
    coach_filter=False, coach_threshold=0.0, half_life=10,
    pulse_sizing=True, mults=dict(DEFAULT_MULTS),
    exit_policy="fixed",
    risk_enabled=True, daily_loss_R=2.0, max_open=3, max_trades=3, corr_limit=0.7,
    edge_sizing=False, edge_k=1.0, sentinel_block=False, paused_setups=[],
)


def merged(cfg):
    c = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in (cfg or {}).items():
        if k == "mults":
            c["mults"].update(v or {})
        elif k in c:
            c[k] = v
    return c


# ============================================================================ SCORECARD (walk-forward)
def _welch(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return 0.0
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return float((a.mean() - b.mean()) / se) if se > 0 else 0.0


def _paired(d):
    d = np.asarray(d, float)
    if len(d) < 2 or d.std(ddof=1) == 0:
        return 0.0
    return float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d))))


def evidence(z, n, min_n=20):
    """0–100 agent score from the strength of evidence (z). 50 = no effect either way."""
    if n is None or n < min_n:
        return dict(score=None, grade="Too few trades to judge", tone="mid", z=z, n=n)
    score = int(round(50 + 50 * math.tanh(z / 2.5)))
    grade = ("Strong evidence it helps" if z >= 2 else "Likely helps" if z >= 1 else
             "No clear effect" if z > -1 else "Likely hurts" if z > -2 else "Strong evidence it hurts")
    tone = "good" if z >= 1 else "bad" if z <= -1 else "mid"
    return dict(score=score, grade=grade, tone=tone, z=z, n=n)


def _metrics(r, w, ts):
    r, w = np.asarray(r, float), np.asarray(w, float)
    m = w > 0
    if not m.any():
        return dict(trades=0, exp=np.nan, total=0.0, win=np.nan, maxdd=0.0)
    order = np.argsort(np.asarray(ts)[m])
    pnl = (r[m] * w[m])[order]
    eq = np.cumsum(pnl)
    dd = float((eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min()) if len(eq) else 0.0
    return dict(trades=int(m.sum()), exp=float(pnl.sum() / w[m].sum()), total=float(pnl.sum()),
                win=float((r[m] > 0).mean()), maxdd=min(dd, 0.0))


def walk_forward(df, half_life, threshold, warmup=10):
    """Score each day's signals with a Coach trained only on earlier days."""
    days = sorted(df.day.unique())
    out = []
    for d in days[warmup:]:
        coach = Coach(df[df.day < d], half_life, d)
        te = df[df.day == d]
        for row in te.itertuples():
            sc = coach.score(row.features, threshold)
            out.append((row.Index, sc["exp_r"], sc["conf"], sc["verdict"]))
    res = pd.DataFrame(out, columns=["i", "exp_r", "conf", "verdict"]).set_index("i")
    return df.loc[res.index].join(res)


def _apply(T, cfg, corr_by_day, use_coach, use_pulse, policy, use_risk):
    """Weights (0 = not taken) and R for each trade under a combination of agents."""
    r = T["r_" + policy].values
    w = np.ones(len(T))
    if use_coach:
        w[(T.exp_r < cfg["coach_threshold"]).values] = 0
    if use_pulse:
        w *= T.features.map(lambda f: cfg["mults"].get(f.get("mood"), 1.0)).values
    reasons = [""] * len(T)
    if use_risk:
        rc = dict(cfg, risk_enabled=True)
        pos = {ix: k for k, ix in enumerate(T.index)}
        for d, g in T.groupby("day"):
            corr = {tuple(k.split("|")): v for k, v in corr_by_day.get(str(d), {}).items()}
            book = []
            for row in g.sort_values("ts").itertuples():
                k = pos[row.Index]
                if w[k] == 0:
                    continue
                c = dict(symbol=row.symbol, side=row.side, setup=row.setup, t=row.ts, exp_r=row.exp_r)
                v, m, why = risk_check(c, book, rc, corr)
                reasons[k] = why[0] if v == "BLOCK" else ""
                w[k] *= m
                if v != "BLOCK":
                    book.append(dict(symbol=row.symbol, side=row.side, t=row.ts,
                                     t_exit=pd.Timestamp(row.features["_exits"][policy][1]), r=r[k] * w[k]))
    return r, w, reasons


def scorecard(rows, cfg, live=None, corr_by_day=None, risk_rs=1000.0, warmup=10):
    cfg = merged(cfg)
    df = pd.DataFrame(rows)
    if df.empty or df.day.nunique() <= warmup + 2:
        return None
    df["features"] = df.features.map(_feats)
    df = df[df.features.map(lambda f: "_exits" in f)].reset_index(drop=True)
    df["ts"] = pd.to_datetime(df.ts)
    for p in EXIT_POLICIES:
        df["r_" + p] = df.features.map(lambda f: f["_exits"][p][0])
    T = walk_forward(df, cfg["half_life"], cfg["coach_threshold"], warmup)
    if T.empty:
        return None
    corr_by_day = corr_by_day or {}
    n_days = T.day.nunique()
    per_month = len(T) / n_days * 21
    ts = T.ts.values
    base = _metrics(T.r_fixed, np.ones(len(T)), ts)
    agents = {}

    # Coach — does its expected R separate good trades from bad, and does its filter help?
    hi = T.exp_r >= T.exp_r.median()
    z_rank = _welch(T.r_fixed[hi], T.r_fixed[~hi])
    kept = T.exp_r >= cfg["coach_threshold"]
    m_c = _metrics(T.r_fixed, kept.astype(float).values, ts)
    agents["Coach"] = dict(evidence(z_rank, len(T)), on=cfg["coach_filter"],
                           setting=f"Filter {'ON' if cfg['coach_filter'] else 'OFF'} · skip below {cfg['coach_threshold']:+.2f}R · "
                                   f"memory half-life {cfg['half_life']} days",
                           effect=m_c["exp"] - base["exp"], effect_label="per trade if the filter is on",
                           detail=f"Top half of its picks averaged {T.r_fixed[hi].mean():+.2f}R vs bottom half "
                                  f"{T.r_fixed[~hi].mean():+.2f}R. Filter would skip {int((~kept).sum())} of {len(T)} "
                                  f"trades (they averaged {T.r_fixed[~kept].mean() if (~kept).any() else float('nan'):+.2f}R).")
    # Pulse — are reduced-size trades really worse?
    w_p = T.features.map(lambda f: cfg["mults"].get(f.get("mood"), 1.0)).values
    full = w_p >= w_p.max() - 1e-9
    m_p = _metrics(T.r_fixed, w_p, ts)
    agents["Pulse"] = dict(evidence(_welch(T.r_fixed[full], T.r_fixed[~full]), int(min(full.sum(), (~full).sum())) * 2),
                           on=cfg["pulse_sizing"], setting="Sizing " + ("ON · " + ", ".join(
                               f"{k} ×{v:g}" for k, v in cfg["mults"].items() if v != 1) if cfg["pulse_sizing"] else "OFF"),
                           effect=m_p["exp"] - base["exp"], effect_label="per unit of risk vs equal sizing",
                           detail=f"Full-size moods averaged {T.r_fixed[full].mean():+.2f}R; reduced-size moods "
                                  f"{T.r_fixed[~full].mean() if (~full).any() else float('nan'):+.2f}R. "
                                  f"With-market trades {T.r_fixed[T.features.map(lambda f: f.get('market')) == 'with market'].mean():+.2f}R vs "
                                  f"against {T.r_fixed[T.features.map(lambda f: f.get('market')) == 'against market'].mean():+.2f}R.")
    # Exit — paired comparison of every policy against the fixed exit on the same trades
    ex_rows = []
    for p, desc in EXIT_POLICIES.items():
        d = T["r_" + p] - T.r_fixed
        ex_rows.append(dict(policy=p, desc=desc, exp=T["r_" + p].mean(), vs_fixed=d.mean(), z=_paired(d),
                            win=(T["r_" + p] > 0).mean(), n=len(T)))
    ex_tab = pd.DataFrame(ex_rows)
    cur = ex_tab[ex_tab.policy == cfg["exit_policy"]].iloc[0]
    best = ex_tab.sort_values("exp", ascending=False).iloc[0]
    alts = ex_tab[ex_tab.policy != "fixed"].sort_values("exp", ascending=False)
    if cfg["exit_policy"] != "fixed":
        ev_x = evidence(cur.z, len(T))
        det = f"Active policy averages {cur.exp:+.2f}R per trade vs fixed {T.r_fixed.mean():+.2f}R on the same trades."
    elif alts.iloc[0].z < 1:
        ev_x = dict(evidence(0.0, len(T)), grade="Fixed exit is best so far", tone="good")
        det = (f"Every alternative did worse on the same trades (from {alts.iloc[0].vs_fixed:+.2f}R to "
               f"{alts.iloc[-1].vs_fixed:+.2f}R per trade), so the Exit agent keeps the fixed exit.")
    else:
        ev_x = evidence(alts.iloc[0].z, len(T))
        det = f"{alts.iloc[0].policy} beats fixed by {alts.iloc[0].vs_fixed:+.2f}R per trade — see proposals."
    agents["Exit"] = dict(ev_x, on=True, setting=f"Policy: {cfg['exit_policy']}", effect=cur.vs_fixed,
                          effect_label="per trade vs fixed exit", detail=det)
    # Risk — daily P&L and drawdown with and without the rules
    r_r, w_r, why_r = _apply(T, cfg, corr_by_day, False, False, "fixed", True)
    m_r = _metrics(r_r, w_r, ts)
    daily = pd.DataFrame({"day": T.day.values, "with": r_r * w_r, "without": T.r_fixed.values}).groupby("day").sum()
    blocked = pd.Series(why_r, index=T.index)
    cap_hits = T.r_fixed[blocked.str.startswith("Already")]
    agents["Risk"] = dict(evidence(_paired(daily["with"] - daily["without"]), len(daily) * 3), on=cfg["risk_enabled"],
                          setting=f"{'ON' if cfg['risk_enabled'] else 'OFF'} · loss cap −{cfg['daily_loss_R']:g}R · "
                                  f"max {cfg['max_trades']} trades, {cfg['max_open']} open · corr ≥ {cfg['corr_limit']:g} halves size",
                          effect=(m_r["total"] - base["total"]) / n_days, effect_label="per day vs no risk rules",
                          dd_change=m_r["maxdd"] - base["maxdd"],
                          detail=f"Max drawdown {base['maxdd']:+.1f}R → {m_r['maxdd']:+.1f}R. Blocked "
                                 f"{int((blocked != '').sum())} trades; those blocked by the trade cap averaged "
                                 f"{cap_hits.mean() if len(cap_hits) else float('nan'):+.2f}R.",
                          cap_hits=cap_hits.values.tolist())
    # Sentinel — only measurable on live trades (historical headlines aren't available)
    lv = pd.DataFrame(live) if live is not None and len(live) else pd.DataFrame()
    if not lv.empty:
        lv = lv[lv.r.notna()]
        lv["news"] = lv.features.map(lambda f: _feats(f).get("news"))
    ev_r = lv[lv.news == "event"].r if not lv.empty else pd.Series(dtype=float)
    cl_r = lv[lv.news == "clear"].r if not lv.empty else pd.Series(dtype=float)
    agents["Sentinel"] = dict(evidence(_welch(cl_r, ev_r), int(min(len(ev_r), len(cl_r))) * 2), on=True,
                              setting="Block HIGH-risk: " + ("ON" if cfg["sentinel_block"] else "OFF (warn only)"),
                              effect=(cl_r.mean() - ev_r.mean()) if len(ev_r) and len(cl_r) else np.nan,
                              effect_label="per trade: clear-news minus flagged trades",
                              detail=f"Measured on live signals only (past headlines aren't available): {len(ev_r)} flagged, "
                                     f"{len(cl_r)} clear so far. Needs about 10 of each before it can be judged.")
    agents["Narrator"] = dict(score=None, grade="Informational — doesn't change trades", tone="mid", n=None, z=None,
                              on=True, setting="Explains each signal", effect=np.nan, effect_label="", detail=
                              "Measured by whether you find it useful, not by profit.")
    for a_ in agents.values():
        a_["rs_month"] = (a_["effect"] * (n_days if a_ is agents["Risk"] else len(T)) / n_days * 21 * risk_rs
                          if pd.notna(a_.get("effect")) else np.nan)

    # Combined system with the active configuration
    r_s, w_s, _ = _apply(T, cfg, corr_by_day, cfg["coach_filter"], cfg["pulse_sizing"], cfg["exit_policy"], cfg["risk_enabled"])
    system = _metrics(r_s, w_s, ts)
    curves = pd.DataFrame({"ts": T.ts.values, "Baseline (no agents)": T.r_fixed.values,
                           "With agents (active config)": r_s * w_s}).sort_values("ts")
    curves[["Baseline (no agents)", "With agents (active config)"]] = curves[
        ["Baseline (no agents)", "With agents (active config)"]].cumsum()
    by_setup = {}
    for s_, g in T.sort_values("ts").groupby("setup"):
        rest = T[T.setup != s_].r_fixed
        last = g.tail(20).r_fixed
        by_setup[s_] = dict(n=len(g), exp=g.r_fixed.mean(), last20=last.mean(), z_last=_welch(last, T.r_fixed))
    return dict(cfg=cfg, base=base, system=system, agents=agents, exits=ex_tab, curves=curves, days=n_days,
                trades=len(T), per_month=per_month, risk_rs=risk_rs, by_setup=by_setup,
                period=(str(T.day.min()), str(T.day.max())), warmup=warmup)


def proposals(sc, cfg):
    """Changes the agents suggest from the evidence. Nothing changes until a human approves."""
    cfg = merged(cfg)
    P = []

    def add(agent, title, why, change):
        cid = agent + ":" + json.dumps(change, sort_keys=True)
        P.append(dict(id=cid, agent=agent, title=title, why=why, change=change))
    ex = sc["exits"].set_index("policy")
    cur = cfg["exit_policy"]
    good = ex[(ex.z >= 1.5) & (ex.index != cur)]
    if len(good):
        b = good.sort_values("exp", ascending=False).iloc[0]
        if b.exp - ex.loc[cur].exp >= 0.03:
            add("Exit", f"Switch exit policy: {cur} → {b.name}",
                f"{EXIT_POLICIES[b.name]}. Over {int(b.n)} walk-forward trades it averaged {b.exp:+.2f}R vs "
                f"{ex.loc[cur].exp:+.2f}R for {cur} (evidence z={b.z:.1f}).", {"exit_policy": b.name})
    if cur != "fixed" and ex.loc[cur].z <= -1:
        add("Exit", f"Revert exit policy to fixed", f"{cur} is now doing worse than fixed exits "
            f"({ex.loc[cur].vs_fixed:+.2f}R per trade, z={ex.loc[cur].z:.1f}).", {"exit_policy": "fixed"})
    co = sc["agents"]["Coach"]
    if not cfg["coach_filter"] and (co.get("z") or 0) >= 1.5 and co["effect"] >= 0.02:
        add("Coach", "Turn on the Coach filter", f"Its top-half picks clearly beat its bottom half (z={co['z']:.1f}) "
            f"and skipping trades below {cfg['coach_threshold']:+.2f}R expected would add {co['effect']:+.2f}R per trade.",
            {"coach_filter": True})
    if cfg["coach_filter"] and ((co.get("z") or 0) < 0.5 or co["effect"] < 0):
        add("Coach", "Turn off the Coach filter", f"Its predictions no longer separate winners from losers "
            f"(z={co.get('z') or 0:.1f}, filter effect {co['effect']:+.2f}R per trade).", {"coach_filter": False})
    pu = sc["agents"]["Pulse"]
    if cfg["pulse_sizing"] and pu.get("score") is not None and pu["z"] <= -1.0:
        add("Pulse", "Turn off mood-based sizing", f"Trades it shrinks have actually done better than full-size ones "
            f"(z={pu['z']:.1f}), so cutting their size is costing profit.", {"pulse_sizing": False})
    if not cfg["pulse_sizing"] and pu.get("score") is not None and pu["z"] >= 1.5:
        add("Pulse", "Turn on mood-based sizing", f"Trades in weaker moods have done clearly worse than the rest "
            f"(z={pu['z']:.1f}); shrinking them would protect profit.", {"pulse_sizing": True})
    ri = sc["agents"]["Risk"]
    caps = np.asarray(ri.get("cap_hits", []), float)
    if cfg["risk_enabled"] and len(caps) >= 15 and caps.mean() > 0.1 and _paired(caps) >= 1.5:
        add("Risk", f"Raise the daily trade cap to {cfg['max_trades'] + 1}",
            f"{len(caps)} trades blocked by the cap averaged {caps.mean():+.2f}R (z={_paired(caps):.1f}).",
            {"max_trades": cfg["max_trades"] + 1})
    for s_, v in sc["by_setup"].items():
        if s_ not in cfg["paused_setups"] and v["n"] >= 30 and v["last20"] < -0.25 and v["z_last"] <= -1.5:
            add("Risk", f"Pause the {s_} setup", f"Its last 20 trades averaged {v['last20']:+.2f}R, well below normal "
                f"(z={v['z_last']:.1f}) — it may have stopped working in this market.",
                {"paused_setups": sorted(set(cfg["paused_setups"]) | {s_})})
        if s_ in cfg["paused_setups"] and v["last20"] > 0.1:
            add("Risk", f"Resume the {s_} setup", f"While paused, its simulated last 20 trades averaged {v['last20']:+.2f}R.",
                {"paused_setups": sorted(set(cfg["paused_setups"]) - {s_})})
    return P


# ============================================================================ NARRATOR
def narrate(row, mood, sent, coach_score, api_key=None):
    side = "long" if row.side == 1 else "short"
    trig = ("closed beyond the 15-minute opening range with rising volume while price was on the right side of VWAP"
            if row.setup == "ORB" else
            "pulled back to VWAP in an established trend and held")
    parts = [f"{row.symbol} {trig}, so the engine took a {side} at ₹{row.entry:,.2f} "
             f"with the stop at ₹{row.stop:,.2f} and a {abs(row.target - row.entry) / abs(row.entry - row.stop):.0f}R target."]
    if mood:
        aligned = (mood["bias"] == row.side)
        parts.append(f"Market mood is {mood['regime'].lower()}" +
                     (", which supports this direction." if aligned else
                      ", which works against this direction." if mood["bias"] == -row.side else "."))
    if sent and sent["level"] != "CLEAR":
        parts.append(f"News check: {sent['reasons'][0].rstrip('.')}.")
    if coach_score and coach_score.get("conf") is not None:
        c = coach_score
        if c["drivers"]:
            worst = c["drivers"][0]
            if worst[0] < 0:
                parts.append(f"Biggest risk from past trades: {worst[1].lower()} (won {worst[3] * 100:.0f}% of {worst[2]}).")
            best = c["drivers"][-1]
            if best[0] > 0:
                parts.append(f"In its favour: {best[1].lower()} (won {best[3] * 100:.0f}% of {best[2]}).")
    text = " ".join(parts)
    if api_key:
        better = ask_claude("Rewrite this for a retail intraday trader in 2-3 short sentences, plain English, "
                            "keep every number, no advice to buy or sell:\n\n" + text, api_key, 180)
        if better:
            return better
    return text

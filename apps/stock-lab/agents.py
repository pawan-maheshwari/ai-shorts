"""
Stock Lab agents
================
  Sentinel  — news & events risk per stock (results dates, F&O ban, headline red flags)
  Pulse     — market mood from Nifty trend and India VIX; sets position-size multiplier and direction bias
  Narrator  — plain-English explanation of each signal
  Coach     — reviews every closed trade, diagnoses WHY losers lost, and learns which conditions to trust

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


def pulse(idx, day=None):
    """Market regime for the given session (default: latest)."""
    b = idx.get("intraday", pd.DataFrame())
    daily, vix = idx.get("daily", pd.Series(dtype=float)), idx.get("vix", pd.Series(dtype=float))
    if day is None and not b.empty:
        day = b.index[-1].date()
    g = index_day(idx, day) if day else pd.DataFrame()
    reasons, bias, score = [], 0, 0
    nifty_chg = intraday_vs_vwap = np.nan
    if not g.empty:
        prev = daily[daily.index.date < day]
        base = prev.iloc[-1] if len(prev) else g.Open.iloc[0]
        nifty_chg = (g.Close.iloc[-1] / base - 1) * 100
        tp = (g.High + g.Low + g.Close) / 3
        vw = (tp.cumsum() / np.arange(1, len(tp) + 1))
        intraday_vs_vwap = (g.Close.iloc[-1] / vw.iloc[-1] - 1) * 100
        if nifty_chg > 0.25 and intraday_vs_vwap > 0:
            score += 1; reasons.append(f"Nifty up {nifty_chg:.2f}% and above its intraday average")
        elif nifty_chg < -0.25 and intraday_vs_vwap < 0:
            score -= 1; reasons.append(f"Nifty down {abs(nifty_chg):.2f}% and below its intraday average")
        else:
            reasons.append(f"Nifty flat-ish ({nifty_chg:+.2f}%) — no clear intraday direction")
    trend = "unknown"
    if len(daily) >= 50:
        d = daily[daily.index.date <= day] if day else daily
        s20, s50 = d.rolling(20).mean().iloc[-1], d.rolling(50).mean().iloc[-1]
        last = d.iloc[-1]
        if last > s20 > s50:
            trend = "up"; score += 1; reasons.append("Daily trend up (above 20 and 50-day averages)")
        elif last < s20 < s50:
            trend = "down"; score -= 1; reasons.append("Daily trend down (below 20 and 50-day averages)")
        else:
            trend = "sideways"; reasons.append("Daily trend sideways")
    v = vchg = np.nan
    if len(vix) >= 2:
        vv = vix[vix.index.date <= day] if day else vix
        if len(vv) >= 2:
            v, vchg = vv.iloc[-1], (vv.iloc[-1] / vv.iloc[-2] - 1) * 100
    volatile = (not np.isnan(v) and v >= 20) or (not np.isnan(vchg) and vchg >= 8)
    if not np.isnan(v):
        reasons.append(f"India VIX {v:.1f} ({vchg:+.1f}% vs previous day)")
    if volatile:
        regime, mult = "Volatile", 0.5
        reasons.append("Fear gauge elevated — half position sizes")
    elif score >= 2:
        regime, mult, bias = "Risk-on", 1.0, 1
    elif score <= -2:
        regime, mult, bias = "Risk-off", 0.75, -1
    elif score == 1:
        regime, mult, bias = "Mildly positive", 1.0, 1
    elif score == -1:
        regime, mult, bias = "Mildly negative", 0.75, -1
    else:
        regime, mult = "Choppy", 0.75
    return dict(regime=regime, mult=mult, bias=bias, reasons=reasons, nifty_chg=nifty_chg, vix=v, vix_chg=vchg,
                trend=trend, day=str(day) if day else None)


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
}
LEARN_FEATURES = ["market", "timing", "volume", "gap", "chase", "range", "news", "setup", "side"]


def signal_features(g, sig, info, idx_day=None, news="unknown"):
    i = sig["i"]
    t = g.index[i]
    side = sig["side"]
    tp = (g.High + g.Low + g.Close) / 3
    vw = (tp * g.Volume).cumsum() / g.Volume.cumsum().replace(0, np.nan)
    vwap_at = vw.iloc[max(i - 1, 0)]
    ext = abs(sig["entry"] / vwap_at - 1) * 100 if vwap_at and not np.isnan(vwap_at) else np.nan
    mkt = "unknown"
    if idx_day is not None and not idx_day.empty:
        upto = idx_day[idx_day.index <= t]
        if len(upto):
            ch = (upto.Close.iloc[-1] / idx_day.Open.iloc[0] - 1) * 100 * side
            mkt = "with market" if ch > 0.15 else "against market" if ch < -0.15 else "flat market"
    rv = info.get("relvol", np.nan)
    gap = info.get("gap_pct", 0) * side
    orp = info.get("or_pct", np.nan)
    return {
        "market": mkt,
        "timing": "early" if t.time() < dtime(10, 30) else "midday" if t.time() < dtime(12, 30) else "late",
        "volume": "unknown" if pd.isna(rv) else "weak" if rv < 1.5 else "normal" if rv < 2.5 else "strong",
        "gap": "with gap" if gap > 0.2 else "against gap" if gap < -0.2 else "flat gap",
        "chase": "unknown" if pd.isna(ext) else "near" if ext < 0.3 else "moderate" if ext < 0.8 else "extended",
        "range": "unknown" if pd.isna(orp) else "tight" if orp < 0.6 else "normal" if orp < 1.3 else "wide",
        "news": news,
        "setup": sig["setup"],
        "side": "long" if side == 1 else "short",
    }


def trade_key(day, symbol, setup, side):
    return f"{day}|{symbol}|{setup}|{'L' if side == 1 else 'S'}"


def history_trades(data, idx, a, source="backtest"):
    """Replay recent sessions: every signal, how it ended, and its features — the Coach's training set."""
    rows = []
    for s, days in ce.prep_days(data).items():
        for d, g, pc, avg in days[1:]:
            sigs, info = ce.day_signals(g, pc, avg, a)
            if not sigs:
                continue
            idd = index_day(idx, d)
            for sg in sigs:
                px_out, t_out, why = ce.exit_trade(g, sg, a)
                if why == "close" and d == days[-1][0]:
                    continue  # still running today
                risk = abs(sg["entry"] - sg["stop"])
                r = ((px_out - sg["entry"]) * sg["side"] - sg["entry"] * a.cost_pct / 100) / risk if risk else 0
                rows.append(dict(key=trade_key(d, s, sg["setup"], sg["side"]), ts=sg["time"], day=d, source=source,
                                 symbol=s, setup=sg["setup"], side=sg["side"], entry=sg["entry"], stop=sg["stop"],
                                 target=sg["target"], exit=px_out, r=r, status=why,
                                 features=signal_features(g, sg, info, idd), reason=None))
    return rows


class Coach:
    """Shrunk win-rates per condition → confidence for new signals and diagnosis for losers."""
    K = 20          # shrinkage strength: a condition needs ~20 trades before it moves the needle much
    DAMP = 0.6      # dampen naive-Bayes sum to avoid over-confidence from correlated features

    def __init__(self, trades):
        t = trades.copy() if trades is not None else pd.DataFrame()
        if not t.empty:
            t = t[t.r.notna()]
            t["features"] = t.features.map(lambda f: f if isinstance(f, dict) else json.loads(f or "{}"))
            t["win"] = (t.r > 0).astype(int)
        self.t = t
        self.n = len(t)
        self.p0 = float(t.win.mean()) if self.n else 0.45
        self.r0 = float(t.r.mean()) if self.n else 0.0
        self.table = self._learn()

    def _learn(self):
        rows = []
        if not self.n:
            return pd.DataFrame(columns=["feature", "bucket", "n", "win_rate", "avg_r", "shrunk", "edge"])
        for f in LEARN_FEATURES:
            vals = self.t.features.map(lambda d: d.get(f, "unknown"))
            for b, grp in self.t.groupby(vals):
                if b == "unknown":
                    continue
                n, w = len(grp), grp.win.sum()
                shr = (w + self.K * self.p0) / (n + self.K)
                rows.append(dict(feature=f, bucket=b, n=n, win_rate=w / n, avg_r=grp.r.mean(), shrunk=shr,
                                 edge=shr - self.p0))
        return pd.DataFrame(rows).sort_values("edge")

    @staticmethod
    def _logit(p):
        p = min(max(p, 0.02), 0.98)
        return math.log(p / (1 - p))

    def score(self, feats):
        if not self.n:
            return dict(conf=None, base=None, verdict="Learning", drivers=[])
        z = self._logit(self.p0)
        drivers = []
        for f in LEARN_FEATURES:
            b = feats.get(f, "unknown")
            row = self.table[(self.table.feature == f) & (self.table.bucket == b)]
            if row.empty:
                continue
            row = row.iloc[0]
            dz = self.DAMP * (self._logit(row.shrunk) - self._logit(self.p0))
            z += dz
            if abs(row.edge) >= 0.03:
                drivers.append((row.edge, FEATURE_TEXT.get(f, {}).get(b, f"{f}: {b}"), int(row.n), row.win_rate))
        conf = 1 / (1 + math.exp(-z))
        drivers.sort(key=lambda x: x[0])
        verdict = "Favourable" if conf >= self.p0 + 0.05 else "Caution" if conf <= self.p0 - 0.05 else "Neutral"
        return dict(conf=conf, base=self.p0, verdict=verdict, drivers=drivers)

    def postmortem(self, feats, r):
        """Why did this trade lose? Rank the conditions present that historically do worst."""
        if r is None or pd.isna(r) or r > 0:
            return None
        bad = []
        for f in LEARN_FEATURES:
            b = feats.get(f, "unknown")
            row = self.table[(self.table.feature == f) & (self.table.bucket == b)]
            if not row.empty and row.iloc[0].edge <= -0.03:
                bad.append((row.iloc[0].edge, f, b, int(row.iloc[0].n), row.iloc[0].win_rate))
        bad.sort()
        if not bad:
            return dict(primary="Normal variance — conditions were average; a loss like this is expected sometimes",
                        factors=[], feature=None)
        e, f0, b0, n0, wr0 = bad[0]
        return dict(primary=f"{FEATURE_TEXT.get(f0, {}).get(b0, b0)} — this condition has won only "
                            f"{wr0 * 100:.0f}% of {n0} recent trades vs {self.p0 * 100:.0f}% overall",
                    feature=f"{bad[0][1]}={bad[0][2]}",
                    factors=[dict(text=FEATURE_TEXT.get(f, {}).get(b, b), win_rate=round(wr * 100), n=n)
                             for _, f, b, n, wr in bad[:3]])

    def lessons(self, min_n=15):
        """Conditions with a clear, reasonably sampled edge — what the Coach has learned."""
        if self.table.empty:
            return []
        out = []
        for _, r in self.table[self.table.n >= min_n].iterrows():
            if r.edge <= -0.06 and r.avg_r < self.r0:
                direction = "avoid" if r.avg_r < 0 else "weaker"
            elif r.edge >= 0.06 and r.avg_r > self.r0:
                direction = "favour"
            else:
                continue
            out.append(dict(feature=r.feature, bucket=r.bucket, n=int(r.n), win_rate=r.win_rate, avg_r=r.avg_r,
                            text=FEATURE_TEXT.get(r.feature, {}).get(r.bucket, r.bucket), direction=direction))
        return sorted(out, key=lambda x: x["win_rate"])


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

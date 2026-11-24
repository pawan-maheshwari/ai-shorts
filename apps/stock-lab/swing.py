"""
SWING AGENT — positional trades held 1–2 weeks (daily bars, long only, cash/delivery)
====================================================================================
Scans daily OHLCV for three classic setups, replays every past signal under the same rules to learn which setups
actually pay, and grades today's picks by that record. No fundamentals upload needed.

Setups (all need the stock in an uptrend: close above its 200-day average)
  pullback   50-day > 200-day average, price dipped to the 20-day EMA in the last 3 sessions and closed back above it
             with a higher close — buying a rest inside a trend.
  breakout   close above the highest high of the previous 20 sessions on at least 1.5× average volume, 50-day above
             200-day average, and the day's range not a blow-off (≤ 2.5 ATR).
  rsi2       2-day RSI below 10 (a sharp short dip) while above the 200-day average — a mean-reversion bounce.

Trade plan (identical in the replay and for today's picks)
  Entry      next session's open after the signal close.
  Stop       signal close − 2 × ATR(14), never more than 10% below entry. A daily low at or under the stop exits there
             (at the open if the stock gaps below it).
  Target     entry + 2R (R = entry − stop). Same-day touch of stop and target counts as the stop (conservative).
  Time stop  exit at the close of the 10th session (two trading weeks).
  Costs      cost_pct of the entry price per round trip (delivery STT, charges, slippage), charged in R.

Educational tool. Not investment advice.
"""
import math

import numpy as np
import pandas as pd

SETUPS = {
    "pullback": "Pullback to the 20-day EMA inside an uptrend",
    "breakout": "20-day breakout on 1.5× volume",
    "rsi2": "Sharp 2-day dip (RSI-2 < 10) above the 200-day average",
}
HOLD = 10            # sessions (≈ 2 trading weeks)
RR = 2.0
ATR_MULT = 2.0
MAX_STOP = 0.10
WARMUP = 210         # sessions of history needed before a signal can be computed
BENCH = "^NSEI"


# ----------------------------------------------------------------------------- data
def yahoo_ohlcv(symbols, days=1100):
    """{symbol: DataFrame[Open, High, Low, Close, Volume]} plus the benchmark under BENCH."""
    import yfinance as yf
    start = (pd.Timestamp.today() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    tickers = [s + ".NS" for s in symbols] + [BENCH]
    raw = yf.download(tickers, start=start, auto_adjust=True, progress=False, threads=True, group_by="ticker")
    out = {}
    for t in tickers:
        try:
            g = raw[t][["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        except (KeyError, TypeError):
            continue
        if len(g) < WARMUP + 5:
            continue
        g.index = pd.to_datetime(g.index).tz_localize(None)
        out[t.replace(".NS", "")] = g.sort_index()
    return out


def demo_ohlcv(px, n=35):
    """OHLCV built from a wide close-price frame (the demo data), deterministic."""
    rng = np.random.default_rng(11)
    px = px.iloc[-800:]
    cols = [c for c in px.columns if c != BENCH][:n] + ([BENCH] if BENCH in px else [])
    out = {}
    for c in cols:
        cl = px[c].astype(float)
        op = cl.shift(1).fillna(cl) * (1 + rng.normal(0, 0.004, len(cl)))
        span = np.abs(rng.normal(0, 0.009, len(cl)))
        hi = np.maximum(op, cl) * (1 + span)
        lo = np.minimum(op, cl) * (1 - span * rng.uniform(0.6, 1.4, len(cl)))
        vol = np.exp(rng.normal(13, 0.45, len(cl)))
        out[c] = pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": cl, "Volume": vol}, index=px.index)
    return out


# ----------------------------------------------------------------------------- indicators and signals
def _rsi(c, n):
    d = c.diff()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    rs_ = up.ewm(alpha=1 / n, adjust=False).mean() / dn.ewm(alpha=1 / n, adjust=False).mean().replace(0, np.nan)
    return 100 - 100 / (1 + rs_)


def features(g):
    c, h, l_ = g.Close, g.High, g.Low
    tr = pd.concat([h - l_, (h - c.shift()).abs(), (l_ - c.shift()).abs()], axis=1).max(axis=1)
    f = pd.DataFrame(index=g.index)
    f["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    f["ema20"] = c.ewm(span=20, adjust=False).mean()
    f["sma50"], f["sma200"] = c.rolling(50).mean(), c.rolling(200).mean()
    f["rsi2"] = _rsi(c, 2)
    f["hh20"] = h.shift(1).rolling(20).max()
    f["vol20"] = g.Volume.shift(1).rolling(20).mean()
    up = (c > f.sma200) & f.sma200.notna()
    trend = up & (f.sma50 > f.sma200) & (f.sma50 > f.sma50.shift(10))
    f["pullback"] = trend & (l_.rolling(3).min() <= f.ema20 * 1.005) & (c > f.ema20) & (c > c.shift(1)) \
        & (c >= l_ + 0.5 * (h - l_))
    f["breakout"] = up & (f.sma50 > f.sma200) & (c > f.hh20) & (g.Volume >= 1.5 * f.vol20) & ((h - l_) <= 2.5 * f.atr)
    f["rsi2_sig"] = up & (f.rsi2 < 10)
    return f


def _setup_cols():
    return {"pullback": "pullback", "breakout": "breakout", "rsi2": "rsi2_sig"}


def market_state(bench):
    """True on sessions where Nifty closed above its 50-day average."""
    if bench is None or bench.empty:
        return None
    c = bench.Close
    return c > c.rolling(50).mean()


def simulate(g, i, stop, cost_pct, hold=HOLD, rr=RR):
    """Trade from signal bar i: enter at bar i+1 open. Returns dict or None if it cannot be entered."""
    if i + 1 >= len(g):
        return None
    o, h, l_, c = (g[k].values for k in ("Open", "High", "Low", "Close"))
    entry = float(o[i + 1])
    stop = max(stop, entry * (1 - MAX_STOP))
    risk = entry - stop
    if not (risk > 0) or not np.isfinite(risk):
        return None
    target = entry + rr * risk
    cost_r = cost_pct / 100 * entry / risk
    last = min(i + hold, len(g) - 1)
    for j in range(i + 1, last + 1):
        if l_[j] <= stop:
            px = min(o[j], stop) if j > i + 1 else stop
            return dict(entry=entry, stop=stop, target=target, exit=float(px), exit_day=g.index[j], why="stop",
                        r=(px - entry) / risk - cost_r, days=j - i, open=False)
        if h[j] >= target:
            px = max(o[j], target) if j > i + 1 else target
            return dict(entry=entry, stop=stop, target=target, exit=float(px), exit_day=g.index[j], why="target",
                        r=(px - entry) / risk - cost_r, days=j - i, open=False)
    done = last == i + hold
    return dict(entry=entry, stop=stop, target=target, exit=float(c[last]), exit_day=g.index[last],
                why="time" if done else "open", r=(c[last] - entry) / risk - cost_r, days=last - i, open=not done)


def replay(data, cost_pct=0.25):
    """Every past signal for every stock, simulated. One position per stock at a time (no stacking)."""
    bench = data.get(BENCH)
    mkt = market_state(bench)
    rows = []
    for sym, g in data.items():
        if sym == BENCH or len(g) < WARMUP + 2:
            continue
        f = features(g)
        busy_until = -1
        sig = pd.DataFrame({s: f[c].fillna(False).astype(bool) for s, c in _setup_cols().items()})
        for i in range(WARMUP, len(g)):
            if i <= busy_until:
                continue
            hit = [s for s in SETUPS if sig[s].iat[i]]
            if not hit:
                continue
            setup = hit[0]                                        # priority: pullback, breakout, rsi2
            stop = float(g.Close.iat[i] - ATR_MULT * f.atr.iat[i])
            t = simulate(g, i, stop, cost_pct)
            d = g.index[i]
            mv = mkt.get(d) if mkt is not None else None
            with_mkt = None if mv is None or pd.isna(mv) else bool(mv)
            base = dict(symbol=sym, setup=setup, day=d, close=float(g.Close.iat[i]), atr=float(f.atr.iat[i]),
                        signal_stop=stop, with_market=with_mkt, also=",".join(hit[1:]))
            if t is None:                                         # signal on the last bar → today's pick
                rows.append(dict(base, pending=True))
                continue
            rows.append(dict(base, pending=False, **t))
            busy_until = i + t["days"]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("day").reset_index(drop=True)


# ----------------------------------------------------------------------------- statistics
def _t(x):
    x = np.asarray(x, float)
    if len(x) < 2 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def stats(closed, min_n=20):
    """Per-setup record on completed trades. score: 0–100, 50 = no edge (same scale as the other agents)."""
    out = []
    groups = [("All setups", closed)] + [(s, closed[closed.setup == s]) for s in SETUPS]
    for name, g in groups:
        r = g.r.values if len(g) else np.array([])
        z = _t(r)
        n = len(r)
        score = int(round(50 + 50 * math.tanh(z / 2.5))) if n >= min_n else None
        wm = g[g.with_market == True].r if len(g) else pd.Series(dtype=float)       # noqa: E712
        am = g[g.with_market == False].r if len(g) else pd.Series(dtype=float)      # noqa: E712
        out.append(dict(setup=name, n=n, win=float((r > 0).mean()) if n else np.nan,
                        exp=float(r.mean()) if n else np.nan, z=z, score=score,
                        days=float(g.days.mean()) if n else np.nan,
                        hit_target=float((g.why == "target").mean()) if n else np.nan,
                        exp_with=float(wm.mean()) if len(wm) else np.nan, n_with=len(wm),
                        exp_against=float(am.mean()) if len(am) else np.nan, n_against=len(am)))
    return pd.DataFrame(out)


def verdict(st_row, with_market):
    """Favourable / Unproven / Avoid from the setup's own record, in today's market state when there is enough."""
    if st_row is None or st_row["n"] < 20:
        return "Unproven", "fewer than 20 past trades"
    exp, z = st_row["exp"], st_row["z"]
    side_exp, side_n = ((st_row["exp_with"], st_row["n_with"]) if with_market else
                        (st_row["exp_against"], st_row["n_against"]))
    if with_market is not None and side_n >= 15 and side_exp < 0:
        return "Avoid", f"{side_exp:+.2f}R per trade when Nifty is {'above' if with_market else 'below'} its 50-day"
    if z >= 1 and exp > 0:
        return "Favourable", f"{exp:+.2f}R per trade over {int(st_row['n'])} past trades (z={z:.1f})"
    if z <= -1:
        return "Avoid", f"{exp:+.2f}R per trade over {int(st_row['n'])} past trades (z={z:.1f})"
    return "Unproven", f"{exp:+.2f}R per trade, not yet reliable (z={z:.1f})"


def run(data, capital=200000.0, risk_pct=0.5, cost_pct=0.25, max_pos_frac=0.25):
    """Everything the Swing tab shows: today's picks, still-open earlier signals, setup record, equity curve."""
    tr = replay(data, cost_pct)
    if tr.empty:
        return None
    bench = data.get(BENCH)
    mkt = market_state(bench)
    mkt_now = bool(mkt.iloc[-1]) if mkt is not None and len(mkt) and pd.notna(mkt.iloc[-1]) else None
    closed = tr[(~tr.pending) & (~tr.open.fillna(False).astype(bool))]
    st_ = stats(closed)
    by = st_.set_index("setup")
    risk_rs = capital * risk_pct / 100

    def size(entry, stop):
        rps = entry - stop
        if not (rps > 0):
            return 0
        return int(max(0, min(risk_rs / rps, capital * max_pos_frac / entry)))

    last_day = max(g.index[-1] for s, g in data.items() if s != BENCH)
    picks = tr[tr.pending & (tr.day == last_day)].copy()
    if len(picks):
        picks["entry_est"] = picks.close
        picks["stop"] = np.maximum(picks.signal_stop, picks.close * (1 - MAX_STOP))
        picks["target"] = picks.close + RR * (picks.close - picks.stop)
        picks["stop_pct"] = picks.stop / picks.close - 1
        vv = [verdict(by.loc[s] if s in by.index else None, mkt_now) for s in picks.setup]
        picks["verdict"], picks["why"] = [v[0] for v in vv], [v[1] for v in vv]
        picks["qty"] = [size(e, s_) for e, s_ in zip(picks.entry_est, picks.stop)]
        picks["rank"] = picks.verdict.map({"Favourable": 0, "Unproven": 1, "Avoid": 2})
        picks["setup_exp"] = picks.setup.map(lambda s: by.loc[s, "exp"] if s in by.index else np.nan)
        picks = picks.sort_values(["rank", "setup_exp"], ascending=[True, False])
    active = tr[(~tr.pending) & (tr.open.fillna(False).astype(bool))].copy()
    if len(active):
        last_px = {s: float(g.Close.iat[-1]) for s, g in data.items()}
        active["last"] = active.symbol.map(last_px)
        active["r_now"] = active.r
    eq = closed.sort_values("exit_day")[["exit_day", "r", "setup"]].copy()
    eq["cum"] = eq.r.cumsum()
    return dict(trades=tr, closed=closed, stats=st_, picks=picks, active=active, equity=eq, market_up=mkt_now,
                as_of=last_day, risk_rs=risk_rs, cost_pct=cost_pct,
                period=(str(closed.day.min().date()) if len(closed) else "", str(last_day.date())))


def agent_card(res):
    """Score-card dict in the same shape the 🎯 Agent scores grid uses."""
    if not res:
        return dict(score=None, grade="No daily data yet", tone="mid", detail="", setting="", effect=np.nan)
    a = res["stats"].set_index("setup").loc["All setups"]
    best = res["stats"][res["stats"].setup != "All setups"].dropna(subset=["score"]).sort_values("z", ascending=False)
    z, n = float(a.z), int(a.n)
    score = a.score if pd.notna(a.score) else None
    grade = ("Too few past trades to judge" if score is None else "Strong evidence of an edge" if z >= 2 else
             "Likely has an edge" if z >= 1 else "No clear edge yet" if z > -1 else "Likely loses money")
    tone = "mid" if score is None else "good" if z >= 1 else "bad" if z <= -1 else "mid"
    return dict(score=None if score is None else int(score), grade=grade, tone=tone, z=z, n=n, on=True,
                effect=float(a.exp), effect_label="per swing trade after costs",
                detail=(f"Replayed {n} past swing trades ({res['period'][0]} → {res['period'][1]}): won {a.win * 100:.0f}%, "
                        f"held {a.days:.1f} sessions on average."
                        + (f" Best setup: {best.iloc[0].setup} {best.iloc[0].exp:+.2f}R per trade." if len(best) else "")),
                setting=f"Long only · entry next open · stop 2 ATR · target {RR:g}R · {HOLD}-session time stop · "
                        f"costs {res['cost_pct']:g}% round trip", rs_month=np.nan)

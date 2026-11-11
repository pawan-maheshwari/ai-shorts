"""Scout — the strategy-research agent.

The other agents can only filter, size or exit the signals they are given. Scout looks for better signals:
it generates thousands of candidate intraday strategies (8 setup families × their parameters × side, market,
volume, stop-size and target filters), simulates every one with the same fills, stop-first rule and costs as
the live engine, and keeps only those that

  1. were profitable on the DISCOVERY days (the older ~60% of history), and
  2. stayed profitable on the CONFIRMATION days (the newest ~40%), which played no part in choosing them.

The confirmed best can be proposed as the new signal engine; it goes live only after a human approves it.
`day_signals` turns an approved strategy into live signals in the same format as the classic engine.
"""
import json
import math
from datetime import time as dtime
from itertools import product

import numpy as np
import pandas as pd

LAST_ENTRY = 14 * 60 + 30          # no entries after 14:30
EXIT_MIN = 15 * 60 + 15            # everything closed at 15:15
RRS = [1.0, 1.5, 2.0, 3.0]

FAMILIES = {   # family: (short code shown on cards, plain description, list of parameter dicts)
    "orb": ("ORB", "Opening-range breakout: a 5-min close beyond the first {nb_min} minutes' range, above/below "
                   "VWAP, on rising volume. Stop at the {stop_txt} of the range.",
            [dict(nb=nb, stop=s) for nb in (3, 6) for s in ("far", "mid")]),
    "vwap": ("VWAP", "VWAP pullback: price has held above (below) VWAP for {k_min} minutes, dips to VWAP and closes "
                     "back on the trend side. Stop just beyond the pullback bar.",
             [dict(k=k) for k in (6, 12)]),
    "gapgo": ("GAP-GO", "Gap-and-go: the stock gaps at least {g}% and the first {nb_min} minutes close further in the "
                        "gap's direction. Trade with the gap; stop beyond that opening move.",
              [dict(g=g, nb=nb) for g in (0.5, 1.0) for nb in (1, 3)]),
    "gapfade": ("GAP-FADE", "Gap fade: the stock gaps at least {g}% but the first {nb_min} minutes close back against "
                            "the gap. Trade against the gap; stop beyond the opening extreme.",
                [dict(g=g, nb=nb) for g in (0.5, 1.0) for nb in (1, 3)]),
    "trend": ("TREND", "Trend day: by {T_txt} the stock is up (down) at least {m}% from the open and on the right "
                       "side of VWAP. Enter in that direction; stop just beyond VWAP.",
              [dict(T=T, m=m) for T in (630, 690, 780) for m in (0.5, 1.0)]),
    "hod": ("HOUR-BRK", "First-hour breakout: after 10:15, a close beyond the first hour's high (low) on rising "
                        "volume. Stop at the {stop_txt} of the first-hour range.",
            [dict(stop=s) for s in ("far", "mid")]),
    "revert": ("REVERT", "Stretch reversal: after 11:00 price is at least {k}× its normal bar size away from VWAP and "
                         "a bar closes back toward it. Trade back toward VWAP; stop beyond the last 3 bars.",
               [dict(k=k) for k in (3, 5)]),
    "rs": ("REL-STR", "Relative strength: by {T_txt} the stock has beaten (lagged) Nifty by at least {d}% and is on the "
                      "right side of VWAP. Trade in that direction; stop at the day's low (high) so far.",
           [dict(T=T, d=d) for T in (600, 630) for d in (0.7, 1.2)]),
}
SIDES = ["long", "short", "both"]
NIFTY = ["any", "with"]
RELVOL = [0.0, 1.2, 1.5]
MINSTOP = [0.0, 0.4, 0.8]
OBJECTIVES = {"win": "Highest win rate (must still be profitable)", "exp": "Most profit per trade",
              "month": "Most profit per month"}


def _hm(m):
    return f"{m // 60}:{m % 60:02d}"


def describe(spec):
    """Plain-English rules of a strategy."""
    fam = spec["fam"]
    p = dict(spec["prm"])
    p.update(nb_min=p.get("nb", 0) * 5, k_min=p.get("k", 0) * 5, T_txt=_hm(p.get("T", 0)),
             stop_txt={"far": "far side", "mid": "middle"}.get(p.get("stop"), ""))
    rules = FAMILIES[fam][1].format(**p)
    f = [{"long": "Longs only", "short": "Shorts only", "both": "Longs and shorts"}[spec["side"]]]
    if spec["nifty"] == "with":
        f.append("only when Nifty is moving the same way")
    if spec["relvol"]:
        f.append(f"opening volume ≥ {spec['relvol']:g}× normal")
    if spec["minstop"]:
        f.append(f"stop at least {spec['minstop']:g}% away (keeps costs small relative to risk)")
    f.append(f"target {spec['rr']:g}× risk, otherwise out at 15:15")
    return rules + " " + "; ".join(f) + "."


def label(spec):
    p = spec["prm"]
    bits = {"orb": lambda: f"{p['nb'] * 5}-min range, {p['stop']} stop", "vwap": lambda: f"{p['k'] * 5}-min trend",
            "gapgo": lambda: f"gap ≥{p['g']}%, {p['nb'] * 5}-min confirm", "gapfade": lambda: f"gap ≥{p['g']}%, {p['nb'] * 5}-min fail",
            "trend": lambda: f"by {_hm(p['T'])}, ≥{p['m']}%", "hod": lambda: f"{p['stop']} stop",
            "revert": lambda: f"{p['k']}× stretch", "rs": lambda: f"by {_hm(p['T'])}, ≥{p['d']}% vs Nifty"}[spec["fam"]]()
    f = [spec["side"]]
    if spec["nifty"] == "with":
        f.append("with Nifty")
    if spec["relvol"]:
        f.append(f"vol ≥{spec['relvol']:g}×")
    if spec["minstop"]:
        f.append(f"stop ≥{spec['minstop']:g}%")
    f.append(f"{spec['rr']:g}R")
    return f"{FAMILIES[spec['fam']][0]} ({bits}) · " + " · ".join(f)


def spec_id(spec):
    return json.dumps(spec, sort_keys=True)


# ------------------------------------------------------------------ per stock-day arrays and signal generators
def _arrays(g, pc, avg, idx_day=None):
    o, h, l, c, v = (g[k].values.astype(float) for k in ["Open", "High", "Low", "Close", "Volume"])
    tm = g.index.hour.values * 60 + g.index.minute.values
    cv = np.cumsum(v)
    tp = (h + l + c) / 3
    vwap = np.where(cv > 0, np.cumsum(tp * v) / np.maximum(cv, 1), c)
    n = len(g)
    mean_prev = np.r_[np.nan, cv[:-1] / np.arange(1, n)] if n else np.array([])
    rng_ = np.where(c > 0, (h - l) / c, np.nan)
    atr = np.cumsum(np.nan_to_num(rng_)) / np.arange(1, n + 1) * c        # running average bar size, in price
    nm = np.full(n, np.nan)                                                  # Nifty % move from open, at bar close
    if idx_day is not None and len(idx_day):
        it = idx_day.index.hour.values * 60 + idx_day.index.minute.values
        ic = idx_day.Close.values.astype(float)
        io = float(idx_day.Open.iloc[0])
        k = np.searchsorted(it, tm, side="right") - 1
        ok = k >= 0
        nm[ok] = (ic[k[ok]] / io - 1) * 100
    gap = (o[0] / pc - 1) * 100 if pc else np.nan
    relvol = v[:3].sum() / avg if avg and avg > 0 else np.nan
    orh, orl = h[:3].max(), l[:3].min()
    info = dict(gap_pct=gap, relvol=relvol, or_high=orh, or_low=orl, or_pct=(orh - orl) / o[0] * 100,
                last=c[-1], vwap=vwap[-1], prev_close=pc, chg_pct=(c[-1] / pc - 1) * 100 if pc else np.nan,
                last_bar=g.index[-1])
    return dict(o=o, h=h, l=l, c=c, v=v, tm=tm, vwap=vwap, n=n, vol_ok=v > mean_prev, atr=atr, nm=nm,
                gap=gap, relvol=relvol), info


def _first(mask):
    k = int(np.argmax(mask)) if len(mask) else 0
    return k if len(mask) and mask[k] else None


def _entry_ok(A):
    """Signal bar i is allowed if bar i+1 exists and starts by 14:30."""
    ok = np.zeros(A["n"], bool)
    ok[:-1] = A["tm"][1:] <= LAST_ENTRY
    return ok


def generate(fam, prm, side, A):
    """First signal of one family/variant/side for a stock-day → (i_entry, entry, stop) or None."""
    o, h, l, c, tm, vw, n = A["o"], A["h"], A["l"], A["c"], A["tm"], A["vwap"], A["n"]
    if n < 15 or not np.isfinite(A["gap"]) or abs(A["gap"]) > 4:
        return None
    ok = _entry_ok(A)
    idx = np.arange(n)
    s = side
    if fam == "orb":
        nb = prm["nb"]
        hi, lo = h[:nb].max(), l[:nb].min()
        pct = (hi - lo) / o[0] * 100
        if not (0.3 <= pct <= (2.0 if nb == 3 else 3.0)):
            return None
        brk = (c > hi) & (c > vw) if s == 1 else (c < lo) & (c < vw)
        i = _first(brk & A["vol_ok"] & ok & (idx >= nb))
        if i is None:
            return None
        stop = (lo if s == 1 else hi) if prm["stop"] == "far" else (hi + lo) / 2
        return i + 1, o[i + 1], stop
    if fam == "vwap":
        k = prm["k"]
        if n <= k + 1:
            return None
        d = (c - vw) * s
        win = np.lib.stride_tricks.sliding_window_view(d, k)          # win[j] = d[j:j+k]
        prev_min = np.full(n, -np.inf)
        prev_min[k:] = win[:n - k].min(axis=1)                       # min of d[i-k:i]
        vw_up = np.zeros(n, bool)
        vw_up[k:] = (vw[k:] - vw[:-k]) * s > 0
        touch = (l <= vw * 1.001) & (c > vw) if s == 1 else (h >= vw * 0.999) & (c < vw)
        i = _first((prev_min > 0) & vw_up & touch & ok & (tm >= 600) & (idx >= max(k, 6)))
        if i is None:
            return None
        stop = min(l[i], vw[i]) * 0.999 if s == 1 else max(h[i], vw[i]) * 1.001
        if abs(o[i + 1] - stop) / o[i + 1] * 100 > 2.0:
            return None
        return i + 1, o[i + 1], stop
    if fam in ("gapgo", "gapfade"):
        g, nb = prm["g"], prm["nb"]
        want = np.sign(A["gap"]) * (1 if fam == "gapgo" else -1)
        if abs(A["gap"]) < g or want != s or not ok[nb - 1]:
            return None
        if (c[nb - 1] - o[0]) * s <= 0:
            return None
        stop = l[:nb].min() if s == 1 else h[:nb].max()
        return nb, o[nb], stop
    if fam == "trend":
        i = _first(tm >= prm["T"] - 5)
        if i is None or not ok[i]:
            return None
        if (c[i] / o[0] - 1) * 100 * s < prm["m"] or (c[i] - vw[i]) * s <= 0:
            return None
        return i + 1, o[i + 1], vw[i] * (1 - s * 0.001)
    if fam == "hod":
        if n < 14:
            return None
        hi, lo = h[:12].max(), l[:12].min()
        if (hi - lo) / o[0] * 100 > 3.0:
            return None
        brk = (c > hi) if s == 1 else (c < lo)
        i = _first(brk & A["vol_ok"] & ok & (idx >= 12))
        if i is None:
            return None
        stop = (lo if s == 1 else hi) if prm["stop"] == "far" else (hi + lo) / 2
        return i + 1, o[i + 1], stop
    if fam == "revert":
        stretch = (vw - c) * s / np.maximum(A["atr"], 1e-9)
        turn = (c > o) if s == 1 else (c < o)
        i = _first((stretch >= prm["k"]) & turn & ok & (tm >= 660) & (idx >= 3))
        if i is None:
            return None
        stop = l[i - 2:i + 1].min() * 0.999 if s == 1 else h[i - 2:i + 1].max() * 1.001
        return i + 1, o[i + 1], stop
    if fam == "rs":
        i = _first(tm >= prm["T"] - 5)
        if i is None or not ok[i] or not np.isfinite(A["nm"][i]):
            return None
        rel = ((c[i] / o[0] - 1) * 100 - A["nm"][i]) * s
        if rel < prm["d"] or (c[i] - vw[i]) * s <= 0:
            return None
        return i + 1, o[i + 1], (l[:i + 1].min() if s == 1 else h[:i + 1].max())
    raise ValueError(fam)


def simulate(A, i0, side, entry, stop, cost_pct, rrs=RRS):
    """R after costs for each target multiple, with the live engine's rules: stop checked before target inside a
    bar, target filled at the better of open/target after the entry bar, out at the 15:15 bar's open.
    Returns (list of R, list of exit bar index) or None if the session ended before an exit (still running)."""
    o, h, l, tm = A["o"][i0:], A["h"][i0:], A["l"][i0:], A["tm"][i0:]
    risk = abs(entry - stop)
    if risk <= 0 or (entry - stop) * side <= 0:
        return None
    cost = entry * cost_pct / 100 / risk
    m = len(o)
    big = m + 10
    jx = _first(tm >= EXIT_MIN)
    jx = big if jx is None else jx
    hit_s = (l <= stop) if side == 1 else (h >= stop)
    js = _first(hit_s)
    js = big if js is None else js
    out_r, out_j = [], []
    for rr in rrs:
        tgt = entry + side * rr * risk
        hit_t = (h >= tgt) if side == 1 else (l <= tgt)
        jt = _first(hit_t)
        jt = big if jt is None else jt
        j = min(js, jt, jx)
        if j >= big:
            return None
        if j == jx and jx <= min(js, jt):
            px = o[j]
        elif js <= jt:
            px = min(o[j], stop) if side == 1 else max(o[j], stop)
        else:
            px = ((max(o[j], tgt) if side == 1 else min(o[j], tgt)) if j > 0 else tgt)
        out_r.append((px - entry) * side / risk - cost)
        out_j.append(i0 + j)
    return out_r, out_j


# ------------------------------------------------------------------ research run
def _sessions(df):
    df = df[(df.index.time >= dtime(9, 15)) & (df.index.time < dtime(15, 30))]
    return [(d, g) for d, g in df.groupby(df.index.date)]


def raw_signals(data, idx, cost_pct):
    """Every first signal of every family/variant/side on every stock-day, with filter fields and R per target."""
    ib = (idx or {}).get("intraday")
    ib = ib if ib is not None and len(ib) else None
    idays = {d: g for d, g in ib.groupby(ib.index.date)} if ib is not None else {}
    rows = []
    for sym, df in data.items():
        days = _sessions(df)
        or_vols, pc = [], None
        for d, g in days:
            avg = np.mean(or_vols[-10:]) if or_vols else np.nan
            if pc is not None and len(g) >= 15:
                A, info = _arrays(g, pc, avg, idays.get(d))
                for fam, (_, _, variants) in FAMILIES.items():
                    for vi, prm in enumerate(variants):
                        for side in (1, -1):
                            sg = generate(fam, prm, side, A)
                            if sg is None:
                                continue
                            i0, entry, stop = sg
                            sim = simulate(A, i0, side, entry, stop, cost_pct)
                            if sim is None:
                                continue
                            nmv = A["nm"][i0 - 1]
                            rows.append((d, sym, fam, vi, side, i0, float(entry), float(stop),
                                         info["relvol"], (nmv * side > 0.1) if np.isfinite(nmv) else None,
                                         abs(entry - stop) / entry * 100, *sim[0]))
            or_vols.append(g.Volume.values[:3].sum())
            pc = g.Close.values[-1]
    cols = ["day", "symbol", "fam", "vi", "side", "i", "entry", "stop", "relvol", "nwith", "stop_pct"] + \
           [f"r{rr:g}" for rr in RRS]
    return pd.DataFrame(rows, columns=cols)


def _m(r):
    n = len(r)
    if not n:
        return dict(n=0, win=np.nan, exp=np.nan, total=0.0, z=0.0)
    sd = r.std(ddof=1) if n > 1 else 0.0
    return dict(n=n, win=float((r > 0).mean()), exp=float(r.mean()), total=float(r.sum()),
                z=float(r.mean() / (sd / math.sqrt(n))) if sd > 0 else 0.0)


def _score(m, objective, per_month):
    """Ranking score with a penalty for small samples (lower confidence bound), so lucky handfuls don't win."""
    if not m["n"]:
        return -np.inf
    se_w = math.sqrt(max(m["win"] * (1 - m["win"]), 1e-6) / m["n"])
    se_r = abs(m["exp"]) / max(abs(m["z"]), 1e-6) if m["z"] else 1.0
    if objective == "win":
        return m["win"] - se_w if m["exp"] - se_r * 0.5 > 0 else -np.inf
    if objective == "exp":
        return m["exp"] - se_r
    return (m["exp"] - se_r) * per_month


def run(data, idx, cost_pct, objective="win", split=0.6, min_disc=30, min_conf=15, top_k=25, per_family=3,
        current=None, raw=None):
    """Discover on the older days, confirm on the newest days. Returns a dict for the UI and the proposals.
    current = the active spec (dict) or None for the classic ORB + VWAP engine. raw = raw_signals(...) if cached."""
    raw = raw_signals(data, idx, cost_pct) if raw is None else raw
    if raw.empty:
        return None
    days = sorted(raw.day.unique())
    if len(days) < 15:
        return None
    cut = days[int(len(days) * split)]
    disc_mask = (raw.day < cut).values
    n_disc_days, n_conf_days = int((np.array(days) < cut).sum()), int((np.array(days) >= cut).sum())
    rows, by_id = [], {}
    has_idx = raw.nwith.notna().any()
    for (fam, vi), sub in raw.groupby(["fam", "vi"]):
        prm = FAMILIES[fam][2][vi]
        side = sub.side.values
        rv = sub.relvol.values
        nw = sub.nwith.values
        sp = sub.stop_pct.values
        dm = disc_mask[sub.index.values]
        R = {rr: sub[f"r{rr:g}"].values for rr in RRS}
        for sd, nf, rl, ms in product(SIDES, NIFTY if has_idx else ["any"], RELVOL, MINSTOP):
            m = np.ones(len(sub), bool)
            if sd != "both":
                m &= side == (1 if sd == "long" else -1)
            if nf == "with":
                m &= nw == True                                              # noqa: E712
            if rl:
                m &= np.nan_to_num(rv, nan=0) >= rl
            if ms:
                m &= sp >= ms
            if m.sum() < min_disc:
                continue
            for rr in RRS:
                spec = dict(fam=fam, prm=prm, side=sd, nifty=nf, relvol=rl, minstop=ms, rr=rr)
                r = R[rr]
                md, mc, ma = _m(r[m & dm]), _m(r[m & ~dm]), _m(r[m])
                rows.append(dict(id=spec_id(spec), fam=fam, spec=spec, d_n=md["n"], d_win=md["win"], d_exp=md["exp"],
                                 d_z=md["z"], c_n=mc["n"], c_win=mc["win"], c_exp=mc["exp"], c_z=mc["z"],
                                 c_total=mc["total"], all_n=md["n"] + mc["n"],
                                 all_win=ma["win"], all_exp=ma["exp"], all_z=ma["z"],
                                 d_pm=md["n"] / n_disc_days * 21, c_pm=mc["n"] / max(n_conf_days, 1) * 21,
                                 score=_score(md, objective, md["n"] / n_disc_days * 21)))
                by_id[rows[-1]["id"]] = (sub.index.values[m], rr)
    C = pd.DataFrame(rows)
    tested = len(C)
    C = C[C.d_n >= min_disc]
    C["nfilt"] = C.spec.map(lambda x: (x["side"] != "both") + (x["nifty"] != "any") + (x["relvol"] > 0) + (x["minstop"] > 0))
    shortlist = C[np.isfinite(C.score)].sort_values(["score", "nfilt"], ascending=[False, True])
    # a filter that removes no trades gives the same trades under another name — keep the simplest version only
    shortlist = shortlist.drop_duplicates(["fam", "d_n", "c_n", "d_exp", "c_exp"])
    shortlist["rule"] = shortlist.spec.map(lambda x: spec_id(dict(x, rr=None)))
    shortlist = shortlist.drop_duplicates("rule")          # best target size per rule; other sizes are close cousins
    shortlist = shortlist.groupby("fam", sort=False).head(per_family).head(top_k).copy()
    shortlist["held"] = (shortlist.c_n >= min_conf) & (shortlist.c_exp > 0)
    if objective == "win":
        shortlist["held"] &= shortlist.c_win >= shortlist.d_win - 0.12
    shortlist["label"] = shortlist.spec.map(label)
    conf = shortlist[shortlist.held].copy()
    if len(conf):
        conf["final"] = [_score(dict(n=r.all_n, win=r.all_win, exp=r.all_exp, z=r.all_z), objective,
                                (r.d_pm + r.c_pm) / 2) for r in conf.itertuples()]
        conf = conf.sort_values(["final", "c_exp"], ascending=False)

    # the classic engine on the same days, for comparison (ORB 15-min range + VWAP 30-min trend, longs, vol ≥ 1.5, 2R)
    cm = ((raw.fam == "orb") & (raw.vi == 0)) | ((raw.fam == "vwap") & (raw.vi == 0))
    cm &= (raw.side == 1) & (np.nan_to_num(raw.relvol.values, nan=0) >= 1.5)
    classic = dict(label="Classic engine (ORB + VWAP pullback, longs, volume ≥ 1.5×, 2R)",
                   d=_m(raw.r2[cm & disc_mask].values), c=_m(raw.r2[cm & ~disc_mask].values))
    cur = None
    if current:
        cid = spec_id(current)
        hit = C[C.id == cid] if len(C) else C
        if len(hit):
            h_ = hit.iloc[0]
            cur = dict(label=label(current), d=dict(n=h_.d_n, win=h_.d_win, exp=h_.d_exp),
                       c=dict(n=h_.c_n, win=h_.c_win, exp=h_.c_exp, z=h_.c_z))
    curves = {}
    curves["classic"] = raw[cm].groupby("day").r2.sum()
    keep = set(shortlist.id) | ({spec_id(current)} if current else set())
    for i_ in keep:
        if i_ in by_id:
            ix, rr = by_id[i_]
            t = raw.loc[ix]
            curves[i_] = t.groupby("day")[f"r{rr:g}"].sum()
    return dict(candidates=C, shortlist=shortlist, confirmed=conf, classic=classic, current=cur, tested=tested,
                days=len(days), cut=str(cut), period=(str(days[0]), str(days[-1])), n_disc_days=n_disc_days,
                n_conf_days=n_conf_days, objective=objective, curves=curves, n_raw=len(raw), has_idx=bool(has_idx),
                held_rate=float(shortlist.held.mean()) if len(shortlist) else np.nan)


def adopt_change(spec):
    """Settings change for adopting a strategy: Scout's results assume holding to stop / target / 15:15."""
    return {"strategy": spec, "paper_window_min": 0, "exit_policy": "fixed", "panel": False}


def proposal(res, cfg):
    """Scout's proposal for the shared settings, or None. Same shape as agents.proposals entries."""
    if not res:
        return None
    cur_spec = cfg.get("strategy")
    conf = res["confirmed"]
    base = res["current"]["c"] if (cur_spec and res["current"]) else res["classic"]["c"]
    base_exp = base["exp"] if base["n"] else -np.inf
    if len(conf):
        b = conf.iloc[0]
        if spec_id(b.spec) != (spec_id(cur_spec) if cur_spec else None) and b.c_exp >= (
                base_exp if np.isfinite(base_exp) else -9) + 0.05 and b.all_exp > 0:
            why = (f"Tested {res['tested']:,} strategies. Chosen on {res['n_disc_days']} older sessions "
                   f"(won {b.d_win * 100:.0f}%, {b.d_exp:+.2f}R per trade) and then checked on the newest "
                   f"{res['n_conf_days']} sessions it had never seen: won {b.c_win * 100:.0f}%, {b.c_exp:+.2f}R per trade "
                   f"over {int(b.c_n)} trades, vs {base['win'] * 100:.0f}% and {base_exp:+.2f}R for the current engine. "
                   f"About {b.c_pm:.0f} signals a month before the Risk agent's caps. Rules: {describe(b.spec)} "
                   f"Scout tested it held to its stop, target or 15:15, so approving also sets the execution window "
                   f"to 'hold' and the exit to fixed — otherwise live trades would not match what was tested.")
            return dict(id="Scout:" + b.id, agent="Scout", title=f"Switch the signal engine to: {b.label}", why=why,
                        change=adopt_change(b.spec))
    if cur_spec and res["current"] and (res["current"]["c"]["n"] >= 15 and res["current"]["c"]["exp"] <= 0):
        return dict(id="Scout:revert:" + spec_id(cur_spec), agent="Scout", title="Return to the classic signal engine",
                    why=f"The approved strategy lost money on the newest {res['n_conf_days']} sessions "
                        f"({res['current']['c']['exp']:+.2f}R per trade over {int(res['current']['c']['n'])} trades) and "
                        f"no other strategy held up well enough to replace it.", change={"strategy": None})
    return None


# ------------------------------------------------------------------ live use of an approved strategy
def day_signals(g, prev_close, avg_or_vol, p):
    """Drop-in replacement for cheetah_engine.day_signals when a Scout strategy is approved (p.strategy)."""
    spec = p.strategy
    if len(g) < 15 or prev_close is None:
        return [], {}
    ib = getattr(p, "idx_bars", None)
    idd = None
    if ib is not None and len(ib):
        d = g.index[0].date()
        idd = ib[ib.index.date == d]
    A, info = _arrays(g, prev_close, avg_or_vol, idd)
    rv = info["relvol"]
    if spec["relvol"] and not (np.isfinite(rv) and rv >= spec["relvol"]):
        return [], info
    sigs = []
    for side in ((1,) if spec["side"] == "long" else (-1,) if spec["side"] == "short" else (1, -1)):
        sg = generate(spec["fam"], spec["prm"], side, A)
        if sg is None:
            continue
        i0, entry, stop = sg
        if abs(entry - stop) / entry * 100 < max(spec["minstop"], float(getattr(p, "min_stop_pct", 0) or 0)):
            continue
        if spec["nifty"] == "with":
            nmv = A["nm"][i0 - 1]
            if not (np.isfinite(nmv) and nmv * side > 0.1):
                continue
        risk = abs(entry - stop)
        sigs.append(dict(setup=FAMILIES[spec["fam"]][0], side=side, i=i0, entry=float(entry), stop=float(stop),
                         target=float(entry + side * spec["rr"] * risk), time=g.index[i0]))
    return sigs, info


def study_spec(spec):
    """A looser version of a strategy for the models' practice set: both sides, no volume / stop / market filter."""
    return dict(spec, side="both", nifty="any", relvol=0.0, minstop=0.0)


# ====================================================================== recommendation panel
# Eight specialist sub-agents (one per setup family) each nominate their best variant every day; a Selector
# picks one specialist — or stands aside. The whole procedure only ever looks at days before the one it acts on,
# so its record is out-of-sample end to end (both the specialists' tuning and the Selector's choice).
PANEL_MINSTOP = 0.8      # stops at least 0.8% away: round-trip costs stay ≈ 0.12R instead of 0.3R+
SPEC_WIN = 15            # sessions a specialist looks back over to tune itself
CHIEF_WIN = 20           # sessions of specialist track record the Selector compares
MIN_N_SPEC = 8           # trades a variant needs in the window to be eligible
MIN_N_CHIEF = 8          # trades of track record a specialist needs to be picked
Z_LCB = 1.0              # confidence haircut: mean − 1 standard error


def _lcb(n, s, q, z=Z_LCB):
    """Lower confidence bound of the mean R (vectorised); -inf where n < 2."""
    n = np.asarray(n, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = s / n
        var = np.maximum(q / n - mean ** 2, 0) * n / np.maximum(n - 1, 1)
        out = mean - z * np.sqrt(var / n)
    return np.where(n >= 2, out, -np.inf)


def panel(raw, today=None, minstop=PANEL_MINSTOP, has_idx=None):
    """Run the panel over every session. Returns today's decision, each specialist's state and the walk-forward
    record of the panel vs the classic engine, or None if there are too few sessions."""
    if raw is None or raw.empty:
        return None
    raw = raw.reset_index(drop=True)
    days_all = sorted(raw.day.unique())
    today = today or days_all[-1]
    days = [d for d in days_all if str(d) < str(today)]       # decisions never use the day being decided
    D = len(days)
    if D < SPEC_WIN + 5:
        return None
    di = {d: i for i, d in enumerate(days)}
    keep = raw.day.map(di).notna().values
    raw = raw[keep].reset_index(drop=True)
    dix = raw.day.map(di).astype(int).values
    has_idx = raw.nwith.notna().any() if has_idx is None else has_idx

    def daily(ix, rr):
        r = raw[f"r{rr:g}"].values[ix]
        d = dix[ix]
        return (np.bincount(d, minlength=D).astype(float), np.bincount(d, r, D), np.bincount(d, r * r, D),
                np.bincount(d, (r > 0).astype(float), D))

    def cum(a):
        return np.concatenate([np.zeros(a.shape[:-1] + (1,)), np.cumsum(a, axis=-1)], axis=-1)

    specs = {}
    for fam, sub in raw.groupby("fam", sort=False):
        cand, N, S, Q, W = [], [], [], [], []
        for vi, s2 in sub.groupby("vi"):
            prm = FAMILIES[fam][2][vi]
            side, rv, nw, sp = s2.side.values, np.nan_to_num(s2.relvol.values, nan=0), s2.nwith.values, s2.stop_pct.values
            for sd, nf, rl in product(SIDES, NIFTY if has_idx else ["any"], RELVOL):
                m = sp >= minstop
                if sd != "both":
                    m &= side == (1 if sd == "long" else -1)
                if nf == "with":
                    m &= nw == True                                              # noqa: E712
                if rl:
                    m &= rv >= rl
                ix = s2.index.values[m]
                if len(ix) < MIN_N_SPEC:
                    continue
                for rr in RRS:
                    n, s_, q, w = daily(ix, rr)
                    cand.append(dict(fam=fam, prm=prm, side=sd, nifty=nf, relvol=rl, minstop=minstop, rr=rr))
                    N.append(n), S.append(s_), Q.append(q), W.append(w)
        if cand:
            specs[fam] = dict(cand=cand, N=np.array(N), S=np.array(S), Q=np.array(Q), W=np.array(W))
    if not specs:
        return None

    # specialists: tune on the trailing SPEC_WIN sessions, act on the next one
    fams = list(specs)
    F = len(fams)
    rec = {k: np.zeros((F, D)) for k in "nsqw"}
    pick_ix = np.full((F, D + 1), -1)
    for f, fam in enumerate(fams):
        sp_ = specs[fam]
        cN, cS, cQ = cum(sp_["N"]), cum(sp_["S"]), cum(sp_["Q"])
        for d in range(SPEC_WIN, D + 1):
            a = d - SPEC_WIN
            n, s_, q = cN[:, d] - cN[:, a], cS[:, d] - cS[:, a], cQ[:, d] - cQ[:, a]
            sc = np.where(n >= MIN_N_SPEC, _lcb(n, s_, q), -np.inf)
            if not np.isfinite(sc).any():
                continue
            k = int(np.argmax(sc))
            pick_ix[f, d] = k
            if d < D:
                rec["n"][f, d], rec["s"][f, d] = sp_["N"][k, d], sp_["S"][k, d]
                rec["q"][f, d], rec["w"][f, d] = sp_["Q"][k, d], sp_["W"][k, d]

    # Selector: compare specialists on their own out-of-sample records, pick one or stand aside
    cn, cs, cq = cum(rec["n"]), cum(rec["s"]), cum(rec["q"])
    start = SPEC_WIN + 5
    choice = np.full(D + 1, -1)
    trail = np.full((F, D + 1), -np.inf)
    for d in range(start, D + 1):
        a = max(SPEC_WIN, d - CHIEF_WIN)
        n, s_, q = cn[:, d] - cn[:, a], cs[:, d] - cs[:, a], cq[:, d] - cq[:, a]
        sc = np.where((n >= MIN_N_CHIEF) & (pick_ix[:, d] >= 0), _lcb(n, s_, q), -np.inf)
        trail[:, d] = sc
        k = int(np.argmax(sc))
        if np.isfinite(sc[k]) and sc[k] > 0:
            choice[d] = k

    test = list(range(start, D))
    pn = np.array([rec["n"][choice[d], d] if choice[d] >= 0 else 0 for d in test])
    ps = np.array([rec["s"][choice[d], d] if choice[d] >= 0 else 0 for d in test])
    pw = np.array([rec["w"][choice[d], d] if choice[d] >= 0 else 0 for d in test])

    def summ(n, s_, w):
        N_ = float(np.sum(n))
        return dict(n=int(N_), exp=float(np.sum(s_) / N_) if N_ else np.nan, win=float(np.sum(w) / N_) if N_ else np.nan,
                    total=float(np.sum(s_)))
    record = summ(pn, ps, pw)
    record.update(days=len(test), aside_days=int(sum(choice[d] < 0 for d in test)),
                  first=str(days[start]) if test else None, last=str(days[D - 1]))
    # the classic engine on the same days, for comparison
    cm = ((raw.fam == "orb") & (raw.vi == 0)) | ((raw.fam == "vwap") & (raw.vi == 0))
    cm &= (raw.side == 1) & (np.nan_to_num(raw.relvol.values, nan=0) >= 1.5)
    cix = np.where(cm.values)[0]
    bn, bs, _, bw = daily(cix, 2.0)
    base = summ(bn[start:D], bs[start:D], bw[start:D])
    curve = pd.DataFrame({"day": [str(days[d]) for d in test], "Panel": np.cumsum(ps), "Classic engine": np.cumsum(bs[start:D]),
                          "picked": [FAMILIES[fams[choice[d]]][0] if choice[d] >= 0 else "stand aside" for d in test]})

    rows = []
    for f, fam in enumerate(fams):
        k = pick_ix[f, D]
        sp_ = specs[fam]
        mine = [d for d in test if choice[d] == f]
        own = summ(rec["n"][f, start:D], rec["s"][f, start:D], rec["w"][f, start:D])
        a = max(SPEC_WIN, D - CHIEF_WIN)
        tn, ts_ = cn[f, D] - cn[f, a], cs[f, D] - cs[f, a]
        rows.append(dict(fam=fam, name=FAMILIES[fam][0], spec=sp_["cand"][k] if k >= 0 else None,
                         label=label(sp_["cand"][k]) if k >= 0 else "— no eligible variant",
                         trail_n=int(tn), trail_exp=float(ts_ / tn) if tn else np.nan,
                         trail_lcb=float(trail[f, D]) if np.isfinite(trail[f, D]) else np.nan,
                         rec_n=own["n"], rec_exp=own["exp"], rec_win=own["win"], chosen_days=len(mine),
                         selected=bool(choice[D] == f)))
    spec_tab = pd.DataFrame(rows).sort_values(["selected", "trail_lcb"], ascending=False, na_position="last")
    pick = None
    if choice[D] >= 0:
        r0 = spec_tab.iloc[0]
        pick = dict(fam=r0.fam, name=r0["name"], spec=r0.spec, label=r0.label, lcb=r0.trail_lcb, trail_exp=r0.trail_exp,
                    trail_n=int(r0.trail_n), rules=describe(r0.spec))
    return dict(pick=pick, specialists=spec_tab, record=record, base=base, curve=curve, sessions=D,
                today=str(today), minstop=minstop, n_candidates=int(sum(len(v["cand"]) for v in specs.values())))


def panel_trusted(res, min_trades=20):
    """The panel's picks are only treated as real recommendations when its own walk-forward record is positive."""
    return bool(res and res["pick"] and res["record"]["n"] >= min_trades and res["record"]["exp"] > 0)


def panel_proposal(res, cfg):
    if not res or cfg.get("panel"):
        return None
    r, b = res["record"], res["base"]
    if r["n"] < 20 or not (r["exp"] > 0) or not (r["exp"] >= (b["exp"] if b["n"] else -9) + 0.05):
        return None
    why = (f"Eight specialist sub-agents (one per setup family) tune themselves daily on the last {SPEC_WIN} sessions, and "
           f"the Selector picks the specialist with the best confidence-adjusted record — or stands aside. Run "
           f"walk-forward over {r['days']} sessions it never peeked at: {r['n']} trades, won {r['win'] * 100:.0f}%, "
           f"{r['exp']:+.2f}R per trade ({r['total']:+.1f}R total), standing aside on {r['aside_days']} days — vs "
           f"{b['exp']:+.2f}R for the classic engine on the same days. All stops ≥ {res['minstop']:g}% so costs stay small. "
           f"Approving lets the panel choose the strategy each morning; trades are held to stop, target or 15:15, as tested.")
    return dict(id="Panel:on", agent="Selector", title="Let the recommendation panel pick the strategy each day",
                why=why, change={"panel": True, "paper_window_min": 0, "exit_policy": "fixed"}, whatif=False)

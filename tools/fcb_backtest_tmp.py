"""Temporary: real-data check of the first-candle breakout variants (prints to the Actions log)."""
import sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "apps/stock-lab")
import numpy as np, pandas as pd
import cheetah_engine as ce, research as rs

data = ce.yahoo_intraday(ce.WATCHLIST, 59)
print("symbols with data:", len(data), "of", len(ce.WATCHLIST))
if not data:
    sys.exit("NO DATA")
nd = sorted({d for df in data.values() for d in set(df.index.date)})
print("sessions:", len(nd), nd[0], "->", nd[-1])

V = [("USER  2 closes, VWAP/2-candle stop", dict(nc=2, stop="vwap")),
     ("      2 closes, 2-candle stop", dict(nc=2, stop="bars")),
     ("      2 closes, far side of 9:15 candle", dict(nc=2, stop="range")),
     ("      3 closes, VWAP/2-candle stop", dict(nc=3, stop="vwap")),
     ("      3 closes, far side of 9:15 candle", dict(nc=3, stop="range")),
     ("abl:  1 close, VWAP stop", dict(nc=1, stop="vwap")),
     ("abl:  2 closes, VWAP stop, NO volume rule", dict(nc=2, stop="vwap", vol=False)),
     ("abl:  2 closes, VWAP stop, NO range-size rule", dict(nc=2, stop="vwap", rng=False)),
     ("abl:  2 closes, range stop, NO volume rule", dict(nc=2, stop="range", vol=False))]
rs.FAMILIES = {"fcb": ("1ST-CANDLE", "", [p for _, p in V]),
               "orb": rs.FAMILIES["orb"]}
raw = rs.raw_signals(data, None, 0.10)
raw["tm"] = raw.i * 5 + 555
print("signals:", len(raw))

def line(name, x):
    if len(x) < 5:
        return f"{name:<48} n={len(x):>4}"
    out = f"{name:<48} n={len(x):>4} stop%={x.stop_pct.median():.2f}"
    for rr in (1.0, 1.5, 2.0, 3.0):
        r = x[f"r{rr:g}"]
        out += f" | {rr:g}R: win {100 * (r > 0).mean():.0f}% avg {r.mean():+.3f}"
    return out

f = raw[raw.fam == "fcb"]
for title, m in [("ALL STOPS", f.stop_pct > 0), ("STOP >= 0.4%", f.stop_pct >= 0.4), ("STOP >= 0.8%", f.stop_pct >= 0.8)]:
    print(f"\n=== {title} (both sides, cost 0.10% round trip, R = multiples of risk) ===")
    for vi, (name, _) in enumerate(V):
        print(line(name, f[m & (f.vi == vi)]))
print("\n=== base rule (vi 0) by side, all stops ===")
for sd, nm in ((1, "long"), (-1, "short")):
    print(line(nm, f[(f.vi == 0) & (f.side == sd)]))
print("\n=== base rule (vi 0) by entry time, all stops ===")
b = f[f.vi == 0]
for lo, hi, nm in ((0, 600, "before 10:00"), (600, 660, "10:00-11:00"), (660, 780, "11:00-13:00"), (780, 900, "13:00-14:40")):
    print(line(nm, b[(b.tm >= lo) & (b.tm < hi)]))
print("\n=== far-side stop (vi 2) by entry time, all stops ===")
b = f[f.vi == 2]
for lo, hi, nm in ((0, 600, "before 10:00"), (600, 660, "10:00-11:00"), (660, 780, "11:00-13:00"), (780, 900, "13:00-14:40")):
    print(line(nm, b[(b.tm >= lo) & (b.tm < hi)]))
print("\n=== far-side stop (vi 2) by opening relative volume ===")
for lo, hi in ((0, 1.2), (1.2, 1.5), (1.5, 99)):
    print(line(f"relvol {lo}-{hi}", b[(b.relvol >= lo) & (b.relvol < hi)]))
print("\n=== reference: classic ORB 15-min far stop ===")
print(line("ORB", raw[(raw.fam == "orb") & (raw.vi == 0)]))
# day-level dispersion for the user rule and the far-side variant at 2R
for vi in (0, 2):
    x = f[f.vi == vi].groupby("day").r2.sum()
    print(f"vi {vi}: days {len(x)}, positive days {100 * (x > 0).mean():.0f}%, total {x.sum():+.1f}R, worst day {x.min():+.1f}R")

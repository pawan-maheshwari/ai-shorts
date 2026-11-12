"""Temporary: real-data walk-forward record of the recommendation panel and Scout (prints to the Actions log)."""
import sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "apps/stock-lab")
import numpy as np, pandas as pd
import cheetah_engine as ce, research as rs

data = ce.yahoo_intraday(ce.WATCHLIST, 59)
print("symbols with data:", len(data))
idx = None
try:
    import agents
    idx = agents.fetch_index(False)
    print("index bars:", None if not idx or idx.get("intraday") is None else len(idx["intraday"]))
except Exception as e:
    print("index unavailable:", type(e).__name__, str(e)[:120])
raw = rs.raw_signals(data, idx, 0.10)
days = sorted(raw.day.unique())
print("sessions:", len(days), days[0], "->", days[-1], "| signals:", len(raw))

print("\n=== every family, all variants pooled, 2R target, after costs ===")
for fam, x in raw.groupby("fam"):
    y = x[x.stop_pct >= 0.8]
    print(f"{rs.FAMILIES[fam][0]:<11} all: n={len(x):>5} win {100*(x.r2>0).mean():.0f}% avg {x.r2.mean():+.3f}R | stops>=0.8%: n={len(y):>5} win {100*(y.r2>0).mean() if len(y) else 0:.0f}% avg {y.r2.mean() if len(y) else float('nan'):+.3f}R")

for label, today in (("as of the latest session", None),):
    res = rs.panel(raw, today=today)
    if not res:
        print("panel: not enough sessions"); continue
    r, b = res["record"], res["base"]
    print(f"\n=== recommendation panel walk-forward ({label}) ===")
    print(f"panel:   {r['n']} trades over {r['days']} sessions ({r['first']} -> {r['last']}), win {100*r['win']:.0f}%, {r['exp']:+.3f}R per trade, total {r['total']:+.1f}R, stood aside {r['aside_days']} days")
    print(f"classic: {b['n']} trades, win {100*b['win'] if b['n'] else 0:.0f}%, {b['exp']:+.3f}R per trade, total {b['total']:+.1f}R")
    print("today's pick:", res["pick"]["label"] if res["pick"] else "STAND ASIDE")
    t = res["specialists"]
    for _, q in t.iterrows():
        print(f"  {q['name']:<11} own out-of-sample: n={q.rec_n:>4} win {100*q.rec_win if q.rec_n else 0:.0f}% {q.rec_exp if q.rec_n else float('nan'):+.3f}R | chosen {q.chosen_days} days | now: {q.label}")
    print("picked by day:", res["curve"].picked.value_counts().to_dict())

print("\n=== Scout: discovery (older 60%) vs confirmation (newest 40%) ===")
try:
    out = rs.run(data, idx, 0.10, objective="exp")
    keys = [k for k in out.keys()] if isinstance(out, dict) else type(out)
    print("keys:", keys)
    for k in ("n_candidates", "n_tested", "n_disc", "n_confirmed", "days", "period", "cut"):
        if isinstance(out, dict) and k in out: print(k, "=", out[k])
    tab = out.get("table") if isinstance(out, dict) else None
    if tab is None:
        for k, v in out.items():
            if isinstance(v, pd.DataFrame): tab = v; print("table key:", k); break
    if tab is not None:
        print("columns:", list(tab.columns))
        pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
        print(tab.head(8).to_string(index=False)[:2500])
except Exception as e:
    print("scout failed:", type(e).__name__, str(e)[:200])

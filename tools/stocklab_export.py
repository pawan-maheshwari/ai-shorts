"""
Export Stock Lab's raw market data for offline study.

Downloads the same ~59 sessions of 5-minute bars the app uses (watchlist + Nifty + VIX) and the full table of
candidate signals every specialist family can generate (research.raw_signals), and writes them to ./study/.
The workflow publishes that folder to the `stocklab-study` branch. Nothing in the app or database is touched.
"""
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "apps", "stock-lab"))

import agents as ag  # noqa: E402
import cheetah_engine as ce  # noqa: E402
import research as rs  # noqa: E402

OUT = "study"


def main():
    os.makedirs(OUT, exist_ok=True)
    syms = sorted({s.strip().upper() for s in ag.DEFAULT_CONFIG["watchlist"] if s.strip()})
    extra = [s for s in rs.FOCUS_TOP if s not in syms]
    syms += extra
    data = ce.yahoo_intraday(syms, 59)
    print(f"bars for {len(data)} of {len(syms)} symbols")
    frames = []
    for s, df in data.items():
        d = df.copy()
        d["symbol"] = s
        frames.append(d)
    bars = pd.concat(frames).reset_index().rename(columns={"index": "ts"})
    bars.to_csv(f"{OUT}/bars.csv.gz", index=False)
    idx = ag.fetch_index(False, data)
    for k in ("intraday", "daily", "vix"):
        v = idx.get(k)
        if v is not None and len(v):
            (v.to_frame(k) if isinstance(v, pd.Series) else v).to_csv(f"{OUT}/index_{k}.csv")
    raw = rs.raw_signals(data, idx, float(ag.DEFAULT_CONFIG["cost_pct"]))
    raw.to_csv(f"{OUT}/raw_signals.csv.gz", index=False)
    print(f"raw signals: {len(raw):,} rows, {raw.day.nunique()} sessions")
    with open(f"{OUT}/README.txt", "w") as f:
        f.write(f"symbols={len(data)} sessions={raw.day.nunique()} raw_rows={len(raw)} cost_pct={ag.DEFAULT_CONFIG['cost_pct']}\n")


if __name__ == "__main__":
    main()

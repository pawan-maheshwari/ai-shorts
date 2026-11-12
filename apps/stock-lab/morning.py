"""Morning setup — a pre-market watchlist, then the 9:15 candle levels once the session opens.

Before 9:15 there is no price for today (the data feed has no NSE pre-open session), so the list is built
from what the completed sessions show: unusual volume, a close at the edge of the day's range, a narrow
range. From 9:20 it adds each stock's first-candle high and low — the levels the 1ST-CANDLE specialist
watches. It is a watchlist of conditions, not a prediction and not a recommendation.
"""
import numpy as np
import pandas as pd

import cheetah_engine as ce


def table(data, today=None, lookback=10):
    """One row per symbol. `today` (a date) marks the session in progress, if the data has one."""
    rows = []
    for s, df in data.items():
        days = ce.session_days(df)
        live = None
        if today is not None and days and days[-1][0] == today:
            live, days = days[-1][1], days[:-1]
        if len(days) < 3:
            continue
        d, g = days[-1]
        hi, lo, cl = float(g.High.max()), float(g.Low.min()), float(g.Close.iloc[-1])
        before = float(days[-2][1].Close.iloc[-1])
        base = [float(x.Volume.sum()) for _, x in days[-1 - lookback:-1]]
        vol_x = float(g.Volume.sum()) / np.mean(base) if base and np.mean(base) > 0 else np.nan
        rngs = [(float(x.High.max()) - float(x.Low.min())) / float(x.Close.iloc[-1]) for _, x in days[-4:]]
        pos = (cl - lo) / (hi - lo) * 100 if hi > lo else 50.0
        why = []
        if np.isfinite(vol_x) and vol_x >= 1.3:
            why.append(f"volume {vol_x:.1f}× normal")
        if pos >= 80:
            why.append("closed near the day's high")
        elif pos <= 20:
            why.append("closed near the day's low")
        if len(rngs) == 4 and rngs[-1] == min(rngs):
            why.append("narrowest range in 4 sessions")
        r = dict(symbol=s, prev_day=d, focus=len(why), why="; ".join(why) or "—", prev_close=cl,
                 prev_chg=(cl / before - 1) * 100 if before else np.nan, vol_x=vol_x,
                 gap=np.nan, fc_high=np.nan, fc_low=np.nan, last=np.nan, state="")
        if live is not None and len(live):
            f = live.iloc[0]
            r.update(gap=(float(f.Open) / cl - 1) * 100, fc_high=float(f.High), fc_low=float(f.Low),
                     last=float(live.Close.iloc[-1]))
            c = live.Close.values[1:]
            if not len(c):
                r["state"] = "waiting for the next close"
            else:
                side = np.where(c > r["fc_high"], 1, np.where(c < r["fc_low"], -1, 0))
                k = 1
                while k < len(side) and side[-1 - k] == side[-1]:
                    k += 1
                r["state"] = ("inside the range" if side[-1] == 0 else
                              f"{k} close{'s' if k > 1 else ''} {'above' if side[-1] == 1 else 'below'}")
        rows.append(r)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["focus", "vol_x"], ascending=False, na_position="last").reset_index(drop=True)


def render(st, data, today, top=15):
    """Draw the panel. Returns False when there is nothing to show."""
    T = table(data, today)
    if T.empty:
        return False
    live = T.fc_high.notna().any()
    st.markdown(f"**🌅 Morning setup** · {'9:15 candle levels' if live else 'watchlist for the next open'} "
                f"· from the session of {pd.Timestamp(T.prev_day.iloc[0]):%d %b}")
    show = T.head(top)
    cols = {"Stock": show.symbol, "In focus because": show.why,
            "Prev close": show.prev_close.map("{:,.2f}".format),
            "Prev day": show.prev_chg.map(lambda v: "—" if pd.isna(v) else f"{v:+.2f}%")}
    if live:
        cols.update({"Gap": show.gap.map(lambda v: "—" if pd.isna(v) else f"{v:+.2f}%"),
                     "Long above": show.fc_high.map(lambda v: "—" if pd.isna(v) else f"{v:,.2f}"),
                     "Short below": show.fc_low.map(lambda v: "—" if pd.isna(v) else f"{v:,.2f}"),
                     "Now": show["last"].map(lambda v: "—" if pd.isna(v) else f"{v:,.2f}"),
                     "So far": show.state})
    st.dataframe(pd.DataFrame(cols), hide_index=True, width="stretch", height=min(35 * (len(show) + 1) + 3, 460))
    st.caption(("Levels are the 9:15 candle's high and low. The 1ST-CANDLE specialist acts only after two 5-minute "
                "closes beyond a level, on the right side of VWAP, with above-average volume, and not after 14:40. "
                if live else
                "Built from completed sessions only: the data feed has no pre-open prices, so today's gap and the "
                "9:15 candle levels appear here from about 9:20 IST. ")
               + "A watchlist of conditions for practice, not a prediction or a recommendation.")
    return True

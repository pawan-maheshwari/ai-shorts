"""
CHEETAH Signals — web app for the CHEETAH engine (NSE)
Run:   pip install -r requirements.txt
       streamlit run app.py
Educational tool. Not investment advice.
"""
import argparse
from datetime import time as dtime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import cheetah_engine as ce

st.set_page_config(page_title="AIShorts Stock Lab (Beta)", page_icon="📈", layout="wide")


# ----------------------------------------------------------------------------- helpers
def intraday_args(**kw):
    base = dict(setup="both", shorts=False, or_bars=3, min_relvol=1.5, max_gap=4.0, min_or_pct=0.3,
                max_or_pct=2.0, rr=2.0, last_entry="14:30", exit_time="15:15", capital=200000.0,
                risk_pct=0.5, leverage=5.0, cost_pct=0.10, max_trades=3, max_losses=2)
    base.update(kw)
    return argparse.Namespace(**base)


@st.cache_data(ttl=60, show_spinner="Fetching 5-minute bars…")
def load_intraday(symbols, demo, days=15):
    return ce.demo_intraday(days=max(days, 15)) if demo else ce.yahoo_intraday(list(symbols), days)


@st.cache_data(ttl=3600, show_spinner="Fetching daily prices…")
def load_daily(symbols, demo):
    if demo:
        fund, px, _ = ce.demo_swing(panel=False)
        return px
    start = (pd.Timestamp.today() - pd.Timedelta(days=500)).strftime("%Y-%m-%d")
    return ce.yahoo_daily(list(symbols), start)


def inr(x, dp=2):
    return "—" if pd.isna(x) else f"₹{x:,.{dp}f}"


def intraday_chart(g, row, a):
    tp = (g.High + g.Low + g.Close) / 3
    vwap = (tp * g.Volume).cumsum() / g.Volume.cumsum().replace(0, np.nan)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=g.index, open=g.Open, high=g.High, low=g.Low, close=g.Close, name="Price",
                                 increasing_line_color="#16a34a", decreasing_line_color="#dc2626"))
    fig.add_trace(go.Scatter(x=g.index, y=vwap, name="VWAP", line=dict(color="#7c3aed", width=1.6)))
    lines = [("OR high", row.or_high, "#64748b", "dot"), ("OR low", row.or_low, "#64748b", "dot")]
    if not pd.isna(row.entry):
        lines += [("Entry", row.entry, "#2563eb", "solid"), ("Stop", row.stop, "#dc2626", "dash"),
                  ("Target", row.target, "#16a34a", "dash")]
    for name, y, col, dash in lines:
        fig.add_hline(y=y, line=dict(color=col, dash=dash, width=1.2),
                      annotation_text=f"{name} {y:,.2f}", annotation_position="right",
                      annotation_font_size=10)
    if row.signal_time is not None and not pd.isna(row.entry):
        fig.add_trace(go.Scatter(x=[row.signal_time], y=[row.entry], mode="markers", name="Signal",
                                 marker=dict(symbol="triangle-up" if row.side == 1 else "triangle-down",
                                             size=13, color="#2563eb")))
    fig.update_layout(height=380, margin=dict(l=10, r=70, t=10, b=10), xaxis_rangeslider_visible=False,
                      legend=dict(orientation="h", y=1.08), template="plotly_white")
    return fig


# ----------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Stock Lab · Beta")
    demo = st.toggle("Demo data (no market feed)", value=False,
                     help="Synthetic prices — use to try the app outside market hours.")
    st.subheader("Intraday settings")
    capital = st.number_input("Capital (₹)", 10000, 100_000_000, 200000, step=10000)
    risk_pct = st.slider("Risk per trade (%)", 0.1, 2.0, 0.5, 0.1)
    setup = st.selectbox("Setups", ["both", "orb", "vwap"],
                         format_func={"both": "ORB + VWAP pullback", "orb": "Opening range breakout",
                                      "vwap": "VWAP pullback"}.get)
    shorts = st.toggle("Include short trades", value=False)
    rr = st.slider("Target (× risk)", 1.0, 4.0, 2.0, 0.5)
    min_relvol = st.slider("Min relative volume", 1.0, 3.0, 1.5, 0.1,
                           help="First-15-min volume vs average of the prior 10 sessions.")
    cost_pct = st.number_input("Round-trip costs (%)", 0.0, 1.0, 0.10, 0.01,
                               help="Brokerage + STT + exchange + GST + stamp + slippage.")
    wl_text = st.text_area("Watchlist (NSE symbols)", ", ".join(ce.WATCHLIST), height=140)
    symbols = tuple(sorted({s.strip().upper() for s in wl_text.replace("\n", ",").split(",") if s.strip()}))
    st.subheader("Refresh")
    auto = st.toggle("Auto-refresh intraday", value=False)
    every = st.select_slider("Every", [60, 120, 300], 120, format_func=lambda s: f"{s // 60} min")

A = intraday_args(setup=setup, shorts=shorts, rr=rr, min_relvol=min_relvol, capital=float(capital),
                  risk_pct=risk_pct, cost_pct=cost_pct)

st.title("Stock Lab — CHEETAH Signals")
st.caption("BETA — under trials. Rule-based setups with entry, stop and target levels. Educational tool — not investment advice. "
           "Yahoo data can lag several minutes; confirm on your broker's chart before acting.")
if demo:
    st.info("Demo mode: synthetic prices. Switch off in the sidebar for the live market feed.")

tab_i, tab_s, tab_b = st.tabs(["⚡ Intraday", "📈 Swing picks", "🧪 Backtest"])


# ----------------------------------------------------------------------------- intraday tab
def intraday_view():
    now = pd.Timestamp.now(tz=ce.IST)
    if not demo:
        if now.weekday() >= 5:
            st.warning("Market closed (weekend) — showing the last session.")
        elif now.time() < dtime(9, 30):
            st.warning("Opening range completes at 9:30 IST. Signals appear after that.")
        elif now.time() > dtime(15, 30):
            st.info("Market closed — showing today's completed session.")
    data = load_intraday(symbols, demo)
    if not data:
        st.error("No data returned. Check symbols or try again in a minute.")
        return
    S, bars = ce.run_scan(data, A)
    if S.empty:
        st.error("Not enough history to scan.")
        return
    sig = S[S.setup.notna()].copy()
    sig["open"] = sig.status == "OPEN"
    sig = sig.sort_values(["open", "signal_time"], ascending=[False, False])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Session", str(S.date.iloc[0]))
    c2.metric("Stocks in play", int(S.in_play.sum()), help="Relative volume and gap within limits")
    c3.metric("Signals today", len(sig))
    c4.metric("Open signals", int(sig.open.sum()))
    st.caption(f"Updated {now:%H:%M:%S} IST · exit all positions by {A.exit_time} · "
               f"max {A.max_trades} trades / stop after {A.max_losses} losses per day")

    st.subheader("Signals")
    if sig.empty:
        st.write("No setups have triggered yet. Watch the trigger levels below.")
    for _, r in sig.iterrows():
        side = "LONG" if r.side == 1 else "SHORT"
        risk_rs = abs(r.entry - r.stop) * (r.qty or 0)
        icon = "🟢" if r.status == "OPEN" else ("✅" if "TARGET" in str(r.status) else "⛔" if "STOP" in str(r.status) else "⏹️")
        with st.container(border=True):
            h1, h2 = st.columns([3, 2])
            h1.markdown(f"### {r.symbol} · {side} · {r.setup}")
            h2.markdown(f"**{icon} {r.status}**  \nSignal at {r.signal_time:%H:%M}")
            m = st.columns(5)
            m[0].metric("Entry", inr(r.entry))
            m[1].metric("Stop-loss", inr(r.stop), f"{(r.stop / r.entry - 1) * 100:+.2f}%", delta_color="off")
            m[2].metric("Target", inr(r.target), f"{(r.target / r.entry - 1) * 100:+.2f}%", delta_color="off")
            m[3].metric("Quantity", f"{int(r.qty):,}" if not pd.isna(r.qty) else "—")
            m[4].metric("Risk", inr(risk_rs, 0))
            if r.status != "OPEN" and not pd.isna(r.exit_price):
                pnl = (r.exit_price - r.entry) * r.side * (r.qty or 0)
                st.caption(f"Exited at {inr(r.exit_price)} → {inr(pnl, 0)} before costs")
            else:
                st.caption(f"Exit: stop at {inr(r.stop)}, target at {inr(r.target)}, or at {A.exit_time} — whichever comes first.")
            with st.expander("Chart"):
                st.plotly_chart(intraday_chart(bars[r.symbol], r, A), width="stretch",
                                key=f"chart_{r.symbol}_{r.setup}")

    st.subheader("Watchlist")
    w = S[["symbol", "in_play", "gap_pct", "relvol", "last", "vs_vwap", "or_high", "or_low", "signal"]].copy()
    w.columns = ["Symbol", "In play", "Gap %", "Rel vol", "Last", "vs VWAP", "OR high", "OR low", "Signal / trigger"]
    st.dataframe(w.round(2), hide_index=True, width="stretch")
    pick = st.selectbox("Chart a stock", S.symbol.tolist(), key="wl_chart")
    st.plotly_chart(intraday_chart(bars[pick], S[S.symbol == pick].iloc[0], A), width="stretch",
                    key="wl_chart_fig")


with tab_i:
    if auto and hasattr(st, "fragment"):
        st.fragment(run_every=f"{every}s")(intraday_view)()
    else:
        if st.button("🔄 Refresh now"):
            load_intraday.clear()
        intraday_view()


# ----------------------------------------------------------------------------- swing tab
with tab_s:
    st.write("Upload a fundamentals export (e.g. Screener.in) with at least: symbol, pe, roe, eps, "
             "profit_growth_3y, sales_growth_3y, debt_to_equity, promoter_holding, mcap_cr. "
             "Optional columns (roce, pledge_pct, piotroski, peg, cfo, net_profit…) switch on the enhanced rules.")
    up = st.file_uploader("Fundamentals CSV", type="csv")
    fund = None
    if up is not None:
        fund = ce.read_fundamentals(up)
    elif demo:
        fund, _, _ = ce.demo_swing(panel=False)
    if fund is None:
        st.info("Upload a CSV, or switch on demo data in the sidebar to see how this works.")
    else:
        px = load_daily(tuple(sorted(fund.symbol.unique())), demo and up is None)
        d, skipped = ce.run_screen(fund, px)
        if skipped:
            st.caption("Rules skipped (columns missing): " + ", ".join(skipped))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Universe", len(d))
        c2.metric("Pass original", int(d.pass_original.sum()))
        c3.metric("Pass enhanced", int(d.pass_enhanced.sum()))
        c4.metric("Pass safety gates", int(d.pass_safety.sum()))
        mode = st.radio("Show", ["Score ranking (safety gates)", "Enhanced screen", "Original CHEETAH"],
                        horizontal=True)
        view = {"Score ranking (safety gates)": d[d.pass_safety], "Enhanced screen": d[d.pass_enhanced],
                "Original CHEETAH": d[d.pass_original]}[mode]
        view = view[view.stop.notna()]
        n = st.slider("How many", 5, 50, 20)
        out = view.head(n).copy()
        out["risk_pct"] = (out.price / out.stop - 1) * 100
        for c in ["s_quality", "s_growth", "s_value", "s_momentum"]:
            if c in out:
                out[c] = out[c] * 100
        cols = {"symbol": "Symbol", "price": "Price", "score": "Score", "s_quality": "Quality",
                "s_growth": "Growth", "s_value": "Value", "s_momentum": "Momentum",
                "entry_zone": "Entry", "stop": "Stop-loss", "risk_pct": "Risk %",
                "review_at": "Review / trim at", "fails": "Fails (enhanced rules)"}
        out = out[[c for c in cols if c in out]].rename(columns=cols)
        st.dataframe(out.round(1), hide_index=True, width="stretch")
        st.markdown(
            "**Exit rules for swing picks**\n"
            "- **Stop-loss:** exit on a daily close below the stop (the 200-day EMA, capped at 15% below price).\n"
            "- **Review at 2× risk:** consider booking part of the gain and trailing the stop up to the 50-day average.\n"
            "- **Re-check monthly:** exit if the stock drops out of the top 50 by score or fails two or more safety rules.\n"
            "- Read results, annual report and governance news before buying — this is a shortlist, not a verdict.")
        st.download_button("Download full ranked list", d.to_csv(index=False), "cheetah_ranked.csv", "text/csv")


# ----------------------------------------------------------------------------- backtest tab
with tab_b:
    st.write("Tests the intraday setups on recent 5-minute data with your sidebar settings, costs and daily "
             "limits. Yahoo provides about 60 days of 5-minute history.")
    days = st.slider("Days of history", 10, 59, 59)
    if st.button("Run intraday backtest", type="primary"):
        with st.spinner("Simulating trades…"):
            data = load_intraday(symbols, demo, days)
            T, tab = ce.run_intraday_backtest(data, A)
        if T.empty:
            st.warning("No trades triggered. Lower the relative-volume filter or add symbols.")
        else:
            net = T.pnl.sum()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Trades", len(T))
            c2.metric("Win rate", f"{(T.pnl > 0).mean() * 100:.0f}%")
            c3.metric("Net P&L", inr(net, 0))
            c4.metric("Costs paid", inr(T.costs.sum(), 0))
            eq = T.sort_values("exit_t").set_index("exit_t").pnl.cumsum()
            st.line_chart(eq, height=260)
            t2 = tab.reset_index()
            t2["side"] = t2.side.map({1: "Long", -1: "Short", "": ""}).fillna(t2.side)
            st.dataframe(t2.round(2), hide_index=True, width="stretch")
            if len(T) < 200:
                st.warning(f"Only {len(T)} trades — too few to trust. Paper trade before using real money.")
            elif net <= 0:
                st.error("Loses money after costs on this data. Don't trade it as-is.")
            else:
                st.success("Positive after costs on this sample. Paper trade for a few weeks to confirm.")
            st.download_button("Download trade log", T.to_csv(index=False), "intraday_trades.csv", "text/csv")
    st.divider()
    st.caption("Swing backtest (original vs enhanced vs score vs neural net) needs a dated fundamentals history "
               "file; run it from the command line: python cheetah_engine.py backtest-swing --fundamentals history.csv --ml")

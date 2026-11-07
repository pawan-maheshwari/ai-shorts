"""
Stock Lab (beta) — web app for the CHEETAH engine (NSE)
Run:   pip install -r requirements.txt
       streamlit run app.py
Educational tool. Not investment advice.
"""
import argparse
import html
import time
from datetime import time as dtime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import cheetah_engine as ce

st.set_page_config(page_title="AIShorts Stock Lab (Beta)", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")

UP, DOWN = "#0f7b3f", "#c2181f"

# ============================================================================ style
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@400;500;600;700;800&family=Newsreader:opsz,wght@6..72,400;6..72,600&family=JetBrains+Mono:wght@500;700&display=swap');
:root{--ink:#121212;--paper:#fff;--paper2:#f7f7f5;--rule:#dcdcd6;--meta:#666;--up:#0f7b3f;--down:#c2181f;
--upbg:#e7f4ec;--downbg:#fcebeb;--amber:#9a6700;--amberbg:#fff6dc;--blue:#1f5f99}
.block-container{padding-top:.6rem!important;padding-bottom:6rem!important;max-width:1180px}
header[data-testid="stHeader"]{background:transparent}
[data-testid="stSidebarCollapsedControl"],[data-testid="stExpandSidebarButton"]{background:#fff;border:1px solid var(--rule);border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.08)}
.sl *{box-sizing:border-box}
.sl{font-family:"Libre Franklin",system-ui,sans-serif;color:var(--ink)}
.num{font-family:"JetBrains Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--meta)}
/* masthead */
.mast{display:flex;align-items:flex-end;justify-content:space-between;gap:10px;flex-wrap:wrap;
 border-bottom:3px double var(--ink);padding:4px 0 8px;margin-bottom:6px}
.brand{font-family:"Newsreader",Georgia,serif;font-weight:600;font-size:34px;line-height:1;letter-spacing:-.5px}
.beta{display:inline-block;margin-left:10px;vertical-align:middle;background:var(--down);color:#fff;font-size:10px;
 font-weight:800;letter-spacing:1.4px;padding:4px 7px;border-radius:2px;transform:translateY(-4px)}
.mast-r{font-size:11px;font-weight:700;letter-spacing:1.3px;text-transform:uppercase;color:var(--meta)}
.dek{font-family:"Newsreader",Georgia,serif;font-style:italic;color:var(--meta);font-size:15px;margin:2px 0 4px}
/* status strip */
.status{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;background:var(--paper2);border:1px solid var(--rule);
 border-left:4px solid var(--ink);padding:9px 12px;font-size:13px;margin:4px 0 12px}
.status b{font-weight:700}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot.live{background:#16a34a;box-shadow:0 0 0 0 rgba(22,163,74,.6);animation:pulse 1.8s infinite}
.dot.closed{background:#9a9a94}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(22,163,74,.55)}70%{box-shadow:0 0 0 8px rgba(22,163,74,0)}100%{box-shadow:0 0 0 0 rgba(22,163,74,0)}}
@media (prefers-reduced-motion:reduce){.dot.live{animation:none}}
/* KPI tiles */
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 14px}
.kpi{border:1px solid var(--rule);background:#fff;padding:10px 12px}
.kpi label{display:block;font-size:10.5px;font-weight:700;letter-spacing:1.1px;text-transform:uppercase;color:var(--meta)}
.kpi b{display:block;font-size:24px;font-weight:800;line-height:1.15;margin-top:3px}
.kpi small{font-size:12px;color:var(--meta)}
@media (max-width:760px){.kpis{grid-template-columns:repeat(2,1fr)}.kpi:first-child{grid-column:1/-1}.brand{font-size:28px}}
/* section heads */
.sh{font-size:12px;font-weight:800;letter-spacing:1.6px;text-transform:uppercase;border-top:2px solid var(--ink);
 padding-top:7px;margin:18px 0 10px;display:flex;justify-content:space-between;align-items:baseline}
.sh span{font-weight:500;letter-spacing:.3px;text-transform:none;color:var(--meta);font-size:12px}
/* signal card */
.card{border:1px solid var(--rule);border-left:5px solid var(--ink);background:#fff;padding:12px 14px 10px;margin-bottom:4px}
.card.long{border-left-color:var(--up)} .card.short{border-left-color:var(--down)}
.card-h{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.sym{font-size:20px;font-weight:800;letter-spacing:.2px;margin-right:6px}
.chip{display:inline-block;font-size:11px;font-weight:800;letter-spacing:.8px;padding:3px 8px;border-radius:999px;
 text-transform:uppercase;margin-right:4px;vertical-align:middle}
.chip.long{background:var(--upbg);color:var(--up)} .chip.short{background:var(--downbg);color:var(--down)}
.chip.setup{background:var(--paper2);color:var(--ink);border:1px solid var(--rule)}
.chip.open{background:var(--amberbg);color:var(--amber)} .chip.win{background:var(--upbg);color:var(--up)}
.chip.loss{background:var(--downbg);color:var(--down)} .chip.time{background:var(--paper2);color:var(--meta)}
.px{font-size:13px;color:var(--meta);margin:6px 0 10px}
.px .num{color:var(--ink);font-weight:700;font-size:15px}
.lv{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}
.lv div{background:var(--paper2);padding:7px 9px}
.lv label{display:block;font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--meta)}
.lv b{display:block;font-size:16px;font-weight:700;margin-top:2px}
.lv small{font-size:11.5px;font-weight:600}
@media (max-width:640px){.lv{grid-template-columns:repeat(3,1fr)}}
.track{position:relative;height:34px;margin:14px 4px 2px}
.track .bar{position:absolute;left:0;right:0;top:13px;height:8px;border-radius:4px;
 background:linear-gradient(90deg,#f3c3c3 0%,#ececea var(--e),#bfe3cc 100%)}
.track .tick{position:absolute;top:8px;width:2px;height:18px;background:var(--ink)}
.track .now{position:absolute;top:7px;width:20px;height:20px;margin-left:-10px;border-radius:50%;border:3px solid #fff;
 box-shadow:0 1px 4px rgba(0,0,0,.35)}
.track .lab{position:absolute;top:-6px;font-size:10px;font-weight:700;color:var(--meta);transform:translateX(-50%);white-space:nowrap}
.tl{display:flex;justify-content:space-between;font-size:10.5px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;margin:0 0 4px}
.foot{font-size:12.5px;color:var(--meta);border-top:1px dashed var(--rule);padding-top:7px;margin-top:6px}
.empty{border:1px dashed var(--rule);padding:16px;text-align:center;color:var(--meta);font-size:14px;background:var(--paper2)}
.note{font-size:12px;color:var(--meta)}
/* tabs */
.stTabs [data-baseweb="tab-list"]{gap:4px;border-bottom:1px solid var(--rule)}
.stTabs [data-baseweb="tab"] p{font-family:"Libre Franklin",sans-serif;font-weight:700;font-size:14px;letter-spacing:.3px}
div[data-testid="stExpander"] details summary p{font-family:"Libre Franklin",sans-serif;font-weight:600;font-size:13px}
</style>""", unsafe_allow_html=True)

# floating back-to-top arrow (injected once into the app page)
_TOP_JS = """<script>
(function(){
 const d = window.parent.document;
 if (d.getElementById('sl-top')) return;
 const b = d.createElement('button');
 b.id = 'sl-top'; b.type = 'button'; b.title = 'Back to top'; b.setAttribute('aria-label','Back to top');
 b.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
 Object.assign(b.style, {position:'fixed', right:'16px', bottom:'84px', width:'46px', height:'46px', borderRadius:'50%',
   border:'none', background:'#121212', color:'#fff', zIndex:'99999', cursor:'pointer', display:'flex',
   alignItems:'center', justifyContent:'center', boxShadow:'0 6px 18px rgba(0,0,0,.25)', opacity:'0',
   pointerEvents:'none', transition:'opacity .25s, transform .25s', transform:'translateY(8px)'});
 const scrollers = () => [d.querySelector('[data-testid="stMain"]'), d.querySelector('[data-testid="stAppViewContainer"]'),
   d.scrollingElement].filter(Boolean);
 b.onclick = () => scrollers().forEach(s => s.scrollTo({top:0, behavior:'smooth'}));
 d.body.appendChild(b);
 const show = () => { const y = Math.max(...scrollers().map(s => s.scrollTop || 0));
   const on = y > 350; b.style.opacity = on ? '1' : '0'; b.style.pointerEvents = on ? 'auto' : 'none';
   b.style.transform = on ? 'translateY(0)' : 'translateY(8px)'; };
 d.addEventListener('scroll', show, true); setInterval(show, 1500);
})();
</script>"""
if hasattr(st, "iframe"):
    st.iframe(_TOP_JS, height=1)
else:  # older Streamlit
    import streamlit.components.v1 as components
    components.html(_TOP_JS, height=0)


# ============================================================================ helpers
def intraday_args(**kw):
    base = dict(setup="both", shorts=False, or_bars=3, min_relvol=1.5, max_gap=4.0, min_or_pct=0.3,
                max_or_pct=2.0, rr=2.0, last_entry="14:30", exit_time="15:15", capital=200000.0,
                risk_pct=0.5, leverage=5.0, cost_pct=0.10, max_trades=3, max_losses=2)
    base.update(kw)
    return argparse.Namespace(**base)


@st.cache_data(ttl=3600, show_spinner=False, max_entries=12)
def fetch_intraday(symbols, demo, days, token):
    """token changes whenever a refresh is due, so a new token = a fresh download."""
    data = ce.demo_intraday(days=max(days, 15)) if demo else ce.yahoo_intraday(list(symbols), days)
    return data, pd.Timestamp.now(tz=ce.IST)


@st.cache_data(ttl=3600, show_spinner="Fetching daily prices…")
def load_daily(symbols, demo):
    if demo:
        _, px, _ = ce.demo_swing(panel=False)
        return px
    start = (pd.Timestamp.today() - pd.Timedelta(days=500)).strftime("%Y-%m-%d")
    return ce.yahoo_daily(list(symbols), start)


def inr(x, dp=2):
    return "—" if x is None or pd.isna(x) else f"₹{x:,.{dp}f}"


def arrow(v, dp=2, suffix="%", plus=True):
    """▲ / ▼ with colour class."""
    if v is None or pd.isna(v):
        return '<span class="flat">—</span>'
    if abs(v) < 10 ** -dp:
        return f'<span class="flat">• {0:.{dp}f}{suffix}</span>'
    cls, a = ("up", "▲") if v > 0 else ("down", "▼")
    return f'<span class="{cls}">{a} {abs(v):.{dp}f}{suffix}</span>'


def arrow_txt(v, dp=2, suffix="%"):
    if v is None or pd.isna(v):
        return "—"
    if abs(v) < 10 ** -dp:
        return f"• {0:.{dp}f}{suffix}"
    return f'{"▲" if v > 0 else "▼"} {abs(v):.{dp}f}{suffix}'


def market_state(now):
    if now.weekday() >= 5:
        return False, "Market closed · weekend"
    if now.time() < dtime(9, 15):
        return False, "Pre-open · opening range completes 9:30"
    if now.time() <= dtime(15, 30):
        return True, "Market open"
    return False, "Market closed"


def fmt_interval(s):
    if s <= 0:
        return "off"
    return f"{s} sec" if s < 60 else (f"{s // 60} min" if s % 60 == 0 else f"{s / 60:.1f} min")


def kpis(items):
    cells = "".join(f'<div class="kpi"><label>{html.escape(l)}</label><b>{v}</b>'
                    f'{f"<small>{s}</small>" if s else ""}</div>' for l, v, s in items)
    st.markdown(f'<div class="sl kpis">{cells}</div>', unsafe_allow_html=True)


def section(title, aside=""):
    st.markdown(f'<div class="sl sh">{html.escape(title)}<span>{aside}</span></div>', unsafe_allow_html=True)


def intraday_chart(g, row):
    tp = (g.High + g.Low + g.Close) / 3
    vwap = (tp * g.Volume).cumsum() / g.Volume.cumsum().replace(0, np.nan)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=g.index, open=g.Open, high=g.High, low=g.Low, close=g.Close, name="Price",
                                 increasing=dict(line=dict(color=UP), fillcolor=UP),
                                 decreasing=dict(line=dict(color=DOWN), fillcolor=DOWN)))
    fig.add_trace(go.Scatter(x=g.index, y=vwap, name="VWAP", line=dict(color="#6d28d9", width=1.8)))
    lines = [("OR high", row.or_high, "#8a8a8a", "dot"), ("OR low", row.or_low, "#8a8a8a", "dot")]
    if not pd.isna(row.entry):
        lines += [("Entry", row.entry, "#1f5f99", "solid"), ("Stop", row.stop, DOWN, "dash"),
                  ("Target", row.target, UP, "dash")]
    keys = [y for n, y, *_ in lines if not n.startswith("OR")]
    gap = (g.High.max() - g.Low.min()) * 0.035
    for name, y, col, dash in lines:
        clash = name.startswith("OR") and any(abs(y - k) < gap for k in keys)
        fig.add_hline(y=y, line=dict(color=col, dash=dash, width=1.2),
                      annotation_text=None if clash else f"{name} {y:,.2f}",
                      annotation_position="right", annotation_font=dict(size=10, color=col))
    if row.signal_time is not None and not pd.isna(row.entry):
        fig.add_trace(go.Scatter(x=[row.signal_time], y=[row.entry], mode="markers", name="Signal",
                                 marker=dict(symbol="triangle-up" if row.side == 1 else "triangle-down",
                                             size=14, color=UP if row.side == 1 else DOWN,
                                             line=dict(color="#fff", width=1.5))))
    fig.update_layout(height=360, margin=dict(l=8, r=78, t=8, b=8), xaxis_rangeslider_visible=False,
                      legend=dict(orientation="h", y=1.1, font=dict(size=11)), template="plotly_white",
                      font=dict(family="Libre Franklin, sans-serif"), hovermode="x unified")
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#eeeeea")
    return fig


def signal_card(r, exit_time):
    side_cls = "long" if r.side == 1 else "short"
    side_txt = "▲ Long" if r.side == 1 else "▼ Short"
    st_txt = str(r.status)
    st_cls = "open" if st_txt == "OPEN" else "win" if "TARGET" in st_txt else "loss" if "STOP" in st_txt else "time"
    st_icon = {"open": "●", "win": "✓", "loss": "✕", "time": "■"}[st_cls]
    risk_ps = abs(r.entry - r.stop)
    qty = 0 if pd.isna(r.qty) else int(r.qty)
    # where price is now (or where it exited) on the stop → target track
    ref = r.last if st_cls == "open" or pd.isna(r.exit_price) else r.exit_price
    span = (r.target - r.stop)
    pos = float(np.clip((ref - r.stop) / span, 0, 1)) if span else 0.5
    epos = float(np.clip((r.entry - r.stop) / span, 0, 1)) if span else 0.5
    rmult = (ref - r.entry) * r.side / risk_ps if risk_ps else 0
    now_col = UP if rmult > 0 else DOWN if rmult < 0 else "#555"
    ref_lab = "Now" if st_cls == "open" else "Exit"
    stop_pct = (r.stop / r.entry - 1) * 100
    tgt_pct = (r.target / r.entry - 1) * 100
    if st_cls == "open":
        foot = (f"Exit at the stop, the target, or {exit_time} — whichever comes first. "
                f"Running <b class='{'up' if rmult >= 0 else 'down'}'>{rmult:+.2f}R</b> "
                f"(₹{(ref - r.entry) * r.side * qty:+,.0f} on {qty:,} shares, before costs).")
    else:
        foot = (f"Closed at <b class='num'>{inr(r.exit_price)}</b> → "
                f"<b class='{'up' if rmult >= 0 else 'down'}'>{rmult:+.2f}R · ₹{(ref - r.entry) * r.side * qty:+,.0f}</b> before costs.")
    h = (f'<div class="sl card {side_cls}">'
         f'<div class="card-h"><div><span class="sym">{html.escape(r.symbol)}</span>'
         f'<span class="chip {side_cls}">{side_txt}</span><span class="chip setup">{html.escape(r.setup)}</span></div>'
         f'<span class="chip {st_cls}">{st_icon} {html.escape(st_txt)}</span></div>'
         f'<div class="px">Last <span class="num">{inr(r.last)}</span> {arrow(r.chg_pct)} on the day · '
         f'signal {r.signal_time:%H:%M}</div>'
         f'<div class="lv">'
         f'<div><label>Entry</label><b class="num">{inr(r.entry)}</b></div>'
         f'<div><label>Stop-loss</label><b class="num down">{inr(r.stop)}</b><small>{arrow(stop_pct)}</small></div>'
         f'<div><label>Target</label><b class="num up">{inr(r.target)}</b><small>{arrow(tgt_pct)}</small></div>'
         f'<div><label>Quantity</label><b class="num">{qty:,}</b></div>'
         f'<div><label>Risk</label><b class="num">{inr(risk_ps * qty, 0)}</b></div></div>'
         f'<div class="track" style="--e:{epos * 100:.0f}%"><div class="bar"></div>'
         f'<div class="tick" style="left:{epos * 100:.1f}%"></div>'
         f'<div class="lab" style="left:{epos * 100:.1f}%">Entry</div>'
         f'<div class="now" style="left:{pos * 100:.1f}%;background:{now_col}"></div></div>'
         f'<div class="tl"><span class="down">Stop {inr(r.stop)}</span>'
         f'<span style="color:{now_col}">{ref_lab} {inr(ref)}</span><span class="up">Target {inr(r.target)}</span></div>'
         f'<div class="foot">{foot}</div></div>')
    st.markdown(h, unsafe_allow_html=True)


# ============================================================================ sidebar
with st.sidebar:
    st.markdown('<div class="sl" style="font-weight:800;font-size:18px">Settings</div>', unsafe_allow_html=True)
    demo = st.toggle("Demo data (no market feed)", value=st.query_params.get("demo") == "1",
                     help="Synthetic prices — use to try the app outside market hours.")
    st.caption("INTRADAY")
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
    wl_text = st.text_area("Watchlist (NSE symbols)", ", ".join(ce.WATCHLIST), height=150)
    symbols = tuple(sorted({s.strip().upper() for s in wl_text.replace("\n", ",").split(",") if s.strip()}))

A = intraday_args(setup=setup, shorts=shorts, rr=rr, min_relvol=min_relvol, capital=float(capital),
                  risk_pct=risk_pct, cost_pct=cost_pct)

# ============================================================================ masthead
st.markdown(
    '<div class="sl mast"><div><span class="brand">Stock Lab</span><span class="beta">Beta · Under trials</span></div>'
    '<div class="mast-r">CHEETAH engine · NSE · AIShorts</div></div>'
    '<div class="dek">Rule-based setups with entry, stop and target. Educational tool — not investment advice. '
    'Prices can lag a few minutes; confirm on your broker\'s chart. Settings: tap <b>›</b> top-left.</div>',
    unsafe_allow_html=True)
if demo:
    st.info("Demo mode: synthetic prices. Switch it off in Settings for the live market feed.")

tab_i, tab_s, tab_b = st.tabs(["⚡ Intraday", "📈 Swing picks", "🧪 Backtest"])

# ============================================================================ intraday
REFRESH = {"Off": 0, "30 seconds": 30, "1 minute": 60, "2 minutes": 120, "5 minutes": 300,
           "10 minutes": 600, "15 minutes": 900, "Custom…": -1}

with tab_i:
    ss = st.session_state
    ss.setdefault("tok", 0)
    ss.setdefault("tok_t", time.time())
    custom = st.session_state.get("refresh_choice") == "Custom…"
    cols = st.columns([2, 1.3, 1] if custom else [2, 1], vertical_alignment="bottom")
    choice = cols[0].selectbox("Auto-refresh", list(REFRESH), index=3, key="refresh_choice",
                               help="How often to pull fresh prices. New 5-minute bars arrive every 5 minutes; "
                                    "faster refresh updates the latest price inside the current bar.")
    interval = REFRESH[choice]
    if interval == -1:
        mins = cols[1].number_input("Every (minutes)", 0.5, 120.0, 3.0, 0.5, key="refresh_custom")
        interval = int(mins * 60)
    c3 = cols[-1]
    if c3.button("↻ Refresh now", width="stretch"):
        ss.tok += 1
        ss.tok_t = time.time()

    def intraday_view():
        now_t = time.time()
        if interval > 0 and now_t - ss.tok_t >= interval * 0.9:
            ss.tok += 1
            ss.tok_t = now_t
        with st.spinner("Pulling the latest 5-minute bars…"):
            data, fetched = fetch_intraday(symbols, demo, 15, ss.tok)
        now = pd.Timestamp.now(tz=ce.IST)
        is_open, mkt_txt = market_state(now)
        if demo:
            is_open, mkt_txt = True, "Demo session"
        if not data:
            st.error("No data returned. Check the symbols or tap Refresh in a minute.")
            return
        S, bars = ce.run_scan(data, A)
        if S.empty:
            st.error("Not enough history to scan.")
            return
        last_bar = pd.to_datetime(S.last_bar).max()
        nxt = (f"Next <b>{(fetched + pd.Timedelta(seconds=interval)):%H:%M:%S}</b> (every {fmt_interval(interval)})"
               if interval > 0 else "Auto-refresh <b>off</b>")
        st.markdown(
            f'<div class="sl status"><span><span class="dot {"live" if is_open else "closed"}"></span>'
            f'<b>{mkt_txt}</b></span>'
            f'<span>Last refreshed <b class="num">{fetched:%H:%M:%S} IST</b> · {fetched:%d %b}</span>'
            f'<span>Latest bar <b class="num">{last_bar:%H:%M}</b></span><span>{nxt}</span></div>',
            unsafe_allow_html=True)

        sig = S[S.setup.notna()].copy()
        sig["is_open"] = sig.status == "OPEN"
        sig = sig.sort_values(["is_open", "signal_time"], ascending=[False, False])
        adv, dec = int((S.chg_pct > 0).sum()), int((S.chg_pct < 0).sum())
        kpis([("Session", f'{pd.Timestamp(S.date.iloc[0]):%d %b %Y}', f"{len(S)} stocks scanned"),
              ("Breadth", f'<span class="up">▲{adv}</span> <span class="down">▼{dec}</span>', "up vs down on the day"),
              ("In play", int(S.in_play.sum()), f"rel. vol ≥ {A.min_relvol}×"),
              ("Signals", len(sig), "triggered today"),
              ("Open", int(sig.is_open.sum()), f"exit by {A.exit_time}")])

        section("Signals", f"max {A.max_trades} trades · stop after {A.max_losses} losses a day")
        if sig.empty:
            st.markdown('<div class="sl empty">No setups have triggered yet. Watch the trigger levels below — '
                        'a 5-minute close beyond the opening range with rising volume starts a breakout.</div>',
                        unsafe_allow_html=True)
        for _, r in sig.iterrows():
            signal_card(r, A.exit_time)
            with st.expander(f"Chart · {r.symbol}"):
                st.plotly_chart(intraday_chart(bars[r.symbol], r), width="stretch",
                                key=f"c_{r.symbol}_{r.setup}_{ss.tok}", config={"displayModeBar": False})

        section("Watchlist", "sorted by in play, then relative volume")
        w = pd.DataFrame({
            "Symbol": S.symbol,
            "Last ₹": S["last"].round(2),
            "Day": S.chg_pct.map(arrow_txt),
            "Gap": S.gap_pct.map(arrow_txt),
            "Rel vol": S.relvol.map(lambda v: "—" if pd.isna(v) else f"{v:.2f}×"),
            "VWAP": S.vs_vwap.map({"above": "▲ above", "below": "▼ below"}),
            "OR high": S.or_high.round(2), "OR low": S.or_low.round(2),
            "In play": S.in_play.map({True: "●", False: ""}),
            "Signal / trigger": S.signal,
        })

        def colour(v):
            if isinstance(v, str) and v.startswith("▲"):
                return f"color:{UP};font-weight:600"
            if isinstance(v, str) and v.startswith("▼"):
                return f"color:{DOWN};font-weight:600"
            return ""
        sty = w.style.map(colour, subset=["Day", "Gap", "VWAP"]).format({"Last ₹": "{:,.2f}", "OR high": "{:,.2f}",
                                                                          "OR low": "{:,.2f}"})
        st.dataframe(sty, hide_index=True, width="stretch", height=min(35 * (len(w) + 1) + 3, 560))
        pick = st.selectbox("Chart a stock", S.symbol.tolist(), key="wl_pick")
        st.plotly_chart(intraday_chart(bars[pick], S[S.symbol == pick].iloc[0]), width="stretch",
                        key=f"wl_fig_{ss.tok}", config={"displayModeBar": False})

    if interval > 0 and hasattr(st, "fragment"):
        st.fragment(run_every=interval)(intraday_view)()
    else:
        intraday_view()

# ============================================================================ swing
with tab_s:
    st.markdown('<div class="sl note">Upload a fundamentals export (e.g. Screener.in) with at least: symbol, pe, roe, '
                'eps, profit_growth_3y, sales_growth_3y, debt_to_equity, promoter_holding, mcap_cr. Optional columns '
                '(roce, pledge_pct, piotroski, peg, cfo, net_profit…) switch on the enhanced rules.</div>',
                unsafe_allow_html=True)
    up = st.file_uploader("Fundamentals CSV", type="csv")
    fund = None
    if up is not None:
        fund = ce.read_fundamentals(up)
    elif demo:
        fund, _, _ = ce.demo_swing(panel=False)
    if fund is None:
        st.markdown('<div class="sl empty">Upload a CSV, or switch on demo data in Settings to see how this works.</div>',
                    unsafe_allow_html=True)
    else:
        px = load_daily(tuple(sorted(fund.symbol.unique())), demo and up is None)
        d, skipped = ce.run_screen(fund, px)
        kpis([("Universe", len(d), "companies"), ("Original", int(d.pass_original.sum()), "pass CHEETAH"),
              ("Enhanced", int(d.pass_enhanced.sum()), "pass all rules"),
              ("Safety", int(d.pass_safety.sum()), "pass safety gates"),
              ("Prices as of", f'{px.index[-1]:%d %b}', "daily close")])
        if skipped:
            st.caption("Rules skipped (columns missing): " + ", ".join(skipped))
        mode = st.radio("Show", ["Score ranking", "Enhanced screen", "Original CHEETAH"], horizontal=True)
        view = {"Score ranking": d[d.pass_safety], "Enhanced screen": d[d.pass_enhanced],
                "Original CHEETAH": d[d.pass_original]}[mode]
        view = view[view.stop.notna()]
        n = st.slider("How many", 5, 50, 20)
        out = view.head(n)
        section("Picks", "entry, stop and review level for each")
        if out.empty:
            st.markdown('<div class="sl empty">Nothing passes this screen today.</div>', unsafe_allow_html=True)
        else:
            t = pd.DataFrame({
                "Symbol": out.symbol, "Price ₹": out.price, "Score": out.score.round(0),
                "Quality": (out.s_quality * 100).round(0) if "s_quality" in out else np.nan,
                "Growth": (out.s_growth * 100).round(0) if "s_growth" in out else np.nan,
                "Value": (out.s_value * 100).round(0) if "s_value" in out else np.nan,
                "Momentum": (out.s_momentum * 100).round(0) if "s_momentum" in out else np.nan,
                "6m": (out.mom_6 * 100).map(arrow_txt),
                "Entry": out.entry_zone,
                "Stop ₹": out.stop, "Stop": ((out.stop / out.price - 1) * 100).map(arrow_txt),
                "Review at ₹": out.review_at, "Upside": ((out.review_at / out.price - 1) * 100).map(arrow_txt),
                "Fails": out.fails,
            })
            sty = t.style.map(lambda v: (f"color:{UP};font-weight:600" if str(v).startswith("▲") else
                                         f"color:{DOWN};font-weight:600" if str(v).startswith("▼") else ""),
                              subset=["6m", "Stop", "Upside"]).format(
                {"Price ₹": "{:,.1f}", "Stop ₹": "{:,.1f}", "Review at ₹": "{:,.1f}", "Score": "{:.0f}",
                 "Quality": "{:.0f}", "Growth": "{:.0f}", "Value": "{:.0f}", "Momentum": "{:.0f}"})
            st.dataframe(sty, hide_index=True, width="stretch",
                         column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100,
                                                                                 format="%d")})
        st.markdown(
            '<div class="sl" style="font-size:14px;line-height:1.55;margin-top:10px">'
            '<b>Exit rules for swing picks</b><br>'
            '<span class="down">▼</span> <b>Stop-loss:</b> exit on a daily close below the stop (the 200-day EMA, '
            'never more than 15% below the price).<br>'
            '<span class="up">▲</span> <b>Review at 2× risk:</b> consider booking part of the gain and trailing the '
            'stop up to the 50-day average.<br>'
            '↻ <b>Re-check monthly:</b> exit if it drops out of the top 50 by score or fails two or more safety rules.<br>'
            '<span class="note">This is a shortlist, not a verdict — read results, the annual report and governance '
            'news before buying.</span></div>', unsafe_allow_html=True)
        st.download_button("Download full ranked list", d.to_csv(index=False), "cheetah_ranked.csv", "text/csv")

# ============================================================================ backtest
with tab_b:
    st.markdown('<div class="sl note">Tests the intraday setups on recent 5-minute data with your Settings, costs and '
                'daily limits. Yahoo provides about 60 days of 5-minute history.</div>', unsafe_allow_html=True)
    days = st.slider("Days of history", 10, 59, 59)
    if st.button("Run intraday backtest", type="primary"):
        with st.spinner("Simulating trades…"):
            data, fetched = fetch_intraday(symbols, demo, days, f"bt-{pd.Timestamp.now(tz=ce.IST):%Y%m%d%H}")
            T, tab = ce.run_intraday_backtest(data, A)
        if T.empty:
            st.warning("No trades triggered. Lower the relative-volume filter or add symbols.")
        else:
            net = T.pnl.sum()
            wins = T.pnl[T.pnl > 0].sum()
            loss = -T.pnl[T.pnl < 0].sum()
            kpis([("Trades", len(T), f"{T.date.nunique()} days"),
                  ("Win rate", f"{(T.pnl > 0).mean() * 100:.0f}%", f"avg {T.R.mean():+.2f}R"),
                  ("Net P&L", f'<span class="{"up" if net > 0 else "down"}">{"▲" if net > 0 else "▼"} {inr(abs(net), 0)}</span>',
                   "after costs"),
                  ("Profit factor", f"{wins / loss:.2f}" if loss else "—", "wins ÷ losses"),
                  ("Costs paid", inr(T.costs.sum(), 0), f"data to {fetched:%d %b %H:%M}")])
            eq = T.sort_values("exit_t").set_index("exit_t").pnl.cumsum()
            fig = go.Figure(go.Scatter(x=eq.index, y=eq.values, mode="lines", line=dict(color="#121212", width=2),
                                       fill="tozeroy", fillcolor="rgba(15,123,63,.08)" if net > 0 else "rgba(194,24,31,.08)"))
            fig.update_layout(height=280, margin=dict(l=8, r=8, t=8, b=8), template="plotly_white",
                              yaxis_title="Cumulative P&L (₹)", font=dict(family="Libre Franklin, sans-serif"))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            t2 = tab.reset_index()
            t2["side"] = t2.side.map({1: "▲ Long", -1: "▼ Short", "": ""}).fillna(t2.side)
            st.dataframe(t2.round(2), hide_index=True, width="stretch")
            if len(T) < 200:
                st.warning(f"Only {len(T)} trades — too few to trust. Paper trade before using real money.")
            elif net <= 0:
                st.error("Loses money after costs on this data. Don't trade it as-is.")
            else:
                st.success("Positive after costs on this sample. Paper trade for a few weeks to confirm.")
            st.download_button("Download trade log", T.to_csv(index=False), "intraday_trades.csv", "text/csv")
    st.caption("Swing backtest (original vs enhanced vs score vs neural net) needs a dated fundamentals history file; "
               "run it from the command line: python cheetah_engine.py backtest-swing --fundamentals history.csv --ml")

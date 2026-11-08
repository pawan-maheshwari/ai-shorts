"""
Stock Lab (beta) — web app for the CHEETAH engine (NSE)
Run:   pip install -r requirements.txt
       streamlit run app.py
Educational tool. Not investment advice.
"""
import argparse
import html
import json
import time
from datetime import time as dtime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import importlib
import os
import sys


def _fresh_local_modules():
    """Streamlit Cloud keeps imported helper modules in memory across redeploys, so an update can run new
    app.py against old store/agents code. Reload any local module whose file changed since it was loaded."""
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("cheetah_engine", "store", "agents"):          # dependency order
        mod = sys.modules.get(name)
        path = os.path.join(here, name + ".py")
        if mod is None or not os.path.exists(path):
            continue
        mtime = os.path.getmtime(path)
        if getattr(mod, "__stocklab_mtime__", None) != mtime:
            mod = importlib.reload(mod)
            mod.__stocklab_mtime__ = mtime


_fresh_local_modules()
import agents as ag  # noqa: E402
import cheetah_engine as ce  # noqa: E402
from store import SUPABASE_SQL, Store  # noqa: E402

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
/* agents */
.pulse{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:baseline;border:1px solid var(--rule);padding:8px 12px;
 margin:-4px 0 12px;font-size:12.5px;color:var(--meta);background:#fff}
.pulse b.rg{font-size:13px;letter-spacing:.4px;text-transform:uppercase;color:var(--ink)}
.pulse.on{border-left:4px solid var(--up)} .pulse.off{border-left:4px solid var(--down)}
.pulse.vol{border-left:4px solid #b45309} .pulse.mid{border-left:4px solid #9a9a94}
.agents{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 0}
.ag{font-size:11.5px;font-weight:700;padding:4px 9px;border-radius:4px;background:var(--paper2);border:1px solid var(--rule);color:var(--ink)}
.ag i{font-style:normal;font-weight:500;color:var(--meta);margin-right:4px}
.ag.good{background:var(--upbg);border-color:#bfe3cc;color:var(--up)} .ag.bad{background:var(--downbg);border-color:#f3c3c3;color:var(--down)}
.ag.warn{background:var(--amberbg);border-color:#f0d78c;color:var(--amber)}
.pm{margin-top:8px;font-size:12.5px;background:var(--downbg);color:#7a1015;padding:7px 10px;border-radius:4px}
.alert{display:flex;gap:10px;align-items:baseline;padding:7px 0;border-bottom:1px solid var(--rule);font-size:13px}
.alert .t{font-family:"JetBrains Mono",monospace;font-size:11.5px;color:var(--meta);min-width:44px}
.lesson{border:1px solid var(--rule);border-left:4px solid var(--down);padding:8px 11px;margin-bottom:6px;font-size:13.5px;background:#fff}
.lesson.favour{border-left-color:var(--up)} .lesson.weaker{border-left-color:#b45309}
.lesson small{color:var(--meta)}
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


def _secret(name):
    try:
        return st.secrets.get(name)
    except Exception:
        return None


API_KEY = _secret("ANTHROPIC_API_KEY")


@st.cache_resource
def _make_store(demo, schema):
    """Live data → Supabase if configured, else local file. Demo data never touches the live store.
    `schema` changes whenever store.py changes, so an app update never reuses a stale connection object."""
    if demo:
        import os
        return Store(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".data", "demo.db"))
    st_ = Store(_secret("SUPABASE_URL"), _secret("SUPABASE_KEY"))
    st_.purge()
    return st_


def _store_schema():
    import hashlib, inspect, store as _sm
    return hashlib.md5(inspect.getsource(_sm).encode()).hexdigest()[:10]


def get_store(demo):
    s_ = _make_store(demo, _store_schema())
    if not all(hasattr(s_, m) for m in ("kv_get", "kv_set", "trades", "events", "log")):
        _make_store.clear()
        s_ = _make_store(demo, _store_schema())
    return s_


@st.cache_data(ttl=3600, show_spinner=False, max_entries=6)
def fetch_index_cached(demo, token):
    return ag.fetch_index(demo, ce.demo_intraday(days=15) if demo else None)


@st.cache_data(ttl=3600, show_spinner=False)
def ban_cached(day):
    return ag.fo_ban_list()


@st.cache_data(ttl=1800, show_spinner=False, max_entries=300)
def sentinel_cached(sym, demo, day, use_llm):
    return ag.sentinel(sym, demo, API_KEY if use_llm else None, None if demo else ban_cached(day))


@st.cache_data(ttl=3600, show_spinner=False, max_entries=300)
def narrate_cached(key, status, row_json, mood_json, sent_json, score_json, use_llm):
    return ag.narrate(pd.Series(json.loads(row_json)), json.loads(mood_json), json.loads(sent_json),
                      json.loads(score_json), API_KEY if use_llm else None)


@st.cache_data(ttl=86400, show_spinner=False, max_entries=6)
def history_cached(demo, symbols, day, args_key, _a):
    """Point-in-time replay of recent sessions: every signal, its features at signal time, every exit policy."""
    data = ce.demo_intraday(days=45) if demo else ce.yahoo_intraday(list(symbols), 59)
    if not data:
        return [], {}
    idx = ag.fetch_index(demo, data) if demo else fetch_index_cached(False, f"train-{day}")
    return ag.replay(data, idx, _a)


@st.cache_data(ttl=86400, show_spinner=False, max_entries=6)
def train_coach(demo, symbols, day, args_key, _a):
    """Once a day: replay recent sessions so the Coach learns from every signal, not just the ones you saw."""
    rows, _ = history_cached(demo, symbols, day, args_key, _a)
    if not rows:
        return 0
    store = get_store(demo)
    store.upsert_trades(rows)
    store.log("Coach", "training", f"Studied {len(rows)} signals from the last {len({r['day'] for r in rows})} sessions",
              detail={"trades": len(rows)}, dkey=f"coach-train|{day}|{args_key}")
    return len(rows)


@st.cache_data(ttl=300, show_spinner=False, max_entries=4)
def coach_trades(demo, token):
    return get_store(demo).trades()


@st.cache_data(ttl=3600, show_spinner=False, max_entries=24)
def scorecard_cached(demo, symbols, day, args_key, cfg_json, risk_rs, live_token, _a):
    rows, corr = history_cached(demo, symbols, day, args_key, _a)
    tr = get_store(demo).trades()
    live = tr[tr.source == "live"] if not tr.empty else tr
    return ag.scorecard(rows, json.loads(cfg_json), live, corr, risk_rs)


def now_ist():
    return pd.Timestamp.now(tz=ce.IST)


def load_cfg(store):
    state = store.kv_get("agent_config")
    if not state or "active" not in state:
        p = ag.merged({})
        state = dict(version=1, active=p, history=[dict(version=1, ts=now_ist().isoformat(), by="system",
                                                        note="Initial default settings", change={}, params=p)])
        store.kv_set("agent_config", state)
    state["active"] = ag.merged(state["active"])
    return state


def save_cfg(store, state, params, note, by, change):
    v = int(state["version"]) + 1
    params = ag.merged(params)
    hist = (state.get("history") or []) + [dict(version=v, ts=now_ist().isoformat(), by=by, note=note,
                                                change=change, params=params)]
    store.kv_set("agent_config", dict(version=v, active=params, history=hist[-60:]))
    store.log("Reviewer", "config", f"Settings v{v} by {by}: {note}", detail=dict(change=change, version=v),
              dkey=f"cfg|{v}|{now_ist():%Y%m%d%H%M%S}")
    for c_ in [scorecard_cached]:
        c_.clear()
    return v


def cfg_diff(a, b):
    out = {}
    for k in b:
        if a.get(k) != b.get(k):
            out[k] = b[k] if k != "mults" else {m: v for m, v in b[k].items() if a.get(k, {}).get(m) != v}
    return out


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


def signal_card(r, exit_time, agt=None):
    side_cls = "long" if r.side == 1 else "short"
    side_txt = "▲ Long" if r.side == 1 else "▼ Short"
    st_txt = str(r.status)
    risk_ps = abs(r.entry - r.stop)
    if st_txt == "OPEN":
        st_cls = "open"
    else:
        _rr = (r.exit_price - r.entry) * r.side / risk_ps if risk_ps and not pd.isna(r.exit_price) else 0
        st_cls = "win" if _rr > 0.05 else "loss" if _rr < -0.05 else "time"
    st_icon = {"open": "●", "win": "✓", "loss": "✕", "time": "■"}[st_cls]
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
    if agt and agt.get("exit_r") is not None and st_cls != "open":
        rmult = agt["exit_r"]
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
         f'<div class="foot">{foot}</div>{agent_strip(agt)}</div>')
    st.markdown(h, unsafe_allow_html=True)


def agent_strip(a):
    if not a:
        return ""
    chips = []
    al = a["align"]
    chips.append(f'<span class="ag {"good" if al == "with" else "bad" if al == "against" else ""}"><i>Pulse</i>'
                 f'{"▲ with market" if al == "with" else "▼ against market" if al == "against" else "• market neutral"}</span>')
    lv = a["sentinel"]
    chips.append(f'<span class="ag {"bad" if lv == "HIGH" else "warn" if lv == "WATCH" else "good" if lv == "CLEAR" else ""}">'
                 f'<i>Sentinel</i>{"⚠ " if lv == "HIGH" else ""}{lv.title() if lv else "not checked"}</span>')
    sc = a["coach"]
    if sc.get("conf") is not None:
        v = sc["verdict"]
        chips.append(f'<span class="ag {"good" if v == "Favourable" else "bad" if v == "Caution" else ""}"><i>Coach</i>'
                     f'{sc["conf"] * 100:.0f}% win odds · {v}</span>')
    else:
        chips.append('<span class="ag"><i>Coach</i>learning</span>')
    if a.get("exp_r") is not None:
        er = a["exp_r"]
        chips.append(f'<span class="ag {"good" if er > 0.05 else "bad" if er < 0 else ""}"><i>Expected</i>'
                     f'{er:+.2f}R ≈ {"₹" if er >= 0 else "−₹"}{abs(er) * a["risk_rs"]:,.0f}</span>')
    ex = a["exit"]
    chips.append(f'<span class="ag"><i>Exit · {html.escape(a["policy"])}</i>'
                 + (f'stop now {inr(ex["stop_now"])}' if a["is_open"] else html.escape(a["status"])) + '</span>')
    v = a["risk_verdict"]
    chips.append(f'<span class="ag {"bad" if v == "BLOCK" else "warn" if v == "REDUCE" else "good"}"><i>Risk</i>'
                 f'{v} · qty {a["qty_final"]:,}</span>')
    pm = f'<div class="pm"><b>Coach post-mortem:</b> {html.escape(a["pm"]["primary"])}</div>' if a.get("pm") else ""
    return f'<div class="agents">{"".join(chips)}</div>{pm}'


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
    st.caption("AGENTS")
    cfg_slot = st.empty()
    st.caption("Claude API: " + ("connected — richer news reading and explanations" if API_KEY
                                 else "not set — agents run rule-based"))

A = intraday_args(setup=setup, shorts=shorts, rr=rr, min_relvol=min_relvol, capital=float(capital),
                  risk_pct=risk_pct, cost_pct=cost_pct)
STORE = get_store(demo)
CFG_STATE = load_cfg(STORE)
CFG = CFG_STATE["active"]
RISK_RS = A.capital * A.risk_pct / 100
ARGS_KEY = f"{A.setup}|{A.shorts}|{A.rr}|{A.min_relvol}|{A.cost_pct}"
ADMIN_PIN = _secret("ADMIN_PIN")
cfg_slot.caption(f"Agent settings v{CFG_STATE['version']} · exit: {CFG['exit_policy']} · Coach filter "
                 f"{'on' if CFG['coach_filter'] else 'off'} · review in 🎯 Agent scores")

# ============================================================================ masthead
st.markdown(
    '<div class="sl mast"><div><span class="brand">Stock Lab</span><span class="beta">Beta · Under trials</span></div>'
    '<div class="mast-r">CHEETAH engine · NSE · AIShorts</div></div>'
    '<div class="dek">Rule-based setups with entry, stop and target. Educational tool — not investment advice. '
    'Prices can lag a few minutes; confirm on your broker\'s chart. Settings: tap <b>›</b> top-left.</div>',
    unsafe_allow_html=True)
if demo:
    st.info("Demo mode: synthetic prices. Switch it off in Settings for the live market feed.")

tab_i, tab_s, tab_b, tab_g, tab_a = st.tabs(["⚡ Intraday", "📈 Swing picks", "🧪 Backtest", "🎯 Agent scores",
                                             "🗂 Audit log"])

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

        # ------------------------------------------------ agents
        store = STORE
        day = S.date.iloc[0]
        day_s = str(day)
        with st.spinner("Coach is studying recent sessions (once a day)…"):
            train_coach(demo, symbols, day_s, ARGS_KEY, A)
        idx = fetch_index_cached(demo, 0 if demo else ss.tok)
        mood = ag.pulse(idx, day, mults=CFG["mults"])
        size_mult = mood["mult"] if CFG["pulse_sizing"] else 1.0
        store.log("Pulse", "regime", f"{mood['regime']} — position size ×{size_mult:g}. " + "; ".join(mood["reasons"]),
                  detail=mood, dkey=f"pulse|{day_s}|{mood['regime']}")
        coach = ag.Coach(coach_trades(demo, ss.tok), CFG["half_life"], day)
        targets = list(dict.fromkeys(sig.symbol.tolist() + S[S.in_play].symbol.tolist()))[:12]
        sents = {sym: sentinel_cached(sym, demo, day_s, bool(API_KEY)) for sym in targets}
        for sym, v in sents.items():
            if v["level"] != "CLEAR":
                store.log("Sentinel", v["level"].lower(), f"{v['level']}: " + "; ".join(v["reasons"][:2]) +
                          (f" — {v['llm']}" if v.get("llm") else ""), symbol=sym, detail=v,
                          dkey=f"sentinel|{day_s}|{sym}|{v['level']}")
        if targets:
            store.log("Sentinel", "scan", f"Checked news, results dates and F&O ban for {len(targets)} stocks: "
                      f"{sum(v['level'] == 'HIGH' for v in sents.values())} high-risk, "
                      f"{sum(v['level'] == 'WATCH' for v in sents.values())} to watch",
                      dkey=f"sentinel-scan|{day_s}|{','.join(sorted(targets))}")
        for l in coach.lessons():
            store.log("Coach", "lesson", f"{l['direction'].title()}: {l['text']} — averages {l['avg_r']:+.2f}R, won "
                      f"{l['win_rate'] * 100:.0f}% of {l['n']} trades", detail=l,
                      dkey=f"lesson|{day_s}|{l['feature']}={l['bucket']}|{l['direction']}")

        agt, journal = {}, []
        idd = ag.index_day(idx, day)
        policy = CFG["exit_policy"]
        sig["key"] = [ag.trade_key(day, a_, b_, c_) for a_, b_, c_ in zip(sig.symbol, sig.setup, sig.side)]
        sig["qty_base"] = sig.qty
        for ix, r in sig.iterrows():
            key = r.key
            g = bars[r.symbol]
            sd = dict(i=g.index.get_loc(r.signal_time), side=int(r.side), entry=r.entry, stop=r.stop, target=r.target,
                      setup=r.setup)
            sims = {p: ag.simulate_exit(g, sd, p, A) for p in ag.EXIT_POLICIES}
            act = sims[policy]
            status = ag.exit_status(act)
            closed = status != "OPEN"
            sig.at[ix, "status"] = status
            sig.at[ix, "exit_price"] = act["exit"] if closed else np.nan
            sv = sents.get(r.symbol)
            news = "unknown" if sv is None else ("event" if sv["level"] != "CLEAR" else "clear")
            info = dict(relvol=r.relvol, gap_pct=r.gap_pct, or_pct=(r.or_high - r.or_low) / g.Open.iloc[0] * 100)
            m_at = ag.pulse(idx, day, asof=r.signal_time, mults=CFG["mults"])
            feats = ag.signal_features(g, sd, info, idd, news, mood=m_at["regime"])
            sc = coach.score(feats, CFG["coach_threshold"])
            pm = coach.postmortem(feats, act["r"]) if closed else None
            if closed:
                feats_j = dict(feats, _exits={p: [round(v["r"], 4), v["t"].isoformat()] for p, v in sims.items()},
                               _bias=m_at["bias"])
                journal.append(dict(key=key, ts=r.signal_time, day=day, source="live", symbol=r.symbol, setup=r.setup,
                                    side=int(r.side), entry=r.entry, stop=r.stop, target=r.target,
                                    exit=sims["fixed"]["exit"], r=sims["fixed"]["r"], status=sims["fixed"]["reason"],
                                    features=feats_j, reason=pm["primary"] if pm else None))
            if pm:
                store.log("Coach", "post-mortem", f"{r.symbol} {r.setup} lost {act['r']:+.2f}R. Main reason: {pm['primary']}",
                          symbol=r.symbol, detail=dict(pm, features=feats), dkey=f"pm|{key}")
            if act["note"]:
                store.log("Exit", "manage", f"{r.symbol} ({policy}): {act['note']}", symbol=r.symbol,
                          dkey=f"exit|{key}|{act['note']}")
            align = "with" if mood["bias"] == r.side else "against" if mood["bias"] == -r.side else "neutral"
            row_min = dict(symbol=r.symbol, side=int(r.side), setup=r.setup, entry=float(r.entry),
                           stop=float(r.stop), target=float(r.target))
            narr = narrate_cached(key, status, json.dumps(row_min), json.dumps(mood, default=float),
                                  json.dumps(sv or {}, default=str), json.dumps(sc, default=float), bool(API_KEY))
            store.log("Narrator", "explain", narr, symbol=r.symbol, dkey=f"narr|{key}")
            agt[key] = dict(align=align, sentinel=sv["level"] if sv else None, sent=sv, coach=sc, pm=pm, narr=narr,
                            feats=feats, exp_r=sc.get("exp_r"), risk_rs=RISK_RS, exit=act, sims=sims, policy=policy,
                            status=status, is_open=not closed, exit_r=act["r"] if closed else None)
        store.upsert_trades(journal)

        # Risk agent — chronological, so each decision only knows what had happened by then
        corr = ag.corr_pairs(data, sorted(set(sig.symbol)), day) if len(sig) > 1 else {}
        book = []
        for ix, r in sig.sort_values("signal_time").iterrows():
            a = agt[r.key]
            caution = CFG["coach_filter"] and a["coach"].get("verdict") == "Caution"
            if caution:
                verdict, rmul, why = "BLOCK", 0.0, [f"Coach filter: expected {a['exp_r']:+.2f}R is below "
                                                   f"{CFG['coach_threshold']:+.2f}R"]
            else:
                verdict, rmul, why = ag.risk_check(dict(symbol=r.symbol, side=int(r.side), setup=r.setup,
                                                        t=r.signal_time, exp_r=a["exp_r"], sentinel=a["sentinel"]),
                                                   book, CFG, corr)
            qty_final = int((0 if pd.isna(r.qty_base) else r.qty_base) * size_mult * rmul)
            a.update(risk_verdict=verdict, risk_why=why, risk_mult=rmul, qty_final=qty_final, caution=caution)
            sig.at[ix, "qty"] = qty_final
            if verdict != "BLOCK":
                book.append(dict(symbol=r.symbol, side=int(r.side), t=r.signal_time,
                                 t_exit=None if a["is_open"] else a["exit"]["t"],
                                 r=(a["exit"]["r"] if not a["is_open"] else 0) * size_mult * rmul))
            if verdict != "ALLOW":
                store.log("Risk", verdict.lower(), f"{verdict} {r.symbol} {r.setup}: {why[0]}", symbol=r.symbol,
                          dkey=f"risk|{r.key}|{verdict}")
        sig["blocked"] = [agt[k]["risk_verdict"] == "BLOCK" for k in sig.key]
        sig["is_open"] = sig.status == "OPEN"
        sig = sig.sort_values(["blocked", "is_open", "signal_time"], ascending=[True, False, False])

        # ------------------------------------------------ alerts
        ss.setdefault("seen", {})
        ss.setdefault("alerts", [])
        fresh = []
        def _alert(k, val, msg, sym=None, icon="🔔"):
            if ss.seen.get(k) == val:
                return
            ss.seen[k] = val
            fresh.append(dict(t=pd.Timestamp.now(tz=ce.IST), msg=msg, icon=icon))
            store.log("Alerts", "alert", msg, symbol=sym, dkey=f"alert|{day_s}|{k}|{val}")
        _alert("pulse", mood["regime"], f"Market mood: {mood['regime']} — position size ×{size_mult:g}", icon="🧭")
        for _, r in sig.iterrows():
            if r.blocked:
                continue
            a = agt[r.key]
            side_t = "LONG" if r.side == 1 else "SHORT"
            if r.status == "OPEN":
                _alert(r.key, "OPEN", f"New {side_t} · {r.symbol} {r.setup} — entry {inr(r.entry)}, stop {inr(r.stop)}, "
                       f"target {inr(r.target)}, qty {a['qty_final']:,} · expected {a['exp_r'] or 0:+.2f}R",
                       r.symbol, "🟢" if r.side == 1 else "🔴")
                if a["exit"]["note"]:
                    _alert(f"exit|{r.key}", a["exit"]["note"], f"Exit agent · {r.symbol}: {a['exit']['note']} — "
                           f"stop now {inr(a['exit']['stop_now'])}", r.symbol, "🚪")
            else:
                _alert(r.key, r.status, f"{r.symbol} {side_t} {r.setup}: {r.status} ({a['exit_r']:+.2f}R)", r.symbol,
                       "✅" if a["exit_r"] > 0 else "⛔" if a["exit_r"] < 0 else "⏹️")
            if a["sentinel"] == "HIGH":
                _alert(f"sent|{r.symbol}", "HIGH", f"Sentinel: {r.symbol} is high-risk today — {a['sent']['reasons'][0]}",
                       r.symbol, "⚠️")
        ss.alerts = (fresh[::-1] + ss.alerts)[:60]
        if not ss.get("alerts_init"):
            ss.alerts_init = True
            st.toast(f"{len(sig)} signal(s) today · market {mood['regime'].lower()}", icon="🔔")
        else:
            for a in fresh[:4]:
                st.toast(a["msg"], icon=a["icon"])

        cls = {"Risk-on": "on", "Mildly positive": "on", "Risk-off": "off", "Mildly negative": "off",
               "Volatile": "vol"}.get(mood["regime"], "mid")
        st.markdown(f'<div class="sl pulse {cls}"><b class="rg">🧭 Pulse · {mood["regime"]}</b>'
                    f'<span>Position size ×{size_mult:g}{"" if CFG["pulse_sizing"] else " (sizing off)"}</span>'
                    f'<span>Nifty {arrow(mood["nifty_chg"])}</span>'
                    f'<span>{" · ".join(html.escape(x) for x in mood["reasons"][:3])}</span></div>',
                    unsafe_allow_html=True)
        with st.expander(f"🔔 Alerts · {len(ss.alerts)}" + (f" · {len(fresh)} new" if fresh else ""),
                         expanded=bool(fresh) and ss.get("alerts_shown", False)):
            if not ss.alerts:
                st.markdown('<div class="sl note">No alerts yet. New signals, exits, news risks and market-mood '
                            'changes appear here while the app is open.</div>', unsafe_allow_html=True)
            st.markdown("".join(f'<div class="sl alert"><span class="t">{a["t"]:%H:%M}</span><span>{a["icon"]}</span>'
                                f'<span>{html.escape(a["msg"])}</span></div>' for a in ss.alerts[:25]),
                        unsafe_allow_html=True)
            st.caption("Alerts show while this page is open. Everything is also kept in the Audit log tab for 7 days.")
        ss.alerts_shown = True
        adv, dec = int((S.chg_pct > 0).sum()), int((S.chg_pct < 0).sum())
        kpis([("Session", f'{pd.Timestamp(S.date.iloc[0]):%d %b %Y}', f"{len(S)} stocks scanned"),
              ("Breadth", f'<span class="up">▲{adv}</span> <span class="down">▼{dec}</span>', "up vs down on the day"),
              ("In play", int(S.in_play.sum()), f"rel. vol ≥ {A.min_relvol}×"),
              ("Signals", len(sig), "triggered today"),
              ("Open", int(sig.is_open.sum()), f"exit by {A.exit_time}")])

        section("Signals", f"Risk agent: max {CFG['max_trades']} trades, {CFG['max_open']} open, "
                           f"stop at −{CFG['daily_loss_R']:g}R a day")
        if sig.empty:
            st.markdown('<div class="sl empty">No setups have triggered yet. Watch the trigger levels below — '
                        'a 5-minute close beyond the opening range with rising volume starts a breakout.</div>',
                        unsafe_allow_html=True)
        def render(r):
            a = agt[r.key]
            signal_card(r, A.exit_time, a)
            with st.expander(f"Why this signal · agents · {r.symbol}"):
                st.markdown(f'<div class="sl" style="font-size:14px;line-height:1.55"><b>Narrator</b><br>'
                            f'{html.escape(a["narr"])}</div>', unsafe_allow_html=True)
                sc = a["coach"]
                if sc.get("drivers"):
                    st.markdown('<div class="sl" style="font-size:13.5px;margin-top:10px"><b>Coach — what past trades say</b><br>' +
                                f'Expected <b>{sc["exp_r"]:+.2f}R</b> after costs · win odds {sc["conf"] * 100:.0f}%<br>' +
                                "<br>".join(f'{"▼" if d[0] < 0 else "▲"} {html.escape(d[1])} — averages {d[4]:+.2f}R, '
                                            f'won {d[3] * 100:.0f}% of {d[2]}' for d in sc["drivers"]) +
                                f'<br><span class="note">All signals: {sc["r0"]:+.2f}R per trade, '
                                f'{sc["base"] * 100:.0f}% win rate</span></div>',
                                unsafe_allow_html=True)
                ex = a["exit"]
                alt = " · ".join(f'{p} {v["r"]:+.2f}R' for p, v in a["sims"].items())
                st.markdown(f'<div class="sl" style="font-size:13.5px;margin-top:10px"><b>Exit — {html.escape(a["policy"])}</b><br>'
                            f'{html.escape(ag.EXIT_POLICIES[a["policy"]])}<br>'
                            + (f'{html.escape(ex["note"])}<br>' if ex["note"] else "")
                            + (f'Stop now <b class="num">{inr(ex["stop_now"])}</b>' if a["is_open"] else
                               f'Result {a["exit_r"]:+.2f}R after costs')
                            + (f'<br><span class="note">Same trade under each policy so far: {html.escape(alt)}</span>'
                               if not a["is_open"] else "") + '</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="sl" style="font-size:13.5px;margin-top:10px"><b>Risk — {a["risk_verdict"]}</b><br>'
                            + "<br>".join(html.escape(x) for x in a["risk_why"]) +
                            f'<br>Quantity {int(r.qty_base) if not pd.isna(r.qty_base) else 0:,} × Pulse {size_mult:g} × '
                            f'Risk {a["risk_mult"]:.2f} = <b>{a["qty_final"]:,}</b></div>', unsafe_allow_html=True)
                sv = a.get("sent")
                if sv:
                    hl = "".join(f'<br>· <a href="{html.escape(h["link"])}" target="_blank">{html.escape(h["title"])}</a>'
                                 f' <span class="note">{html.escape(h["publisher"] or "")}</span>' if h["link"] else
                                 f'<br>· {html.escape(h["title"])}' for h in sv["headlines"][:4])
                    st.markdown(f'<div class="sl" style="font-size:13.5px;margin-top:10px"><b>Sentinel — {sv["level"].title()}</b><br>'
                                + "<br>".join(html.escape(x) for x in sv["reasons"]) +
                                (f'<br><i>{html.escape(sv["llm"])}</i>' if sv.get("llm") else "") +
                                (f'<br><span class="note">Recent headlines</span>{hl}' if hl else "") + '</div>',
                                unsafe_allow_html=True)
            with st.expander(f"Chart · {r.symbol}"):
                st.plotly_chart(intraday_chart(bars[r.symbol], r), width="stretch",
                                key=f"c_{r.symbol}_{r.setup}_{ss.tok}", config={"displayModeBar": False})

        for _, r in sig[~sig.blocked].iterrows():
            render(r)
        if sig.blocked.any():
            with st.expander(f"Blocked by agents · {int(sig.blocked.sum())} signal(s) — not to be taken"):
                for _, r in sig[sig.blocked].iterrows():
                    st.caption(f"{r.symbol} {r.setup}: {agt[r.key]['risk_why'][0]}")
                    render(r)

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

# ============================================================================ agent scores + human-in-the-loop
AG_ICON = {"Coach": "🎓", "Pulse": "🧭", "Exit": "🚪", "Risk": "⚖️", "Sentinel": "🛡️", "Narrator": "🗣️"}
AG_ROLE = {"Coach": "Predicts each signal's expected profit and can filter weak ones",
           "Pulse": "Reads market mood and shrinks positions on weak days",
           "Exit": "Chooses and runs the exit policy",
           "Risk": "Daily loss cap, trade and position caps, correlation check",
           "Sentinel": "Flags results dates, F&O ban and red-flag news",
           "Narrator": "Explains each signal in plain English"}


def score_card_html(name, a):
    sc_ = a.get("score")
    tone = a.get("tone", "mid")
    col = {"good": UP, "bad": DOWN}.get(tone, "#9a9a94")
    eff = a.get("effect")
    eff_txt = ("" if eff is None or pd.isna(eff) else
               f'<div class="se"><b class="{"up" if eff > 0 else "down" if eff < 0 else ""}">{eff:+.3f}R</b> '
               f'{html.escape(a.get("effect_label", ""))}'
               + (f' · ≈ {"₹" if a["rs_month"] >= 0 else "−₹"}{abs(a["rs_month"]):,.0f}/month'
                  if pd.notna(a.get("rs_month")) else "") + '</div>')
    bar = (f'<div class="sbar"><div style="width:{sc_}%;background:{col}"></div><span style="left:50%"></span></div>'
           if sc_ is not None else '<div class="sbar dim"></div>')
    return (f'<div class="scard"><div class="sh2"><span>{AG_ICON.get(name, "")} <b>{name}</b></span>'
            f'<span class="sscore" style="color:{col}">{sc_ if sc_ is not None else "—"}<small>/100</small></span></div>'
            f'<div class="role">{html.escape(AG_ROLE.get(name, ""))}</div>{bar}'
            f'<div class="grade" style="color:{col}">{html.escape(a["grade"])}'
            + (f' <small>· {a["n"]} trades, z={a["z"]:+.1f}</small>' if a.get("n") and a.get("z") is not None else "")
            + f'</div>{eff_txt}<div class="det">{html.escape(a.get("detail", ""))}</div>'
            f'<div class="set">Setting: {html.escape(a.get("setting", ""))}</div></div>')


st.markdown("""<style>
.sgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:8px}
@media (max-width:900px){.sgrid{grid-template-columns:1fr}}
.scard{border:1px solid var(--rule);background:#fff;padding:12px 13px;font-family:"Libre Franklin",sans-serif}
.scard .sh2{display:flex;justify-content:space-between;align-items:baseline;font-size:16px}
.sscore{font-size:26px;font-weight:800;font-family:"JetBrains Mono",monospace}.sscore small{font-size:12px;color:var(--meta)}
.scard .role{font-size:12px;color:var(--meta);margin:2px 0 8px}
.sbar{position:relative;height:8px;background:var(--paper2);border-radius:4px;overflow:hidden}
.sbar div{height:8px;border-radius:4px}.sbar span{position:absolute;top:0;width:2px;height:8px;background:#121212;opacity:.35}
.sbar.dim{background:repeating-linear-gradient(45deg,#eee,#eee 4px,#f7f7f5 4px,#f7f7f5 8px)}
.scard .grade{font-weight:700;font-size:13.5px;margin-top:7px}.scard .grade small{font-weight:500;color:var(--meta)}
.scard .se{font-size:12.5px;margin-top:3px}.scard .det{font-size:12.5px;color:#333;margin-top:6px;line-height:1.45}
.scard .set{font-size:11.5px;color:var(--meta);margin-top:6px;border-top:1px dashed var(--rule);padding-top:5px}
.prop{border:1px solid var(--rule);border-left:5px solid var(--blue);background:#fff;padding:11px 13px;margin:6px 0 4px;
 font-family:"Libre Franklin",sans-serif}
.prop .t{font-weight:800;font-size:15px}.prop .w{font-size:13px;color:#333;margin-top:4px;line-height:1.45}
.prop .wi{font-size:12.5px;margin-top:6px;color:var(--meta)}
</style>""", unsafe_allow_html=True)


def sys_line(m):
    return (f'{m["trades"]} trades · <b>{m["exp"]:+.3f}R</b> per trade · total {m["total"]:+.1f}R · '
            f'max drawdown {m["maxdd"]:+.1f}R · win {m["win"] * 100:.0f}%' if m["trades"] else "no trades")


with tab_g:
    ss = st.session_state
    can_edit = (not ADMIN_PIN) or ss.get("admin_ok", False)
    st.markdown('<div class="sl note">Every agent is tested <b>walk-forward</b>: each day\'s signals are judged by agents '
                'that only learned from earlier days, so the scores show how they would really have done. '
                'Agents can only <b>propose</b> changes — you approve, reject or fine-tune them here, and every '
                'decision is versioned and logged.</div>', unsafe_allow_html=True)
    with st.spinner("Scoring the agents on recent sessions…"):
        SC = scorecard_cached(demo, symbols, f"{now_ist():%Y-%m-%d}", ARGS_KEY, json.dumps(CFG, sort_keys=True),
                              RISK_RS, ss.get("tok", 0) // 10, A)
    if SC is None:
        st.markdown('<div class="sl empty">Not enough history yet to score the agents (needs about 12 sessions with '
                    'signals). Try more symbols or a lower relative-volume filter in Settings.</div>',
                    unsafe_allow_html=True)
    else:
        b_, s_ = SC["base"], SC["system"]
        per_day = lambda m: m["total"] / SC["days"]
        kpis([("Test period", f'{SC["days"]} days', f'{SC["period"][0]} → {SC["period"][1]}'),
              ("Baseline", f'{b_["exp"]:+.2f}R', f'per trade · {b_["trades"]} trades, no agents'),
              ("With agents", f'<span class="{"up" if s_["exp"] >= b_["exp"] else "down"}">{s_["exp"]:+.2f}R</span>',
               f'per trade · {s_["trades"]} trades'),
              ("Drawdown", f'{b_["maxdd"]:+.1f} → {s_["maxdd"]:+.1f}R', "worst peak-to-trough"),
              ("Profit / month", f'{"₹" if per_day(s_) >= 0 else "−₹"}{abs(per_day(s_)) * 21 * RISK_RS:,.0f}',
               f'vs ₹{per_day(b_) * 21 * RISK_RS:,.0f} baseline · ₹{RISK_RS:,.0f} risk/trade')])
        cv = SC["curves"]
        fig = go.Figure()
        for c_, colr in [("Baseline (no agents)", "#9a9a94"), ("With agents (active config)", "#121212")]:
            fig.add_trace(go.Scatter(x=cv.ts, y=cv[c_], name=c_, mode="lines", line=dict(color=colr, width=2)))
        fig.update_layout(height=260, margin=dict(l=8, r=8, t=8, b=8), template="plotly_white",
                          yaxis_title="Cumulative R", legend=dict(orientation="h", y=1.12),
                          font=dict(family="Libre Franklin, sans-serif", size=11))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        st.caption("Totals are in R (1R = the amount risked per trade). Drawdown shrinking matters as much as total profit.")

        section("Agent scores", "0–100 · 50 = no measurable effect · based on strength of evidence")
        st.markdown('<div class="sgrid">' + "".join(score_card_html(n_, SC["agents"][n_]) for n_ in
                    ["Coach", "Exit", "Risk", "Pulse", "Sentinel", "Narrator"]) + '</div>', unsafe_allow_html=True)
        with st.expander("Exit policies compared on the same trades"):
            et = SC["exits"].copy()
            st.dataframe(pd.DataFrame({"Policy": et.policy, "How it exits": et.desc, "R per trade": et.exp.round(3),
                                       "vs fixed": et.vs_fixed.round(3), "Evidence z": et.z.round(1),
                                       "Win %": (et.win * 100).round(0)}), hide_index=True, width="stretch")

        # ---------------- admin gate
        section("Review & approve", "human-in-the-loop")
        if ADMIN_PIN and not can_edit:
            c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
            pin = c1.text_input("Reviewer PIN", type="password")
            if c2.button("Unlock", width="stretch"):
                ss.admin_ok = pin == str(ADMIN_PIN)
                st.rerun()
            st.caption("Scores are visible to everyone; approving or changing settings needs the reviewer PIN.")
        elif not ADMIN_PIN:
            st.caption("⚠️ No reviewer PIN set — anyone with the link can approve changes. Add ADMIN_PIN in Streamlit "
                       "Secrets to lock this section.")
        reviewer = st.text_input("Reviewer name", value=ss.get("reviewer", "Reviewer"), key="reviewer",
                                 disabled=not can_edit)

        # ---------------- proposals
        decisions = STORE.kv_get("proposal_decisions", {}) or {}
        fresh_cut = (now_ist() - pd.Timedelta(days=7)).isoformat()
        props = [p for p in ag.proposals(SC, CFG)
                 if not (decisions.get(p["id"], {}).get("status") == "rejected"
                         and decisions[p["id"]].get("ts", "") >= fresh_cut)]
        st.markdown(f'<div class="sl" style="font-weight:700;margin:4px 0">Proposals from agents · {len(props)}</div>',
                    unsafe_allow_html=True)
        if not props:
            st.markdown('<div class="sl empty">No changes proposed — the evidence doesn\'t clearly favour any other '
                        'setting right now. Rejected proposals stay hidden for 7 days.</div>', unsafe_allow_html=True)
        for k_, p in enumerate(props):
            cand = ag.merged({**CFG, **p["change"]})
            wi = scorecard_cached(demo, symbols, f"{now_ist():%Y-%m-%d}", ARGS_KEY, json.dumps(cand, sort_keys=True),
                                  RISK_RS, ss.get("tok", 0) // 10, A)
            wis = wi["system"] if wi else None
            st.markdown(f'<div class="sl prop"><div class="t">{AG_ICON.get(p["agent"], "")} {html.escape(p["title"])}</div>'
                        f'<div class="w">{html.escape(p["why"])}</div>'
                        + (f'<div class="wi">What-if on the same days — now: {sys_line(s_)}<br>'
                           f'after: {sys_line(wis)}</div>' if wis else "") + '</div>', unsafe_allow_html=True)
            c1, c2, c3 = st.columns([3, 1, 1], vertical_alignment="bottom")
            note = c1.text_input("Note (optional)", key=f"pn_{k_}", disabled=not can_edit,
                                 placeholder="why you approve or reject")
            if c2.button("✓ Approve", key=f"pa_{k_}", disabled=not can_edit, type="primary", width="stretch"):
                v = save_cfg(STORE, CFG_STATE, cand, f"Approved: {p['title']}" + (f" — {note}" if note else ""),
                             reviewer, p["change"])
                decisions[p["id"]] = dict(status="approved", ts=now_ist().isoformat(), by=reviewer, note=note, version=v)
                STORE.kv_set("proposal_decisions", decisions)
                st.toast(f"Approved — settings v{v} are live", icon="✅")
                st.rerun()
            if c3.button("✕ Reject", key=f"pr_{k_}", disabled=not can_edit, width="stretch"):
                decisions[p["id"]] = dict(status="rejected", ts=now_ist().isoformat(), by=reviewer, note=note)
                STORE.kv_set("proposal_decisions", decisions)
                STORE.log("Reviewer", "reject", f"{reviewer} rejected: {p['title']}" + (f" — {note}" if note else ""),
                          dkey=f"rej|{p['id']}|{now_ist():%Y%m%d%H%M%S}")
                st.toast("Rejected — hidden for 7 days", icon="✕")
                st.rerun()

        # ---------------- manual fine-tuning with what-if
        with st.expander("🛠 Fine-tune settings yourself (simulate before applying)", expanded=False):
            with st.form("tune"):
                st.markdown("**🎓 Coach**")
                t1, t2, t3 = st.columns(3)
                f_cf = t1.toggle("Filter weak signals", CFG["coach_filter"])
                f_th = t2.slider("Skip if expected R below", -0.5, 0.5, float(CFG["coach_threshold"]), 0.05)
                f_hl = t3.slider("Memory half-life (days)", 3, 40, int(CFG["half_life"]),
                                 help="Lower = adapts faster to recent sessions, but noisier.")
                st.markdown("**🧭 Pulse**")
                f_ps = st.toggle("Mood-based sizing", CFG["pulse_sizing"])
                mc = st.columns(3)
                f_m = {m: mc[i % 3].number_input(f"{m} ×", 0.0, 1.5, float(CFG["mults"][m]), 0.05, key=f"m_{m}")
                       for i, m in enumerate(ag.DEFAULT_MULTS)}
                st.markdown("**🚪 Exit**")
                f_ex = st.selectbox("Exit policy", list(ag.EXIT_POLICIES), list(ag.EXIT_POLICIES).index(CFG["exit_policy"]),
                                    format_func=lambda p: ag.EXIT_POLICIES[p])
                st.markdown("**⚖️ Risk**")
                r1, r2, r3 = st.columns(3)
                f_re = r1.toggle("Risk rules on", CFG["risk_enabled"])
                f_dl = r2.slider("Daily loss cap (R)", 0.5, 6.0, float(CFG["daily_loss_R"]), 0.5)
                f_mt = r3.slider("Max trades / day", 1, 10, int(CFG["max_trades"]))
                r4, r5, r6 = st.columns(3)
                f_mo = r4.slider("Max open at once", 1, 8, int(CFG["max_open"]))
                f_cl = r5.slider("Correlation limit", 0.4, 0.95, float(CFG["corr_limit"]), 0.05)
                f_ps2 = r6.multiselect("Paused setups", ["ORB", "VWAP"], CFG["paused_setups"])
                r7, r8, r9 = st.columns(3)
                f_es = r7.toggle("Edge-based sizing", CFG["edge_sizing"], help="Size up/down (0.5–1.5×) by Coach's expected R")
                f_ek = r8.slider("Edge sizing strength", 0.5, 3.0, float(CFG["edge_k"]), 0.25)
                f_sb = r9.toggle("Block Sentinel HIGH", CFG["sentinel_block"])
                sim = st.form_submit_button("Simulate on recent sessions")
            if sim:
                ss.tune_cand = ag.merged(dict(coach_filter=f_cf, coach_threshold=f_th, half_life=f_hl, pulse_sizing=f_ps,
                                              mults=f_m, exit_policy=f_ex, risk_enabled=f_re, daily_loss_R=f_dl,
                                              max_trades=f_mt, max_open=f_mo, corr_limit=f_cl, paused_setups=f_ps2,
                                              edge_sizing=f_es, edge_k=f_ek, sentinel_block=f_sb))
            cand = ss.get("tune_cand")
            if cand:
                diff = cfg_diff(CFG, cand)
                if not diff:
                    st.info("These are the current settings.")
                else:
                    wi = scorecard_cached(demo, symbols, f"{now_ist():%Y-%m-%d}", ARGS_KEY,
                                          json.dumps(cand, sort_keys=True), RISK_RS, ss.get("tok", 0) // 10, A)
                    st.markdown(f'<div class="sl prop"><div class="t">What-if: {html.escape(", ".join(diff))}</div>'
                                f'<div class="wi">Current v{CFG_STATE["version"]}: {sys_line(s_)}<br>'
                                f'Your settings: {sys_line(wi["system"]) if wi else "—"}</div></div>',
                                unsafe_allow_html=True)
                    if wi:
                        st.caption("Agent scores with your settings: " + " · ".join(
                            f'{n_} {wi["agents"][n_]["score"] if wi["agents"][n_]["score"] is not None else "—"}'
                            for n_ in ["Coach", "Exit", "Risk", "Pulse"]))
                    note = st.text_input("Reason for this change (required)", key="tune_note", disabled=not can_edit)
                    if st.button("Apply as new version", type="primary", disabled=not (can_edit and note.strip())):
                        v = save_cfg(STORE, CFG_STATE, cand, f"Manual tune — {note}", reviewer, diff)
                        ss.tune_cand = None
                        st.toast(f"Settings v{v} are live", icon="✅")
                        st.rerun()

        # ---------------- history & rollback
        with st.expander(f"🕘 Settings history · v{CFG_STATE['version']} active"):
            hist = list(reversed(CFG_STATE.get("history") or []))
            st.dataframe(pd.DataFrame({
                "Version": [f"v{h['version']}" + (" (active)" if h["version"] == CFG_STATE["version"] else "") for h in hist],
                "When": [pd.Timestamp(h["ts"]).tz_convert(ce.IST).strftime("%d %b %H:%M") for h in hist],
                "By": [h.get("by", "") for h in hist], "What changed": [h.get("note", "") for h in hist]}),
                hide_index=True, width="stretch")
            older = [h for h in hist if h["version"] != CFG_STATE["version"] and h.get("params")]
            if older:
                c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
                pick = c1.selectbox("Roll back to", [f"v{h['version']} — {h.get('note', '')[:60]}" for h in older])
                if c2.button("Roll back", disabled=not can_edit, width="stretch"):
                    h = older[[f"v{x['version']} — {x.get('note', '')[:60]}" for x in older].index(pick)]
                    v = save_cfg(STORE, CFG_STATE, h["params"], f"Rolled back to v{h['version']}", reviewer,
                                 cfg_diff(CFG, ag.merged(h["params"])))
                    st.toast(f"Rolled back — settings v{v} are live", icon="↩️")
                    st.rerun()
        st.caption("Scores update daily from the last ~60 sessions (Yahoo 5-minute data limit), so treat anything under "
                   "a few hundred trades as early evidence. Sentinel can only be scored on live signals as they build up.")

# ============================================================================ audit log
with tab_a:
    store = STORE
    if demo:
        st.info("Demo mode: this log holds demo activity only and is kept separate from the live log.")
    elif not store.remote:
        st.warning("The log is currently temporary: it is wiped whenever the hosted app restarts or redeploys. "
                   "Connect Supabase to keep a full week.")
        with st.expander("How to connect Supabase (5 minutes)"):
            st.markdown("1. In your Supabase project, open **SQL editor**, paste the SQL below and run it.\n"
                        "2. In Streamlit (Manage app → Settings → Secrets) add:\n"
                        "```\nSUPABASE_URL = \"https://<your-project>.supabase.co\"\nSUPABASE_KEY = \"<service-role key>\"\n"
                        "ANTHROPIC_API_KEY = \"<optional — richer agents>\"\n```\n"
                        "3. Save. The app restarts and the Audit log becomes permanent (events kept 7 days, "
                        "trade journal 90 days).")
            st.code(SUPABASE_SQL, language="sql")
    if store.last_error:
        st.caption(f"Storage note: {store.last_error}")

    ev = store.events()
    tr = store.trades()
    coach_all = ag.Coach(tr)
    live = tr[tr.source == "live"] if not tr.empty else tr
    kpis([("Events · 7 days", len(ev), f"{ev.agent.nunique() if len(ev) else 0} agents active"),
          ("Alerts", int((ev.agent == "Alerts").sum()) if len(ev) else 0, "raised in 7 days"),
          ("Live signals", len(live), "journaled"),
          ("Post-mortems", int((ev.kind == "post-mortem").sum()) if len(ev) else 0, "losses diagnosed"),
          ("Coach studied", coach_all.n, f"trades · {coach_all.p0 * 100:.0f}% win rate")])

    section("Agent activity", "last 7 days · newest first")
    if ev.empty:
        st.markdown('<div class="sl empty">No activity yet. Open the Intraday tab — agents log what they check, '
                    'flag and learn here.</div>', unsafe_allow_html=True)
    else:
        f1, f2, f3 = st.columns([2, 1.2, 1.6])
        agents_sel = f1.multiselect("Agents", sorted(ev.agent.unique()), default=sorted(ev.agent.unique()))
        days_avail = sorted({t.date() for t in ev.ts}, reverse=True)
        day_sel = f2.selectbox("Day", ["All 7 days"] + [f"{d:%a %d %b}" for d in days_avail])
        q = f3.text_input("Search", placeholder="symbol or word")
        view = ev[ev.agent.isin(agents_sel)]
        if day_sel != "All 7 days":
            view = view[view.ts.map(lambda t: f"{t:%a %d %b}") == day_sel]
        if q:
            ql = q.lower()
            view = view[view.message.str.lower().str.contains(ql, regex=False) |
                        view.symbol.fillna("").str.lower().str.contains(ql, regex=False)]
        icons = {"Pulse": "🧭", "Sentinel": "🛡️", "Narrator": "🗣️", "Coach": "🎓", "Alerts": "🔔", "Exit": "🚪", "Risk": "⚖️", "Reviewer": "👤"}
        out = pd.DataFrame({"Time": view.ts.map(lambda t: f"{t:%d %b %H:%M}"), "Agent": view.agent,
                            "Type": view.kind, "Symbol": view.symbol.fillna(""), "What happened": view.message})
        show_n = st.select_slider("Show", [25, 50, 100, 250, 500], 50, key="audit_n") if len(out) > 25 else 25
        rows_html = "".join(
            f'<div class="alert"><span class="t">{html.escape(r.Time)}</span><span>{icons.get(r.Agent, "•")}</span>'
            f'<span><b>{html.escape(r.Agent)}</b> <span class="note">{html.escape(r.Type)}</span>'
            f'{f" · <b>{html.escape(r.Symbol)}</b>" if r.Symbol else ""}<br>{html.escape(r._5)}</span></div>'
            for r in out.head(show_n).itertuples())
        st.markdown(f'<div class="sl">{rows_html}</div>', unsafe_allow_html=True)
        st.caption(f"Showing {min(show_n, len(out))} of {len(out)} events.")
        st.download_button("Download log (CSV)", out.to_csv(index=False), "stocklab_audit_log.csv", "text/csv")

    section("Coach · what it has learned", f"from {coach_all.n} trades (recent history + live signals)")
    lessons = coach_all.lessons()
    if not lessons:
        st.markdown('<div class="sl empty">Not enough trades yet for clear lessons. The Coach needs about 15 trades '
                    'in a condition before it trusts a pattern.</div>', unsafe_allow_html=True)
    else:
        st.markdown("".join(
            f'<div class="sl lesson {l["direction"]}"><b>{ {"avoid": "▼ Avoid", "weaker": "▼ Weaker", "favour": "▲ Favour"}[l["direction"]] }:</b> '
            f'{html.escape(l["text"])} <small>— won {l["win_rate"] * 100:.0f}% of {l["n"]} trades, '
            f'avg {l["avg_r"]:+.2f}R (all signals: {coach_all.r0:+.2f}R, {coach_all.p0 * 100:.0f}% won)</small></div>'
            for l in lessons),
            unsafe_allow_html=True)
        st.caption("How it uses this: each new signal gets a win-odds score from these patterns (shown on the card), "
                   "so conditions that keep failing lower the score of future signals.")
    tbl = coach_all.table
    if not tbl.empty:
        t2 = tbl[tbl.n >= 8].copy()
        t2["label"] = [ag.FEATURE_TEXT.get(f, {}).get(b, b) for f, b in zip(t2.feature, t2.bucket)]
        t2 = t2.sort_values("win_rate")
        fig = go.Figure(go.Bar(x=t2.win_rate * 100, y=t2.label, orientation="h",
                               marker_color=[UP if e > 0 else DOWN for e in t2.edge],
                               text=[f"{w * 100:.0f}% · n={n}" for w, n in zip(t2.win_rate, t2.n)],
                               textposition="outside", cliponaxis=False))
        fig.add_vline(x=coach_all.p0 * 100, line=dict(color="#121212", dash="dash"),
                      annotation_text=f"all signals {coach_all.p0 * 100:.0f}%", annotation_position="top")
        fig.update_layout(height=max(260, 26 * len(t2) + 60), margin=dict(l=8, r=60, t=28, b=8),
                          template="plotly_white", xaxis_title="Win rate %", xaxis_range=[0, 100],
                          font=dict(family="Libre Franklin, sans-serif", size=11))
        with st.expander("Win rate by condition"):
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    section("Why trades went wrong", "losing trades in the last 7 days, diagnosed by the Coach")
    if tr.empty:
        st.markdown('<div class="sl empty">No trades recorded yet.</div>', unsafe_allow_html=True)
    else:
        recent = tr.copy()
        recent["day"] = pd.to_datetime(recent.day)
        recent = recent[(recent.day >= pd.Timestamp.now() - pd.Timedelta(days=7)) & (recent.r < 0)]
        if recent.empty:
            st.markdown('<div class="sl empty">No losing trades in the last 7 days.</div>', unsafe_allow_html=True)
        else:
            def _reason(row):
                if isinstance(row.reason, str) and row.reason:
                    return row.reason
                pm = coach_all.postmortem(row.features if isinstance(row.features, dict) else json.loads(row.features or "{}"), row.r)
                return pm["primary"] if pm else "—"
            recent["why"] = recent.apply(_reason, axis=1)
            counts = recent.why.map(lambda x: x.split(" — ")[0]).value_counts()
            mx = counts.max()
            st.markdown('<div class="sl">' + "".join(
                f'<div style="margin:6px 0"><div style="font-size:13px">{html.escape(k)} '
                f'<b class="num">{v}</b></div><div style="height:8px;background:var(--paper2);border-radius:4px">'
                f'<div style="height:8px;width:{v / mx * 100:.0f}%;background:var(--down);border-radius:4px"></div></div></div>'
                for k, v in counts.items()) + '</div>', unsafe_allow_html=True)
            lt = pd.DataFrame({"Day": recent.day.dt.strftime("%d %b"), "Symbol": recent.symbol, "Setup": recent.setup,
                               "Side": recent.side.map({1: "▲ Long", -1: "▼ Short"}), "Result": recent.r.map(lambda v: f"{v:+.2f}R"),
                               "Source": recent.source, "Main reason": recent.why})
            st.dataframe(lt, hide_index=True, width="stretch")
    if st.button("Re-train Coach now"):
        train_coach.clear()
        coach_trades.clear()
        scorecard_cached.clear()
        with st.spinner("Coach is studying recent sessions…"):
            history_cached.clear()
            n = train_coach(demo, symbols, f"{pd.Timestamp.now(tz=ce.IST):%Y-%m-%d}", ARGS_KEY, A)
        st.success(f"Coach studied {n} signals.")
    st.caption("Agents: 🧭 Pulse market mood · 🛡️ Sentinel news & events · 🗣️ Narrator explanations · "
               "🎓 Coach learning & post-mortems · 🚪 Exit · ⚖️ Risk · 🔔 Alerts · 👤 Reviewer decisions. "
               "Events are kept 7 days; the trade journal 90 days.")

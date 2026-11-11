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
    for name in ("cheetah_engine", "store", "models", "research", "learning", "agents"):          # dependency order
        mod = sys.modules.get(name)
        path = os.path.join(here, name + ".py")
        if mod is None or not os.path.exists(path):
            continue
        mtime = os.path.getmtime(path)
        if getattr(mod, "__stocklab_mtime__", None) != mtime:
            mod = importlib.reload(mod)
            mod.__stocklab_mtime__ = mtime


_fresh_local_modules()
import agents as ag
import research as rs  # noqa: E402
import cheetah_engine as ce  # noqa: E402
import learning as ln  # noqa: E402
from store import SUPABASE_SQL, MIGRATION_SQL, Store, StoreError, TABLE_ROLE, TRADE_DAYS, MEMORY_DAYS, EVENT_DAYS  # noqa: E402

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
    if st_.remote:
        st_.health(force=True)          # finds a wrong key type or missing tables before the agents rely on them
    st_.purge()
    return st_


def _store_schema():
    import hashlib, inspect, store as _sm
    return hashlib.md5(inspect.getsource(_sm).encode()).hexdigest()[:10]


def get_store(demo):
    s_ = _make_store(demo, _store_schema())
    if not all(hasattr(s_, m) for m in ("kv_get", "kv_set", "trades", "events", "log", "flush",
                                       "memory_list", "health")):
        _make_store.clear()
        s_ = _make_store(demo, _store_schema())
    return s_


@st.cache_data(ttl=3600, show_spinner=False, max_entries=6)
def fetch_index_cached(demo, token):
    return ag.fetch_index(demo, ce.demo_intraday(days=15) if demo else None)


@st.cache_data(ttl=1800, show_spinner=False, max_entries=40)
def sentinel_batch(syms, demo, day, use_llm):
    """Sentinel verdicts for the stocks that matter right now (signals + in play), refreshed every 30 minutes."""
    return ag.sentinel_many(list(syms), demo, API_KEY if use_llm else None, pd.Timestamp(day).date())


def sentinel_rows(verdicts, day, scanned=True):
    """Archive rows: today's verdict per stock, plus past results days learnt from the exchange calendar."""
    rows, back = [], []
    for sym, v in verdicts.items():
        if v["level"] != "UNCHECKED":
            rows.append(dict(day=str(day), symbol=sym, level=v["level"],
                             data=dict(types=v["types"], tone=v["tone"], reasons=v["reasons"][:3], sources=v["sources"],
                                       scanned=scanned, by_claude=bool(v.get("llm")))))
        for d in v.get("past_results", []):
            back.append(dict(day=d, symbol=sym, level="HIGH", data=dict(types=["results_day"], scanned=False,
                                                                        reasons=["Results day (back-filled from the exchange calendar)"])))
    return rows, back


@st.cache_resource
def _memo():
    """What the learning memory added to today's history (filled when the daily replays run)."""
    return {}


MEMO = _memo()


@st.cache_resource
def scan_jobs():
    return {}


def ensure_daily_scan(demo, symbols, store):
    """Once per trading day: scan the whole watchlist in the background (so nobody waits for it) and archive
    what the Sentinel knew before or early in the session. The archive is what lets the Sentinel be scored."""
    now = pd.Timestamp.now(tz=ce.IST)
    day_s = f"{now:%Y-%m-%d}"
    jobs = scan_jobs()
    k = (demo, day_s, symbols)
    if k in jobs:
        return jobs[k]
    job = dict(status="running", results={}, started=time.time(), day=day_s)
    jobs[k] = job
    for old in [x for x in jobs if x[1] != day_s]:
        jobs.pop(old, None)
    if demo:                                             # demo: quick and local, build two weeks of history
        dd = ce.demo_intraday(days=45)                    # demo prices use their own symbols
        dsyms = list(dd)
        days = sorted({str(d) for df in dd.values() for d in set(df.index.date)})[-15:]
        rows = []
        for d in days:
            rws, _ = sentinel_rows(ag.sentinel_many(dsyms, True, None, d), d)
            rows += rws
        store.sentinel_upsert(rows)
        job.update(status="done", results=ag.sentinel_many(dsyms, True, None, days[-1]))
        return job
    if now.weekday() >= 5:
        job.update(status="skipped", note="Weekend — no pre-open scan")
        return job
    key = API_KEY

    def run():
        try:
            res = ag.sentinel_many(list(symbols), False, key, now.date())
            rows, back = sentinel_rows(res, day_s)
            store.sentinel_upsert(rows)
            store.sentinel_upsert(back)
            lv = [v["level"] for v in res.values()]
            src = {}
            for v in res.values():
                for name, stt in v["sources"].items():
                    src.setdefault(name, [0, 0])[0 if stt == "ok" else 1] += 1
            store.log("Sentinel", "daily-scan", f"Watchlist scan of {len(res)} stocks: {lv.count('HIGH')} high-risk, "
                      f"{lv.count('WATCH')} to watch, {lv.count('CLEAR')} clear, {lv.count('UNCHECKED')} not checked. Sources — "
                      + "; ".join(f"{n} {ok_}/{ok_ + bad_}" for n, (ok_, bad_) in src.items()),
                      detail=dict(sources=src), dkey=f"sentinel-daily|{day_s}")
            store.flush()
            job.update(status="done", results=res, sources=src, finished=time.time())
        except Exception as e:                           # never let the scan take the app down
            job.update(status="failed", note=str(e)[:200])
    import threading
    threading.Thread(target=run, daemon=True).start()
    return job


@st.cache_data(ttl=3600, show_spinner=False, max_entries=300)
def narrate_cached(key, status, row_json, mood_json, sent_json, score_json, use_llm):
    return ag.narrate(pd.Series(json.loads(row_json)), json.loads(mood_json), json.loads(sent_json),
                      json.loads(score_json), API_KEY if use_llm else None)


@st.cache_data(ttl=86400, show_spinner=False, max_entries=3)
def intraday_history(demo, symbols, day):
    """~59 sessions of 5-minute bars plus the index, downloaded once a day and shared by every study."""
    data = ce.demo_intraday(days=45) if demo else ce.yahoo_intraday(list(symbols), 59)
    if not data:
        return {}, {}
    return data, (ag.fetch_index(demo, data) if demo else fetch_index_cached(False, f"train-{day}"))


@st.cache_data(ttl=86400, show_spinner=False, max_entries=6)
def history_cached(demo, symbols, day, args_key, _a):
    """Point-in-time replay of recent sessions: every signal, its features at signal time, every exit policy."""
    data, idx = intraday_history(demo, symbols, day)
    if not data:
        return [], {}, []
    rows, corr = ag.replay(data, idx, _a)
    store = get_store(demo)
    eng = ln.eng_id(args_key)
    ln.tag_engine(rows, eng)
    rows, info = ln.remember_replay(store, "sig", eng, rows, today=day)
    keep = set(data)                                  # only stocks still on the watchlist
    rows = [r for r in rows if r["symbol"] in keep]
    corr = ln.remember_corr(store, corr, today=day)
    stats = ln.remember_day_stats(store, ag.day_stats(data).to_dict("records"), today=day)
    stats = [s_ for s_ in stats if s_["symbol"] in keep]
    nb = ln.backfill_news(rows, store.sentinel_list(days=MEMORY_DAYS))
    MEMO.update(sig=dict(info, news_backfilled=nb, eng=eng, day=day))
    if info["memory_days"] or nb:
        store.log("Coach", "memory", f"History: {info['fresh_days']} sessions from Yahoo + {info['memory_days']} "
                  f"remembered from the database ({info['n']} signals since {info['first_day']}); "
                  f"news filled in from the Sentinel archive on {nb}", detail=info, dkey=f"memory|{day}|{eng}")
    return rows, corr, stats


@st.cache_data(ttl=86400, show_spinner=False, max_entries=6)
def train_coach(demo, symbols, day, args_key, _a):
    """Once a day: replay recent sessions so the Coach learns from every signal, not just the ones you saw."""
    rows, _, _ = history_cached(demo, symbols, day, args_key, _a)
    if not rows:
        return 0
    store = get_store(demo)
    try:
        old = store.trades(strict=True)
    except StoreError:            # never overwrite live journal rows we could not read; not cached, so it retries
        store.log("Coach", "training", "Skipped training: the trade journal could not be read "
                  f"({store.last_error})", dkey=f"coach-train-skip|{day}|{args_key}")
        raise
    cut = str((pd.Timestamp(day) - pd.Timedelta(days=TRADE_DAYS - 1)).date())
    rows = [r for r in rows if str(r["day"]) >= cut]
    live = {k: (f if isinstance(f, dict) else json.loads(f or "{}"), rs)
            for k, f, rs, src in zip(old.key, old.features, old.reason, old.source) if src == "live"} if not old.empty else {}
    out = []
    for r in rows:
        if r["key"] in live:          # keep what was only known live (news flag, post-mortem) and stay 'live'
            lf, lreason = live[r["key"]]
            feats = dict(r["features"])
            if lf.get("news") not in (None, "unknown"):
                feats["news"] = lf["news"]
            r = dict(r, source="live", features=feats, reason=lreason or r.get("reason"))
        out.append(r)
    store.upsert_trades(out)
    store.log("Coach", "training", f"Studied {len(rows)} signals from the last {len({r['day'] for r in rows})} sessions",
              detail={"trades": len(rows)}, dkey=f"coach-train|{day}|{args_key}")
    return len(rows)


@st.cache_data(ttl=300, show_spinner=False, max_entries=4)
def coach_trades(demo, token):
    return get_store(demo).trades()


@st.cache_data(ttl=86400, show_spinner=False, max_entries=3)
def study_cached(demo, symbols, day, study_key, _a):
    """A wider set of signals from the same sessions (both directions, lower volume bar) for the models to learn from."""
    data, idx = intraday_history(demo, symbols, day)
    if not data:
        return []
    rows = ag.replay(data, idx, _a)[0]
    store = get_store(demo)
    eng = ln.eng_id("study|" + study_key)
    ln.tag_engine(rows, eng)
    rows, info = ln.remember_replay(store, "study", eng, rows, today=day)
    keep = set(data)
    rows = [r for r in rows if r["symbol"] in keep]
    nb = ln.backfill_news(rows, store.sentinel_list(days=MEMORY_DAYS))
    MEMO.update(study=dict(info, news_backfilled=nb, eng=eng, day=day))
    return rows


def _study_key(sa):
    return f"{sa.min_relvol}|{sa.rr}|{sa.cost_pct}|{json.dumps(sa.strategy, sort_keys=True)}"


def _study(demo, symbols, day, cfg):
    sa = ag.study_args(cfg)
    return study_cached(demo, symbols, day, _study_key(sa), sa)


@st.cache_data(ttl=86400, show_spinner=False, max_entries=6)
def scout_cached(demo, symbols, day, cost_pct, objective, current_json):
    """Scout: test thousands of strategies on older sessions, confirm on the newest ones."""
    data, idx = intraday_history(demo, symbols, day)
    if not data:
        return None
    res = rs.run(data, idx, cost_pct, objective, current=json.loads(current_json))
    if res:
        b = res["confirmed"].iloc[0] if len(res["confirmed"]) else None
        get_store(demo).log("Scout", "research",
                            f"Tested {res['tested']:,} strategies on {res['days']} sessions; "
                            f"{int(res['shortlist'].held.sum())} of {len(res['shortlist'])} shortlisted held up on the newest "
                            f"{res['n_conf_days']} sessions." + (f" Best: {b.label} — won {b.c_win * 100:.0f}%, "
                                                                f"{b.c_exp:+.2f}R per trade on unseen days." if b is not None else
                                                                " None held up."),
                            dkey=f"scout|{day}|{objective}|{cost_pct}|{current_json}")
    return res


def scout_for(cfg, demo, objective=None):
    sy = tuple(sorted({str(x).strip().upper() for x in cfg["watchlist"] if str(x).strip()}))
    return scout_cached(demo, sy, f"{now_ist():%Y-%m-%d}", float(cfg["cost_pct"]), objective or cfg["scout_objective"],
                        json.dumps(cfg.get("strategy"), sort_keys=True))


@st.cache_data(ttl=86400, show_spinner=False, max_entries=12)
def model_preds_cached(demo, symbols, day, args_key, plan, cfg_json, _a):
    """Walk-forward win odds from logistic regression, boosted trees, the neural network and their ensemble."""
    rows, _, _ = history_cached(demo, symbols, day, args_key, _a)
    cfg = json.loads(cfg_json)
    return ag.model_predictions(rows, cfg, _study(demo, symbols, day, cfg))


@st.cache_resource(ttl=86400, show_spinner=False, max_entries=6)
def brain_cached(demo, symbols, day, study_key, plan, kind, _cfg):
    """The approved model, refitted once a day on every finished practice signal so far."""
    rows = _study(demo, symbols, day, _cfg)
    b = ag.fit_brain(pd.DataFrame(rows), plan, kind) if rows else None
    if b is not None and b.ok:
        get_store(demo).log("Coach", "training", f"{ag.BRAINS[kind]} refitted on {b.n} practice signals "
                            f"(base win rate {b.p0 * 100:.0f}%)", dkey=f"brain|{day}|{kind}|{plan}|{study_key}")
    return b


@st.cache_data(ttl=3600, show_spinner=False, max_entries=24)
def scorecard_cached(demo, symbols, day, args_key, cfg_json, risk_rs, live_token, _a, lab=False):
    rows, corr, stats = history_cached(demo, symbols, day, args_key, _a)
    cfg = json.loads(cfg_json)
    mcfg = json.dumps({k: cfg[k] for k in ag.ENGINE_KEYS + ["exit_policy", "paper_window_min"] if k in cfg}, sort_keys=True)
    mp = (model_preds_cached(demo, symbols, day, args_key, ag.plan_key(cfg), mcfg, _a)
          if rows and (lab or cfg.get("coach_model", "bucket") != "bucket") else None)
    store = get_store(demo)
    tr = store.trades()
    live = tr[tr.source == "live"] if not tr.empty else tr
    ev = ag.sentinel_evidence(store.sentinel_list(), pd.DataFrame(stats))
    return ag.scorecard(rows, cfg, live, corr, risk_rs, sentinel_ev=ev, mp=mp)


def now_ist():
    return pd.Timestamp.now(tz=ce.IST)


@st.cache_data(ttl=3600, show_spinner=False, max_entries=4)
def distill_audit(demo, hour):
    """Hourly: turn the 7-day audit log into daily digests kept 180 days, then read the trends back.
    The oldest day in the log is skipped — retention has already trimmed it, and a partial digest would
    overwrite the complete one written earlier."""
    store = get_store(demo)
    first_full = str((now_ist() - pd.Timedelta(days=EVENT_DAYS - 1)).date())
    rows = [r for r in ln.distill_events(store.events()) if r["day"] >= first_full]
    if rows:
        store.memory_upsert("audit", "all", rows)
    return ln.audit_trends(store.memory_list("audit", "all"), today=now_ist().tz_localize(None))


@st.cache_data(ttl=3600, show_spinner=False, max_entries=4)
def store_memory_stats(demo, token):
    return get_store(demo).memory_stats()


def score_for(cfg, demo, token, lab=False):
    """Scorecard for any settings — the engine, paper account and watchlist come from the settings themselves."""
    a_ = ag.engine_args(cfg)
    sy = tuple(sorted({str(x).strip().upper() for x in cfg["watchlist"] if str(x).strip()}))
    return scorecard_cached(demo, sy, f"{now_ist():%Y-%m-%d}", ag.engine_key(cfg), json.dumps(cfg, sort_keys=True),
                            a_.capital * a_.risk_pct / 100, token, a_, lab=lab)


def load_cfg(store):
    """Shared settings. If the database cannot be read, run on defaults for this visit WITHOUT writing them —
    writing defaults during an outage would overwrite the real, reviewer-approved settings and their history."""
    try:
        state = store.kv_get("agent_config", strict=True)
    except StoreError:
        p = ag.merged({})
        return dict(version=0, active=p, history=[], offline=True)
    if not state or "active" not in state:
        p = ag.merged({})
        state = dict(version=1, active=p, history=[dict(version=1, ts=now_ist().isoformat(), by="system",
                                                        note="Initial default settings", change={}, params=p)])
        store.kv_set("agent_config", state)
    state["active"] = ag.merged(state["active"])
    return state


def save_cfg(store, state, params, note, by, change):
    if state.get("offline"):
        st.error("Settings were not saved: the database could not be read when this page loaded, so saving now "
                 "could overwrite the approved settings. Reload the page once storage is back.")
        st.stop()
    v = int(state["version"]) + 1
    params = ag.merged(params)
    hist = (state.get("history") or []) + [dict(version=v, ts=now_ist().isoformat(), by=by, note=note,
                                                change=change, params=params)]
    if not store.kv_set("agent_config", dict(version=v, active=params, history=hist[-60:])):
        st.error(f"Settings were not saved: {store.last_error}")
        st.stop()
    store.log("Reviewer", "config", f"Settings v{v} by {by}: {note}", detail=dict(change=change, version=v),
              dkey=f"cfg|{v}|{now_ist():%Y%m%d%H%M%S}")
    scorecard_cached.clear()
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
         + (f'<b>{"BUY" if r.side == 1 else "SELL"}</b> at {r.signal_time:%H:%M} · '
            + (f'close by <b>{agt["deadline"]:%H:%M}</b> unless stop/target hits first' if agt and agt.get("deadline") is not None
               else 'hold to stop, target or 15:15') if agt else f'signal {r.signal_time:%H:%M}')
         + '</div>'
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


BRAIN_SHORT = {"logit": "logistic", "gbm": "boosted trees", "dnn": "neural net", "ensemble": "ensemble"}


def agent_strip(a):
    if not a:
        return ""
    chips = []
    al = a["align"]
    chips.append(f'<span class="ag {"good" if al == "with" else "bad" if al == "against" else ""}">'
                 f'<i>Pulse · {html.escape(a["m_at"]["regime"])} ×{a.get("size_mult", 1):g}</i>'
                 f'{"▲ with market" if al == "with" else "▼ against market" if al == "against" else "• market neutral"}</span>')
    lv = a["sentinel"]
    chips.append(f'<span class="ag {"bad" if lv == "HIGH" else "warn" if lv == "WATCH" else "good" if lv == "CLEAR" else ""}">'
                 f'<i>Sentinel</i>{"⚠ " if lv == "HIGH" else ""}'
                 f'{lv.title() if lv in ("HIGH", "WATCH", "CLEAR") else "not checked"}</span>')
    sc = a["coach"]
    if sc.get("conf") is not None:
        v = sc["verdict"]
        chips.append(f'<span class="ag {"good" if v == "Favourable" else "bad" if v == "Caution" else ""}"><i>Coach'
                     f'{" · " + BRAIN_SHORT.get(sc.get("model"), "") if sc.get("model") not in (None, "bucket") else ""}</i>'
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
    p_ = a.get("paper")
    if p_:
        if p_["status"] == "SKIPPED":
            chips.append(f'<span class="ag"><i>📝 Paper</i>skipped · would be {p_["r"]:+.2f}R</span>')
        else:
            chips.append(f'<span class="ag {"good" if p_["r"] > 0.05 else "bad" if p_["r"] < -0.05 else ""}"><i>📝 Paper</i>'
                         f'{p_["status"].lower()} {p_["r"]:+.2f}R · {"₹" if p_["pnl"] >= 0 else "−₹"}{abs(p_["pnl"]):,.0f}</span>')
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
    eng_slot = st.container()
    st.caption("AGENTS")
    cfg_slot = st.empty()
    st.caption("Claude API: " + ("connected — richer news reading and explanations" if API_KEY
                                 else "not set — agents run rule-based"))

STORE = get_store(demo)
CFG_STATE = load_cfg(STORE)
if CFG_STATE.get("offline"):
    st.warning("⚠️ The database did not answer, so this visit runs on default settings and nothing will be saved. "
               f"Details in 🗂 Audit log → Storage. ({STORE.last_error})")
CFG = CFG_STATE["active"]
# The signal engine, paper account and watchlist are shared settings (versioned, reviewer-approved), so the
# trade log, the Coach's training and every visitor's screen describe the same system.
A = ag.engine_args(CFG)
symbols = tuple(sorted({str(x).strip().upper() for x in CFG["watchlist"] if str(x).strip()}))
RISK_RS = A.capital * A.risk_pct / 100
ARGS_KEY = ag.engine_key(CFG)
ENG_ID = ln.eng_id(ARGS_KEY)
PLAN = ag.plan_key(CFG)
ENGINE_SETUPS = [rs.FAMILIES[CFG["strategy"]["fam"]][0]] if CFG.get("strategy") else ["ORB", "VWAP"]
with eng_slot:
    st.caption("SHARED SETTINGS · v%s" % CFG_STATE["version"])
    st.markdown(
        f'<div class="sl" style="font-size:12.5px;line-height:1.6;color:#333">'
        f'Paper capital <b>₹{A.capital:,.0f}</b> · risk <b>{A.risk_pct:g}%</b> per trade<br>'
        + (f'Signal engine <b>🔭 {html.escape(rs.label(CFG["strategy"]))}</b> (Scout strategy)<br>'
           f'Costs <b>{A.cost_pct:g}%</b><br>' if CFG.get("strategy") else
           f'Setups <b>{ {"both": "ORB + VWAP", "orb": "ORB", "vwap": "VWAP"}[A.setup] }</b> · shorts <b>{"on" if A.shorts else "off"}</b><br>'
           f'Target <b>{A.rr:g}× risk</b> · min rel. volume <b>{A.min_relvol:g}×</b> · costs <b>{A.cost_pct:g}%</b><br>') +
        f'Watchlist <b>{len(symbols)}</b> stocks · exposure cap <b>{CFG["max_exposure_x"]:g}×</b></div>',
        unsafe_allow_html=True)
    st.caption("These are the same for everyone. A reviewer changes them in 🎯 Agent scores → Fine-tune.")
ADMIN_PIN = _secret("ADMIN_PIN")


def admin_gate(where):
    """Reviewer PIN gate shared by tabs. Returns True when changes are allowed."""
    if not ADMIN_PIN:
        return True
    if st.session_state.get("admin_ok"):
        return True
    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    pin = c1.text_input("Reviewer PIN", type="password", key=f"pin_{where}")
    if c2.button("Unlock", key=f"unlock_{where}", width="stretch"):
        st.session_state.admin_ok = pin == str(ADMIN_PIN)
        st.rerun()
    return False
cfg_slot.caption(f"Agent settings v{CFG_STATE['version']} · exit: {CFG['exit_policy']} · Coach filter "
                 f"{'on' if CFG['coach_filter'] else 'off'} · review in 🎯 Agent scores")

# ============================================================================ text size
FONT_ZOOM = {"s": 0.9, "m": 1.0, "l": 1.18}
EMBEDDED = st.query_params.get("src") == "aishorts"
_fq = st.query_params.get("font", "m")
st.session_state.setdefault("font", _fq if _fq in FONT_ZOOM else "m")
with st.sidebar:
    _f = st.radio("Text size", ["s", "m", "l"], horizontal=True, key="font",
                  format_func={"s": "Small", "m": "Medium", "l": "Large"}.get,
                  help="Saved in the page address, so bookmarks and the aishorts.biz page keep your choice.")
if st.query_params.get("font") != _f:
    st.query_params["font"] = _f
if FONT_ZOOM[_f] != 1.0:
    st.markdown(f"""<style>
    [data-testid="stMainBlockContainer"], [data-testid="stSidebarContent"] {{ zoom: {FONT_ZOOM[_f]}; }}
    </style>""", unsafe_allow_html=True)

# ============================================================================ masthead
st.markdown(
    ('' if EMBEDDED else
     '<div class="sl mast"><div><span class="brand">Stock Lab</span><span class="beta">Beta · Under trials</span></div>'
     '<div class="mast-r">CHEETAH engine · NSE · AIShorts</div></div>')
    + ('<div class="dek">Rule-based setups with entry, stop and target. Settings: tap <b>›</b> top-left.</div>'
       if EMBEDDED else
       '<div class="dek">Rule-based setups with entry, stop and target. Educational tool — not investment advice. '
       'Prices can lag a few minutes; confirm on your broker\'s chart. Settings: tap <b>›</b> top-left.</div>'),
    unsafe_allow_html=True)
if demo:
    st.info("Demo mode: synthetic prices. Switch it off in Settings for the live market feed.")

tab_i, tab_t, tab_r, tab_g, tab_b, tab_s, tab_a = st.tabs(["⚡ Intraday", "📒 Trade log", "🔭 Strategy lab",
                                                           "🎯 Agent scores", "🧪 Backtest", "📈 Swing picks",
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
            data, fetched = fetch_intraday(symbols, demo, 15, f"{ss.tok}-{int(ss.tok_t // 60)}")
        now = pd.Timestamp.now(tz=ce.IST)
        is_open, mkt_txt = market_state(now)
        if demo:
            is_open, mkt_txt = True, "Demo session"
        if not data:
            st.error("No data returned. Check the symbols or tap Refresh in a minute.")
            return
        if A.strategy:              # a Scout strategy with a Nifty filter needs today's index bars
            A.idx_bars = fetch_index_cached(demo, 0 if demo else f"{ss.tok}-{int(ss.tok_t // 60)}").get("intraday")
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
            try:
                train_coach(demo, symbols, day_s, ARGS_KEY, A)
            except StoreError:
                st.caption(f"⚠️ Coach training skipped — the database did not answer ({STORE.last_error}). "
                           "It retries on the next refresh.")
        idx = fetch_index_cached(demo, 0 if demo else f"{ss.tok}-{int(ss.tok_t // 60)}")
        mood = ag.pulse(idx, day, mults=CFG["mults"])
        size_mult = mood["mult"] if CFG["pulse_sizing"] else 1.0
        store.log("Pulse", "regime", f"{mood['regime']} — position size ×{size_mult:g}. " + "; ".join(mood["reasons"]),
                  detail=mood, dkey=f"pulse|{day_s}|{mood['regime']}")
        ct_ = coach_trades(demo, ss.tok)
        if not ct_.empty:            # learn only from the signal engine that is live now
            ct_ = ct_[ct_.setup.isin(ENGINE_SETUPS)]
            _e = ct_.features.map(lambda f: ag._feats(f).get("_eng"))
            ct_ = ct_[_e.isna() | (_e == ENG_ID)]       # rows from older engine settings are left out
        coach = ag.Coach(ct_, CFG["half_life"], day, plan=PLAN)
        brain = None
        if CFG["coach_model"] in ag.ml.KINDS:
            with st.spinner(f"Coach is training its {ag.BRAINS[CFG['coach_model']].lower()} (once a day)…"):
                sa_ = ag.study_args(CFG)
                brain = brain_cached(demo, symbols, day_s, _study_key(sa_), PLAN,
                                     CFG["coach_model"], CFG)
        targets = list(dict.fromkeys(sig.symbol.tolist() + S[S.in_play].symbol.tolist()))[:12]
        ensure_daily_scan(demo, symbols, store)             # whole watchlist, in the background, once a day
        sents = sentinel_batch(tuple(targets), demo, day_s, bool(API_KEY)) if targets else {}
        for sym, v in sents.items():
            if v["level"] != "CLEAR":
                store.log("Sentinel", v["level"].lower(), f"{v['level']}: " + "; ".join(v["reasons"][:2]) +
                          (f" — {v['llm']}" if v.get("llm") else ""), symbol=sym, detail=v,
                          dkey=f"sentinel|{day_s}|{sym}|{v['level']}")
        if targets:
            store.log("Sentinel", "scan", f"Checked {len(targets)} stocks in play: "
                      f"{sum(v['level'] == 'HIGH' for v in sents.values())} high-risk, "
                      f"{sum(v['level'] == 'WATCH' for v in sents.values())} to watch, "
                      f"{sum(v['level'] == 'UNCHECKED' for v in sents.values())} could not be checked",
                      dkey=f"sentinel-scan|{day_s}|{','.join(sorted(targets))}")
        for l in coach.lessons():
            store.log("Coach", "lesson", f"{l['direction'].title()}: {l['text']} — averages {l['avg_r']:+.2f}R, won "
                      f"{l['win_rate'] * 100:.0f}% of {l['n']} trades", detail=l,
                      dkey=f"lesson|{day_s}|{l['feature']}={l['bucket']}|{l['direction']}")

        agt, journal = {}, []
        WIN = int(CFG.get("paper_window_min", 0) or 0)
        idd = ag.index_day(idx, day)
        policy = CFG["exit_policy"]
        sig["key"] = [ag.trade_key(day, a_, b_, c_) for a_, b_, c_ in zip(sig.symbol, sig.setup, sig.side)]
        sig["qty_base"] = sig.qty
        for ix, r in sig.iterrows():
            key = r.key
            g = bars[r.symbol]
            sd = dict(i=g.index.get_loc(r.signal_time), side=int(r.side), entry=r.entry, stop=r.stop, target=r.target,
                      setup=r.setup)
            dl = r.signal_time + pd.Timedelta(minutes=WIN) if WIN else None
            sims = {p: ag.simulate_exit(g, sd, p, A, dl) for p in ag.EXIT_POLICIES}
            plain = ag.simulate_exit(g, sd, "fixed", A)      # Coach learns from the plain plan, as in the replay
            act = sims[policy]
            status = ag.exit_status(act)
            closed = status != "OPEN"
            sig.at[ix, "status"] = status
            sig.at[ix, "exit_price"] = act["exit"] if closed else np.nan
            sv = sents.get(r.symbol)
            news = ("unknown" if sv is None or sv["level"] == "UNCHECKED" else
                    "event" if sv["level"] in ("WATCH", "HIGH") else "clear")
            info = dict(relvol=r.relvol, gap_pct=r.gap_pct, or_pct=(r.or_high - r.or_low) / g.Open.iloc[0] * 100)
            m_at = ag.pulse(idx, day, asof=r.signal_time, mults=CFG["mults"])
            feats = ag.signal_features(g, sd, info, idd, news, mood=m_at["regime"])
            feats["_x"] = ag.model_features(g, sd, info, idd, m_at)
            sc = ag.score_signal(coach, brain, feats, CFG)
            pm = coach.postmortem(feats, act["r"]) if closed else None
            if plain["reason"] != "close":
                pw = {}
                for p_ in ag.EXIT_POLICIES:
                    for w_ in ag.WINDOWS:
                        v_ = ag.simulate_exit(g, sd, p_, A, r.signal_time + pd.Timedelta(minutes=w_) if w_ else None)
                        pw[f"{p_}|{w_}"] = [round(v_["r"], 4), v_["t"].isoformat()]
                feats_j = dict(feats, _bias=m_at["bias"], _pw=pw, _eng=ENG_ID,
                               _exits={p_: pw[f"{p_}|0"] for p_ in ag.EXIT_POLICIES})
                journal.append(dict(key=key, ts=r.signal_time, day=day, source="live", symbol=r.symbol, setup=r.setup,
                                    side=int(r.side), entry=r.entry, stop=r.stop, target=r.target,
                                    exit=plain["exit"], r=plain["r"], status=plain["reason"],
                                    features=feats_j, reason=pm["primary"] if pm else None))
            if pm:
                store.log("Coach", "post-mortem", f"{r.symbol} {r.setup} lost {act['r']:+.2f}R. Main reason: {pm['primary']}",
                          symbol=r.symbol, detail=dict(pm, features=feats), dkey=f"pm|{key}")
            if act["note"]:
                store.log("Exit", "manage", f"{r.symbol} ({policy}): {act['note']}", symbol=r.symbol,
                          dkey=f"exit|{key}|{act['note']}")
            align = "with" if m_at["bias"] == r.side else "against" if m_at["bias"] == -r.side else "neutral"
            row_min = dict(symbol=r.symbol, side=int(r.side), setup=r.setup, entry=float(r.entry),
                           stop=float(r.stop), target=float(r.target))
            narr = narrate_cached(key, status, json.dumps(row_min), json.dumps(mood, default=float),
                                  json.dumps(sv or {}, default=str), json.dumps(sc, default=float), bool(API_KEY))
            store.log("Narrator", "explain", narr, symbol=r.symbol, dkey=f"narr|{key}")
            agt[key] = dict(align=align, sentinel=sv["level"] if sv else None, sent=sv, coach=sc, pm=pm, narr=narr,
                            feats=feats, exp_r=sc.get("exp_r"), risk_rs=RISK_RS, exit=act, sims=sims, policy=policy,
                            status=status, is_open=not closed, exit_r=act["r"] if closed else None,
                            m_at=m_at, deadline=dl, sd=sd)
        store.upsert_trades(journal)

        # Risk agent — chronological, so each decision only knows what had happened by then
        corr = ag.corr_pairs(data, sorted(set(sig.symbol)), day) if len(sig) > 1 else {}
        book = []
        for ix, r in sig.sort_values("signal_time").iterrows():
            a = agt[r.key]
            sm = a["m_at"]["mult"] if CFG["pulse_sizing"] else 1.0      # mood at the moment the signal fired
            base_q = 0 if pd.isna(r.qty_base) else int(r.qty_base)
            value_x = base_q * sm * float(r.entry) / A.capital          # position value in multiples of capital
            caution = CFG["coach_filter"] and a["coach"].get("verdict") == "Caution"
            if caution:
                verdict, rmul, why = "BLOCK", 0.0, ["Coach filter: " + (a["coach"].get("caution_why") or "weak signal")]
            else:
                verdict, rmul, why = ag.risk_check(dict(symbol=r.symbol, side=int(r.side), setup=r.setup,
                                                        t=r.signal_time, exp_r=a["exp_r"], sentinel=a["sentinel"],
                                                        value_x=value_x), book, CFG, corr)
            qty_final = int(base_q * sm * rmul)
            a.update(risk_verdict=verdict, risk_why=why, risk_mult=rmul, qty_final=qty_final, caution=caution,
                     size_mult=sm)
            sig.at[ix, "qty"] = qty_final
            if verdict != "BLOCK":
                book.append(dict(symbol=r.symbol, side=int(r.side), t=r.signal_time, value_x=value_x * rmul,
                                 t_exit=None if a["is_open"] else a["exit"]["t"],
                                 r=(a["exit"]["r"] if not a["is_open"] else 0) * sm * rmul))
            if verdict != "ALLOW":
                store.log("Risk", verdict.lower(), f"{verdict} {r.symbol} {r.setup}: {why[0]}", symbol=r.symbol,
                          dkey=f"risk|{r.key}|{verdict}")
        sig["blocked"] = [agt[k]["risk_verdict"] == "BLOCK" for k in sig.key]
        sig["is_open"] = sig.status == "OPEN"

        # ------------------------------------------------ Paper Trader + Analyst
        prev = STORE.paper_list(days=3)
        prev = {k: row for k, row in zip(prev.key, prev.itertuples())} if not prev.empty else {}
        now_seen = pd.Timestamp.now(tz=ce.IST)
        prow = []
        for _, r in sig.iterrows():
            a = agt[r.key]
            skipped = a["risk_verdict"] == "BLOCK"
            base_q = 0 if pd.isna(r.qty_base) else int(r.qty_base)
            qty = int(base_q * a["size_mult"]) if skipped else a["qty_final"]
            pt = ag.paper_trade(bars[r.symbol], a["sd"], A, policy, WIN, qty, idd)
            status = "SKIPPED" if skipped else pt["status"]
            if skipped and pt["status"] == "OPEN":
                status = "SKIPPED"
            sc = a["coach"]
            snap = dict(mood=a["m_at"]["regime"], mood_now=mood["regime"], sentinel=a["sentinel"],
                        sentinel_why=(a["sent"] or {}).get("reasons", [])[:2], exp_r=sc.get("exp_r"), conf=sc.get("conf"),
                        coach_verdict=sc.get("verdict"), coach_model=sc.get("model"),
                        drivers=[dict(edge=d[0], text=d[1], n=d[2]) for d in (sc.get("drivers") or [])][:6],
                        risk_verdict=a["risk_verdict"], risk_why=a["risk_why"], features=a["feats"], narr=a["narr"],
                        size_mult=a["size_mult"], risk_mult=a["risk_mult"])
            rec = dict(pt, symbol=r.symbol, setup=r.setup, side=int(r.side), entry=float(r.entry), stop=float(r.stop),
                       target=float(r.target), qty=qty, window_min=WIN, policy=policy, snapshot=snap, status=status,
                       skip_reason=a["risk_why"][0] if skipped else None, cfg_version=CFG_STATE["version"])
            done = status == "CLOSED" or (status == "SKIPPED" and pt["status"] == "CLOSED")
            rec["factors"] = ag.review_factors(rec) if done else []
            old = prev.get(r.key)
            logged_at = old.logged_at if old is not None and pd.notna(old.logged_at) else now_seen
            rec["live"] = bool(logged_at <= pt["entry_time"].tz_localize(ce.IST) + pd.Timedelta(minutes=10))
            a["paper"] = rec
            od = (old.data or {}) if old is not None else {}
            changed = (old is None or old.status != status or od.get("reason") != rec.get("reason")
                       or abs((od.get("r") or 0) - rec["r"]) >= 0.05)
            if changed:
                prow.append(dict(key=r.key, day=day, symbol=r.symbol, status=status, rec_time=pt["entry_time"],
                                 logged_at=logged_at, data=rec))
            side_w = "BUY" if r.side == 1 else "SELL"
            if old is None and not skipped:
                store.log("Trader", "open", f"Paper {side_w} {qty:,} {r.symbol} @ {inr(r.entry)} at {pt['entry_time']:%H:%M} — "
                          f"stop {inr(r.stop)}, target {inr(r.target)}, "
                          + (f"close by {pt['deadline']:%H:%M}" if pt["deadline"] is not None else "hold to plan"),
                          symbol=r.symbol, dkey=f"paper-open|{r.key}")
            if done and (old is None or old.status in ("OPEN",) or (od.get("reason") is None)):
                verb = "Shadow-tested skipped" if skipped else "Paper closed"
                store.log("Trader", "close", f"{verb} {side_w} {r.symbol}: {pt['reason']} at {pt['exit_time']:%H:%M} "
                          f"@ {inr(pt['exit_price'])} → {rec['r']:+.2f}R ({'₹' if rec['pnl'] >= 0 else '−₹'}{abs(rec['pnl']):,.0f})",
                          symbol=r.symbol, dkey=f"paper-close|{r.key}")
                pos = [f["text"] for f in rec["factors"] if f["kind"] == "pos"][:2]
                neg = [f["text"] for f in rec["factors"] if f["kind"] == "neg"][:2]
                store.log("Analyst", "review", f"{r.symbol} {rec['r']:+.2f}R — helped: " + ("; ".join(pos) or "nothing notable")
                          + " | hurt: " + ("; ".join(neg) or "nothing notable"), symbol=r.symbol,
                          detail=dict(factors=rec["factors"]), dkey=f"analyst|{r.key}")
        STORE.paper_upsert(prow)
        STORE.flush()                    # one write for every audit event this refresh produced
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
                p_ = a.get("paper") or {}
                _alert(r.key, r.status, f"{r.symbol} {side_t} {r.setup}: {r.status} ({a['exit_r']:+.2f}R"
                       + (f", paper {'₹' if p_.get('pnl', 0) >= 0 else '−₹'}{abs(p_.get('pnl', 0)):,.0f}" if p_ else "") + ")",
                       r.symbol, "✅" if a["exit_r"] > 0 else "⛔" if a["exit_r"] < 0 else "⏹️")
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
                    f'<span>New signals sized ×{size_mult:g}{"" if CFG["pulse_sizing"] else " (sizing off)"}</span>'
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
                                f'Expected <b>{sc["exp_r"]:+.2f}R</b> after costs · win odds {sc["conf"] * 100:.0f}%'
                                + (f' <span class="note">from the {html.escape(ag.BRAINS[sc["model"]].lower())}; the rule table '
                                   f'says {sc["conf_rules"] * 100:.0f}%</span>' if sc.get("model") not in (None, "bucket")
                                   and sc.get("conf_rules") is not None else "") + '<br>' +
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
                            f'<br>Quantity {int(r.qty_base) if not pd.isna(r.qty_base) else 0:,} × Pulse {a["size_mult"]:g} '
                            f'({html.escape(a["m_at"]["regime"])} at {r.signal_time:%H:%M}) × '
                            f'Risk {a["risk_mult"]:.2f} = <b>{a["qty_final"]:,}</b></div>', unsafe_allow_html=True)
                sv = a.get("sent")
                if sv:
                    hl = "".join(f'<br>· <a href="{html.escape(h["link"])}" target="_blank">{html.escape(h["title"])}</a>'
                                 f' <span class="note">{html.escape(h["publisher"] or "")}</span>' if h["link"] else
                                 f'<br>· {html.escape(h["title"])}' for h in sv["headlines"][:4])
                    st.markdown(f'<div class="sl" style="font-size:13.5px;margin-top:10px"><b>Sentinel — '
                                f'{sv["level"].title() if sv["level"] != "UNCHECKED" else "Not checked"}</b><br>'
                                + "<br>".join(html.escape(x) for x in sv["reasons"]) +
                                f'<br><span class="note">{html.escape(sv.get("coverage", ""))}'
                                + (f' · {html.escape(sv["llm"])}' if sv.get("llm") else " · read by rules") + '</span>' +
                                (f'<br><span class="note">Recent headlines about the company</span>{hl}' if hl else "") + '</div>',
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

# ============================================================================ trade log (paper trades)
st.markdown("""<style>
.tl-card{border:1px solid var(--rule);background:#fff;padding:12px 14px;font-family:"Libre Franklin",sans-serif;font-size:13.5px;line-height:1.5}
.tl-card h4{margin:0 0 6px;font-size:16px}
.tl-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}
@media (max-width:700px){.tl-grid{grid-template-columns:1fr}}
.tl-box{background:var(--paper2);padding:9px 11px}
.tl-box b.h{display:block;font-size:10.5px;letter-spacing:1.1px;text-transform:uppercase;color:var(--meta);margin-bottom:3px}
.fx{padding:3px 0;font-size:13px}.fx.pos::before{content:"▲ ";color:var(--up)}.fx.neg::before{content:"▼ ";color:var(--down)}
.drv{display:grid;grid-template-columns:1fr 70px;gap:2px 10px;align-items:center;font-size:12.5px;margin:4px 0}
.drv .bw{height:8px;background:var(--paper2);border-radius:4px;position:relative;overflow:hidden}
.drv .bw i{position:absolute;top:0;height:8px;border-radius:4px}
.analyst{border-left:4px solid var(--blue);background:#fff;border-top:1px solid var(--rule);border-right:1px solid var(--rule);
 border-bottom:1px solid var(--rule);padding:10px 13px;font-size:14px;line-height:1.55;margin-bottom:10px}
</style>""", unsafe_allow_html=True)


def paper_frame(raw):
    if raw.empty:
        return raw
    rows = []
    for x in raw.itertuples():
        d = dict(x.data or {})
        h = x.human or {}
        rows.append(dict(key=x.key, day=x.day, symbol=x.symbol, status=x.status, rec_time=x.rec_time, logged_at=x.logged_at,
                         side=d.get("side"), setup=d.get("setup"), entry=d.get("entry"), stop=d.get("stop"),
                         target=d.get("target"), qty=d.get("qty"), window_min=d.get("window_min"), policy=d.get("policy"),
                         deadline=d.get("deadline"), exit_time=d.get("exit_time"), exit_price=d.get("exit_price"),
                         reason=d.get("reason"), r=d.get("r"), pnl=d.get("pnl"), mfe=d.get("mfe"), mae=d.get("mae"),
                         mkt_move=d.get("mkt_move"), vol_ratio=d.get("vol_ratio"), hold_r=d.get("hold_r"),
                         later_target=d.get("later_target"), minutes=d.get("minutes"), live=d.get("live"),
                         skip_reason=d.get("skip_reason"), snapshot=d.get("snapshot") or {}, factors=d.get("factors") or [],
                         cfg_version=d.get("cfg_version"),
                         last_price=d.get("last_price"), human=h, verdict=h.get("verdict", "")))
    L = pd.DataFrame(rows)
    for c in ("r", "pnl", "mfe", "mae", "hold_r", "mkt_move", "vol_ratio"):
        L[c] = pd.to_numeric(L[c], errors="coerce")
    return L


def _hm(x):
    try:
        return pd.Timestamp(x).strftime("%H:%M")
    except Exception:
        return "—"


with tab_t:
    ss = st.session_state
    st.markdown('<div class="sl note">The 📝 <b>Trader</b> agent paper-trades every recommendation at its stated time and price, '
                'closes it at the stop, the target or the end of its <b>execution window</b>, and records the result here. '
                'The 🔎 <b>Analyst</b> agent reviews each closed trade and lists what helped and what hurt. '
                'Signals the agents blocked are shadow-tested as <i>skipped</i> so you can check whether blocking them was right.</div>',
                unsafe_allow_html=True)
    L = paper_frame(STORE.paper_list())
    if L.empty:
        st.markdown('<div class="sl empty">No paper trades yet. They appear as soon as the Intraday tab produces a '
                    'recommendation — the Trader records it and closes it at the end of the execution window.</div>',
                    unsafe_allow_html=True)
    else:
        f1, f2, f3 = st.columns([1.3, 1.3, 1.4])
        period = f1.selectbox("Period", ["Today", "Last 7 days", "Last 30 days", "All (90 days)"], index=1, key="tl_period")
        show = f2.selectbox("Show", ["Executed", "Executed + skipped", "Open only", "Skipped only"], key="tl_show")
        q = f3.text_input("Symbol", placeholder="e.g. RELIANCE", key="tl_q")
        last_day = pd.to_datetime(L.day).max()
        cut = {"Today": last_day, "Last 7 days": last_day - pd.Timedelta(days=6),
               "Last 30 days": last_day - pd.Timedelta(days=29), "All (90 days)": pd.Timestamp("2000-01-01")}[period]
        V = L[pd.to_datetime(L.day) >= cut]
        V = {"Executed": V[V.status.isin(["OPEN", "CLOSED"])], "Executed + skipped": V,
             "Open only": V[V.status == "OPEN"], "Skipped only": V[V.status == "SKIPPED"]}[show]
        if q:
            V = V[V.symbol.str.contains(q.strip().upper(), regex=False)]
        ex = V[V.status == "CLOSED"]
        net = ex.pnl.sum() if len(ex) else 0.0
        kpis([("Paper trades", len(V[V.status != "SKIPPED"]), f"{int((V.status == 'OPEN').sum())} open now"),
              ("Win rate", f"{(ex.r > 0).mean() * 100:.0f}%" if len(ex) else "—", f"{len(ex)} closed"),
              ("Net P&L", f'<span class="{"up" if net >= 0 else "down"}">{"▲" if net >= 0 else "▼"} {inr(abs(net), 0)}</span>',
               "after costs"),
              ("Per trade", f"{ex.r.mean():+.2f}R" if len(ex) else "—", "average result"),
              ("Logged live", f"{V.live.mean() * 100:.0f}%" if len(V) else "—", "recorded at recommendation time")])
        st.markdown(f'<div class="sl analyst"><b>🔎 Analyst</b><br>{html.escape(ag.analyst_summary(V, CFG, API_KEY if API_KEY else None))}</div>',
                    unsafe_allow_html=True)
        if len(ex) >= 2:
            eq = ex.assign(t=pd.to_datetime(ex.exit_time)).sort_values("t")
            fig = go.Figure(go.Scatter(x=eq.t, y=eq.pnl.cumsum(), mode="lines+markers", line=dict(color="#121212", width=2),
                                       marker=dict(size=6, color=[UP if v > 0 else DOWN for v in eq.pnl])))
            fig.update_layout(height=230, margin=dict(l=8, r=8, t=8, b=8), template="plotly_white",
                              yaxis_title="Paper P&L (₹)", font=dict(family="Libre Franklin, sans-serif", size=11))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

        if V.empty:
            st.markdown('<div class="sl empty">No trades match this view. Try “Executed + skipped” or a longer period.</div>',
                        unsafe_allow_html=True)
        else:
            section("Trade log book", f"{len(V)} records · newest first")
            R = V.sort_values("rec_time", ascending=False)
            reason_txt = {"target": "Target", "stop": "Stop", "window": "Window end", "time": "15:15 close", "time-stop": "Time-stop"}
            tab = pd.DataFrame({
                "Rec. time": R.rec_time.dt.strftime("%d %b %H:%M"), "Symbol": R.symbol,
                "Side": R.side.map({1: "▲ BUY", -1: "▼ SELL"}), "Setup": R.setup,
                "Entry ₹": R.entry, "Stop ₹": R.stop, "Target ₹": R.target, "Qty": R.qty,
                "Close by": R.deadline.map(lambda x: _hm(x) if x else "plan"),
                "Exit": R.exit_time.map(lambda x: _hm(x) if x else "—"), "Exit ₹": R.exit_price,
                "How": R.reason.map(lambda x: reason_txt.get(x, "—") if x else "—"),
                "R": R.r.map(lambda v: f"{'▲' if v > 0 else '▼' if v < 0 else '•'} {v:+.2f}" if pd.notna(v) else "—"),
                "P&L ₹": R.pnl, "Status": R.status, "Your review": R.verdict.map(
                    {"agree": "👍 agree", "disagree": "👎 disagree", "unsure": "🤔 unsure"}).fillna("")})
            sty = tab.style.map(lambda v: f"color:{UP};font-weight:600" if str(v).startswith("▲") else
                                f"color:{DOWN};font-weight:600" if str(v).startswith("▼") else "", subset=["Side", "R"]) \
                           .map(lambda v: f"color:{UP}" if isinstance(v, (int, float)) and v > 0 else
                                f"color:{DOWN}" if isinstance(v, (int, float)) and v < 0 else "", subset=["P&L ₹"]) \
                           .format({"Entry ₹": "{:,.2f}", "Stop ₹": "{:,.2f}", "Target ₹": "{:,.2f}", "Exit ₹": "{:,.2f}",
                                    "P&L ₹": "{:+,.0f}", "Qty": "{:,.0f}"}, na_rep="—")
            st.dataframe(sty, hide_index=True, width="stretch", height=min(35 * (len(tab) + 1) + 3, 460))
            st.download_button("Download trade log (CSV)", R.drop(columns=["snapshot", "factors", "human"]).to_csv(index=False),
                               "stocklab_paper_trades.csv", "text/csv")

            # ---------------- review one trade
            section("Review a trade", "recommendation → result → why")
            labels = [f'{t:%d %b %H:%M} · {s_} {"▲BUY" if sd_ == 1 else "▼SELL"} {st_} · '
                      + (f"{r_:+.2f}R" if pd.notna(r_) else "open")
                      for t, s_, sd_, st_, r_ in zip(R.rec_time, R.symbol, R.side, R.setup, R.r)]
            pick = st.selectbox("Trade", range(len(R)), format_func=lambda i: labels[i], key="tl_pick")
            if not isinstance(pick, (int, np.integer)) or pick >= len(R):
                pick = 0
            T_ = R.iloc[pick]
            sn = T_.snapshot or {}
            side_w = "BUY" if T_.side == 1 else "SELL"
            rec_html = (f'<b class="h">Recommendation · {T_.rec_time:%d %b %H:%M}</b>'
                        f'{side_w} {int(T_.qty or 0):,} at <b class="num">{inr(T_.entry)}</b> · stop {inr(T_.stop)} · target {inr(T_.target)}<br>'
                        f'Execution window: {ag.WINDOW_TEXT.get(int(T_.window_min or 0), str(T_.window_min) + " min")}'
                        + (f' (close by {_hm(T_.deadline)})' if T_.deadline else "") + f' · exit policy {T_.policy}'
                        + (f' · settings v{int(T_.cfg_version)}' if pd.notna(T_.cfg_version) else "") + '<br>'
                        f'<span class="note">Agents at the time — Pulse: {html.escape(str(sn.get("mood", "—")))} · '
                        f'Sentinel: {html.escape(str(sn.get("sentinel") or "not checked"))} · Coach: '
                        + (f'{sn["exp_r"]:+.2f}R expected ({html.escape(str(sn.get("coach_verdict")))})' if sn.get("exp_r") is not None else "learning")
                        + f' · Risk: {html.escape(str(sn.get("risk_verdict", "—")))}</span>'
                        + (f'<br><span class="note">Skipped: {html.escape(str(T_.skip_reason))}</span>' if T_.status == "SKIPPED" else ""))
            if T_.status == "OPEN":
                out_html = (f'<b class="h">Result so far</b>Open · last {inr(T_.last_price)} → <b>{T_.r:+.2f}R</b> '
                            f'({inr(T_.pnl, 0)}) · best +{(T_.mfe or 0):.1f}R, worst −{(T_.mae or 0):.1f}R')
            else:
                out_html = (f'<b class="h">Result{" (shadow — not executed)" if T_.status == "SKIPPED" else ""}</b>'
                            f'Closed {_hm(T_.exit_time)} by {reason_txt.get(T_.reason, T_.reason)} at <b class="num">{inr(T_.exit_price)}</b> → '
                            f'<b class="{"up" if (T_.r or 0) > 0 else "down"}">{T_.r:+.2f}R · {"₹" if (T_.pnl or 0) >= 0 else "−₹"}{abs(T_.pnl or 0):,.0f}</b><br>'
                            f'Held {int(T_.minutes or 0)} min · best +{(T_.mfe or 0):.1f}R · worst −{(T_.mae or 0):.1f}R'
                            + (f' · Nifty {T_.mkt_move:+.2f}% in its direction' if pd.notna(T_.mkt_move) else "")
                            + (f'<br>Holding to the plan instead: {T_.hold_r:+.2f}R' if pd.notna(T_.hold_r) else ""))
            pos = [f for f in T_.factors if f["kind"] == "pos"]
            neg = [f for f in T_.factors if f["kind"] == "neg"]
            fx = ('<div class="tl-grid"><div class="tl-box"><b class="h">What helped</b>'
                  + ("".join(f'<div class="fx pos">{html.escape(f["text"])}</div>' for f in pos) or '<div class="note">—</div>')
                  + '</div><div class="tl-box"><b class="h">What hurt</b>'
                  + ("".join(f'<div class="fx neg">{html.escape(f["text"])}</div>' for f in neg) or '<div class="note">—</div>')
                  + '</div></div>') if T_.factors else '<div class="note" style="margin-top:8px">The Analyst reviews the trade once it closes.</div>'
            drv = sn.get("drivers") or []
            coach_html = ('<div class="note" style="margin-top:8px"><b>Coach evidence at recommendation time:</b> '
                          + "; ".join(f'{"▲" if d["edge"] > 0 else "▼"} {html.escape(d["text"])} (n={d["n"]})' for d in drv)
                          + '</div>') if drv else ""
            st.markdown(f'<div class="sl tl-card"><h4>{html.escape(T_.symbol)} · {side_w} · {html.escape(str(T_.setup))}'
                        f'{" · <span class=note>recorded later, not live</span>" if pd.notna(T_.live) and not bool(T_.live) else ""}</h4>'
                        f'<div class="tl-grid"><div class="tl-box">{rec_html}</div><div class="tl-box">{out_html}</div></div>'
                        f'{fx}{coach_html}'
                        + (f'<div class="note" style="margin-top:8px"><b>Narrator:</b> {html.escape(sn.get("narr", ""))}</div>' if sn.get("narr") else "")
                        + '</div>', unsafe_allow_html=True)
            try:
                dd, _f = fetch_intraday(symbols, demo, 15, f"{ss.get('tok', 0)}-{int(ss.get('tok_t', time.time()) // 60)}")
                gsym = dd.get(T_.symbol)
                if gsym is not None:
                    gday = gsym[gsym.index.date == pd.Timestamp(T_.day).date()]
                    gday = gday[(gday.index.time >= dtime(9, 15)) & (gday.index.time < dtime(15, 30))]
                    if len(gday):
                        figt = go.Figure(go.Candlestick(x=gday.index, open=gday.Open, high=gday.High, low=gday.Low, close=gday.Close,
                                                        increasing=dict(line=dict(color=UP), fillcolor=UP),
                                                        decreasing=dict(line=dict(color=DOWN), fillcolor=DOWN), name="Price"))
                        for nm, y, col in [("Entry", T_.entry, "#1f5f99"), ("Stop", T_.stop, DOWN), ("Target", T_.target, UP)]:
                            figt.add_hline(y=y, line=dict(color=col, dash="dash", width=1), annotation_text=f"{nm} {y:,.2f}",
                                           annotation_position="right", annotation_font=dict(size=10, color=col))
                        t_in = pd.Timestamp(T_.rec_time).tz_localize(None)
                        figt.add_trace(go.Scatter(x=[t_in], y=[T_.entry], mode="markers", name="Paper entry",
                                                  marker=dict(symbol="triangle-up" if T_.side == 1 else "triangle-down", size=14,
                                                              color="#1f5f99", line=dict(color="#fff", width=1.5))))
                        if T_.exit_time:
                            figt.add_trace(go.Scatter(x=[pd.Timestamp(T_.exit_time)], y=[T_.exit_price], mode="markers",
                                                      name="Paper exit", marker=dict(symbol="x", size=12, color="#121212")))
                        if T_.deadline:
                            figt.add_vrect(x0=t_in, x1=pd.Timestamp(T_.deadline), fillcolor="#1f5f99", opacity=0.07, line_width=0,
                                           annotation_text="execution window", annotation_position="top left",
                                           annotation_font=dict(size=10))
                        figt.update_layout(height=330, margin=dict(l=8, r=78, t=8, b=8), xaxis_rangeslider_visible=False,
                                           template="plotly_white", legend=dict(orientation="h", y=1.1, font=dict(size=11)),
                                           font=dict(family="Libre Franklin, sans-serif"))
                        st.plotly_chart(figt, width="stretch", config={"displayModeBar": False}, key=f"tl_chart_{T_.key}")
            except Exception:
                pass

            st.markdown('<div class="sl" style="font-weight:700;margin:10px 0 2px">Your review of this recommendation</div>',
                        unsafe_allow_html=True)
            ok_edit = admin_gate("tradelog")
            hv = T_.human or {}
            c1, c2 = st.columns([1.4, 2.6])
            verdict = c1.radio("Was the recommendation sound?", ["agree", "disagree", "unsure"], horizontal=True,
                               index=["agree", "disagree", "unsure"].index(hv.get("verdict", "unsure")),
                               format_func={"agree": "👍 Agree", "disagree": "👎 Disagree", "unsure": "🤔 Unsure"}.get,
                               key=f"hv_{T_.key}", disabled=not ok_edit)
            tags = c2.multiselect("What drove it, in your view?", ["Market direction", "Entry timing", "Stop placement",
                                  "Target too far", "Window too short", "Window too long", "News / event", "Volume",
                                  "Position size", "Good setup", "Bad setup", "Luck / noise"],
                                  default=hv.get("tags", []), key=f"ht_{T_.key}", disabled=not ok_edit)
            note = st.text_input("Note", value=hv.get("note", ""), key=f"hn_{T_.key}", disabled=not ok_edit,
                                 placeholder="What should the agents learn from this one?")
            if st.button("Save review", disabled=not ok_edit, key=f"hs_{T_.key}"):
                who = ss.get("reviewer", "Reviewer")
                STORE.paper_review(T_.key, dict(verdict=verdict, tags=tags, note=note, by=who, ts=now_ist().isoformat()))
                STORE.log("Reviewer", "trade-review", f"{who} reviewed {T_.symbol} {side_w} {T_.rec_time:%d %b %H:%M}: "
                          f"{verdict}" + (f" · {', '.join(tags)}" if tags else "") + (f" — {note}" if note else ""),
                          symbol=T_.symbol, dkey=f"hrev|{T_.key}|{now_ist():%Y%m%d%H%M%S}")
                st.toast("Review saved", icon="✅")
                st.rerun()

            # ---------------- drivers across the log
            section("What drives the results", "every closed trade's factors, averaged")
            DT = ag.driver_table(V)
            if DT.empty:
                st.markdown('<div class="sl empty">Factors appear once trades have closed.</div>', unsafe_allow_html=True)
            else:
                mx = max(DT.avg_r.abs().max(), 0.01)
                rows_h = "".join(
                    f'<div class="drv"><div><span class="{"up" if r_.kind == "pos" else "down"}">'
                    f'{"▲ helped" if r_.kind == "pos" else "▼ hurt"}</span> · {html.escape(r_.factor)} '
                    f'<span class="note">· {int(r_.n)} trade{"s" if r_.n != 1 else ""}, {int(r_.wins)}W/{int(r_.losses)}L</span>'
                    f'<div class="bw"><i style="left:{50 if r_.avg_r >= 0 else 50 - abs(r_.avg_r) / mx * 50}%;'
                    f'width:{abs(r_.avg_r) / mx * 50}%;background:{UP if r_.avg_r >= 0 else DOWN}"></i></div></div>'
                    f'<b class="num {"up" if r_.avg_r >= 0 else "down"}">{r_.avg_r:+.2f}R</b></div>'
                    for r_ in DT.sort_values("avg_r", ascending=False).itertuples())
                st.markdown(f'<div class="sl">{rows_h}</div>', unsafe_allow_html=True)
                st.caption("▲ helped / ▼ hurt is the Analyst's reading of each factor; the bar is the average result of "
                           "closed trades where it appeared. If a 'hurt' factor keeps showing good averages, the Analyst's rule "
                           "is wrong for this market. Small counts are noisy — treat under ~10 trades as anecdotes.")
            hr_ = V[V.verdict.isin(["agree", "disagree"]) & (V.status == "CLOSED")]
            if len(hr_):
                g_ = hr_.groupby("verdict").r.agg(["size", "mean"])
                st.caption("Your reviews vs results: " + " · ".join(f"{k} — {int(v['size'])} trades, {v['mean']:+.2f}R avg"
                                                                      for k, v in g_.iterrows()))
            if not ss.get("tok"):
                pass
            st.caption("Paper trades are filled at the recommendation bar's open price and closed at the first stop, target, "
                       "window end or 15:15 — with costs. Prices come from Yahoo 5-minute bars, so real fills will differ. "
                       "'Recorded later' means the app wasn't open when the recommendation happened; the rules are the same, "
                       "but the result wasn't watched live.")

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
            if A.strategy:
                A.idx_bars = fetch_index_cached(demo, f"bt-{pd.Timestamp.now(tz=ce.IST):%Y%m%d%H}").get("intraday")
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
AG_ICON = {"Reviewer": "👤", "Scout": "🔭", "Coach": "🎓", "Pulse": "🧭", "Exit": "🚪", "Risk": "⚖️", "Sentinel": "🛡️", "Narrator": "🗣️",
           "Trader": "📝", "Analyst": "🔎"}
AG_ROLE = {"Coach": "Predicts each signal's expected profit and can filter weak ones",
           "Pulse": "Reads market mood and shrinks positions on weak days",
           "Exit": "Chooses and runs the exit policy",
           "Risk": "Daily loss cap, trade and position caps, correlation check",
           "Trader": "Paper-trades each recommendation inside its execution window",
           "Sentinel": "Reads exchange filings and news; flags results days, bans and material events",
           "Narrator": "Explains each signal in plain English"}


def score_card_html(name, a):
    sc_ = a.get("score")
    tone = a.get("tone", "mid")
    col = {"good": UP, "bad": DOWN}.get(tone, "#9a9a94")
    eff = a.get("effect")
    eff_txt = ("" if eff is None or pd.isna(eff) else
               f'<div class="se"><b class="{"up" if eff > 0 else "down" if eff < 0 else ""}">{eff:+.3f}{a.get("effect_unit", "R")}</b> '
               f'{html.escape(a.get("effect_label", ""))}'
               + (f' · ≈ {"₹" if a["rs_month"] >= 0 else "−₹"}{abs(a["rs_month"]):,.0f}/month'
                  if pd.notna(a.get("rs_month")) else "") + '</div>')
    bar = (f'<div class="sbar"><div style="width:{sc_}%;background:{col}"></div><span style="left:50%"></span></div>'
           if sc_ is not None else '<div class="sbar dim"></div>')
    return (f'<div class="scard"><div class="sh2"><span>{AG_ICON.get(name, "")} <b>{name}</b></span>'
            f'<span class="sscore" style="color:{col}">{sc_ if sc_ is not None else "—"}<small>/100</small></span></div>'
            f'<div class="role">{html.escape(AG_ROLE.get(name, ""))}</div>{bar}'
            f'<div class="grade" style="color:{col}">{html.escape(a["grade"])}'
            + (f' <small>· {a["n"]} {a.get("n_label", "trades")}, z={a["z"]:+.1f}</small>' if a.get("n") and a.get("z") is not None else "")
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
.warnbox{border:1px solid #e3b4b4;border-left:5px solid var(--red, #c2181f);background:#fff7f6;padding:11px 13px;margin:4px 0 12px;
 font-family:"Libre Franklin",sans-serif;font-size:13.5px;line-height:1.5}
.rules{border:1px solid var(--rule);background:#fff;padding:11px 13px;font-family:"Libre Franklin",sans-serif;font-size:13.5px;
 line-height:1.55;margin:6px 0}
</style>""", unsafe_allow_html=True)


def sys_line(m):
    return (f'{m["trades"]} trades · <b>{m["exp"]:+.3f}R</b> per trade · total {m["total"]:+.1f}R · '
            f'max drawdown {m["maxdd"]:+.1f}R · win {m["win"] * 100:.0f}%' if m["trades"] else "no trades")


# ============================================================================ strategy lab (Scout)
with tab_r:
    ss = st.session_state
    can_edit_r = (not ADMIN_PIN) or ss.get("admin_ok", False)
    st.markdown('<div class="sl note">🔭 <b>Scout</b> is the strategy-research agent. The other agents can only filter, '
                'size or exit the signals they are given; Scout looks for <b>better signals</b>. Every day it tests '
                'thousands of intraday strategies — 8 setup families with different timings, sides, market and volume '
                'filters, stop sizes and targets — using the same fills and costs as the live engine. It picks candidates on '
                'the <b>older</b> sessions and keeps only those that stayed profitable on the <b>newest sessions, which '
                'played no part in choosing them</b>. A strategy only goes live after a reviewer approves it.</div>',
                unsafe_allow_html=True)
    obj = st.radio("Scout is looking for", list(rs.OBJECTIVES), list(rs.OBJECTIVES).index(CFG["scout_objective"]),
                   format_func=rs.OBJECTIVES.get, horizontal=True, key="scout_obj")
    with st.spinner("Scout is testing strategies on recent sessions (once a day)…"):
        RES = scout_for(CFG, demo, obj)
    if RES is None:
        st.markdown('<div class="sl empty">Not enough history yet (needs about 15 sessions of 5-minute data).</div>',
                    unsafe_allow_html=True)
    else:
        sl, conf = RES["shortlist"], RES["confirmed"]
        cur = RES["current"] if CFG.get("strategy") and RES["current"] else None
        base = (cur or RES["classic"])
        base_lbl = ("Approved strategy: " + cur["label"]) if cur else RES["classic"]["label"]
        best = conf.iloc[0] if len(conf) else None
        bc = base["c"]
        kpis([("Strategies tested", f'{RES["tested"]:,}', f'{RES["days"]} sessions · {RES["n_raw"]:,} signals'),
              ("Current engine", f'{bc["win"] * 100:.0f}% won' if bc["n"] else "—",
               f'{bc["exp"]:+.2f}R per trade on unseen days' if bc["n"] else "no trades"),
              ("Best that held up", f'<span class="up">{best.c_win * 100:.0f}% won</span>' if best is not None else "none",
               f'{best.c_exp:+.2f}R per trade · ~{best.c_pm:.0f} signals/month' if best is not None else "nothing passed"),
              ("Held up", f'{int(sl.held.sum())} of {len(sl)}', "shortlisted strategies, on unseen days")])
        st.caption(f'Chosen on {RES["n_disc_days"]} older sessions ({RES["period"][0]} → before {RES["cut"]}); checked on '
                   f'the newest {RES["n_conf_days"]} sessions ({RES["cut"]} → {RES["period"][1]}). Results are after '
                   f'{CFG["cost_pct"]:g}% round-trip costs, one trade per stock per strategy per day.'
                   + ("" if RES["has_idx"] else " Nifty bars were unavailable, so Nifty-based filters were skipped."))
        if best is None:
            st.markdown('<div class="sl warnbox"><b>No strategy held up on the unseen sessions.</b> Everything that looked '
                        'good on the older days stopped working on the newest ones — which is what happens when there is no '
                        'real edge. Scout will keep testing every day as new sessions arrive. Until something holds up, the '
                        'safest choice is to paper trade only.</div>', unsafe_allow_html=True)

        section("Shortlist", "picked on older days → tested on unseen days")
        tab_rows = [dict(Strategy="● " + base_lbl,
                         **{"Won · older": base["d"]["win"] * 100 if base["d"]["n"] else np.nan,
                            "Won · unseen": bc["win"] * 100 if bc["n"] else np.nan,
                            "R/trade · older": base["d"]["exp"], "R/trade · unseen": bc["exp"],
                            "Trades · unseen": bc["n"], "Signals / month": np.nan, "Held up": "current"})]
        for r in sl.itertuples():
            tab_rows.append({"Strategy": r.label, "Won · older": r.d_win * 100, "Won · unseen": r.c_win * 100,
                             "R/trade · older": r.d_exp, "R/trade · unseen": r.c_exp, "Trades · unseen": r.c_n,
                             "Signals / month": r.c_pm, "Held up": "✓ yes" if r.held else "✕ no"})
        st.dataframe(pd.DataFrame(tab_rows).round({"Won · older": 0, "Won · unseen": 0, "R/trade · older": 2,
                                                    "R/trade · unseen": 2, "Signals / month": 0}),
                     hide_index=True, width="stretch")
        st.caption("Win rate alone can mislead: a strategy with a small target wins often but may still lose money. "
                   "Scout only shortlists strategies whose profit per trade was positive, and 'Held up' means it stayed "
                   "profitable on the unseen days. Signals per month are before the Risk agent's daily caps.")

        section("Inspect a strategy", "rules, results by day, what-if with all agents")
        ids = list(sl.id)
        if ids:
            labels_ = dict(zip(sl.id, sl.label))
            default = ids.index(best.id) if best is not None and best.id in ids else 0
            pick = st.selectbox("Strategy", ids, default, format_func=lambda i: labels_[i], key="scout_pick")
            row = sl[sl.id == pick].iloc[0]
            spec = row.spec
            st.markdown(f'<div class="rules"><b>{html.escape(row.label)}</b><br>{html.escape(rs.describe(spec))}<br>'
                        f'<span class="note">Older sessions: {int(row.d_n)} trades, won {row.d_win * 100:.0f}%, '
                        f'{row.d_exp:+.2f}R each · Unseen sessions: {int(row.c_n)} trades, won {row.c_win * 100:.0f}%, '
                        f'{row.c_exp:+.2f}R each</span></div>', unsafe_allow_html=True)
            fig = go.Figure()
            for key_, name_, col_ in [("classic", "Classic engine", "#9a9a94"), (pick, "This strategy", "#121212")]:
                cvr = RES["curves"].get(key_)
                if cvr is not None and len(cvr):
                    fig.add_trace(go.Scatter(x=pd.to_datetime(cvr.index), y=cvr.cumsum().values, name=name_,
                                             mode="lines", line=dict(color=col_, width=2)))
            fig.add_vline(x=pd.Timestamp(RES["cut"]).timestamp() * 1000, line_dash="dash", line_color="#2b5797")
            fig.add_annotation(x=pd.Timestamp(RES["cut"]), y=1, yref="paper", text="unseen →", showarrow=False,
                               xanchor="left", font=dict(size=11, color="#2b5797"))
            fig.update_layout(height=250, margin=dict(l=8, r=8, t=8, b=8), template="plotly_white",
                              yaxis_title="Cumulative R (every signal)", legend=dict(orientation="h", y=1.14),
                              font=dict(family="Libre Franklin, sans-serif", size=11))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            cand = ag.merged({**CFG, **rs.adopt_change(spec)})
            if CFG.get("strategy") and rs.spec_id(CFG["strategy"]) == pick:
                st.success("This is the signal engine in use now.")
            elif st.toggle("What-if: run this strategy through all the agents (Coach, Risk, Exit, Trader)",
                           key="scout_whatif"):
                with st.spinner("Replaying recent sessions with this strategy and every agent…"):
                    wi = score_for(cand, demo, ss.get("tok", 0) // 10)
                now_sc = score_for(CFG, demo, ss.get("tok", 0) // 10)
                st.markdown(f'<div class="sl prop"><div class="t">What-if on the same sessions</div>'
                            f'<div class="wi">Now: {sys_line(now_sc["system"]) if now_sc else "—"}<br>'
                            f'With this strategy: {sys_line(wi["system"]) if wi else "not enough trades to score"}</div></div>',
                            unsafe_allow_html=True)
                st.caption("The what-if holds each trade to its stop, target or 15:15 (as Scout tested it) and applies "
                           "the Risk agent's caps, so it covers fewer trades than the raw numbers above.")
                if ADMIN_PIN and not can_edit_r:
                    st.caption("Unlock with the reviewer PIN in 🎯 Agent scores to adopt a strategy.")
                note_r = st.text_input("Reason for adopting it (required)", key="scout_note", disabled=not can_edit_r)
                if st.button("Adopt as the signal engine (new settings version)", type="primary",
                             disabled=not (can_edit_r and note_r.strip()), key="scout_apply"):
                    v = save_cfg(STORE, CFG_STATE, cand, f"Scout strategy adopted: {row.label} — {note_r}",
                                 ss.get("reviewer", "Reviewer"), rs.adopt_change(spec))
                    st.toast(f"Settings v{v} are live — new signals use this strategy", icon="✅")
                    st.rerun()
        if CFG.get("strategy"):
            if st.button("Return to the classic ORB + VWAP engine", disabled=not can_edit_r, key="scout_classic"):
                v = save_cfg(STORE, CFG_STATE, ag.merged({**CFG, "strategy": None}), "Returned to the classic engine",
                             ss.get("reviewer", "Reviewer"), {"strategy": None})
                st.toast(f"Settings v{v} — classic engine restored", icon="↩️")
                st.rerun()
        if obj != CFG["scout_objective"] and st.button(f"Make “{rs.OBJECTIVES[obj]}” Scout's goal for proposals",
                                                       disabled=not can_edit_r, key="scout_goal"):
            v = save_cfg(STORE, CFG_STATE, ag.merged({**CFG, "scout_objective": obj}), f"Scout goal: {rs.OBJECTIVES[obj]}",
                         ss.get("reviewer", "Reviewer"), {"scout_objective": obj})
            st.toast(f"Settings v{v}", icon="✅")
            st.rerun()
        with st.expander("What Scout explores"):
            st.markdown("".join(f'<div class="sl" style="font-size:13px;margin:4px 0"><b>{code}</b> — '
                                f'{html.escape(desc.split(":")[0])}</div>'
                                for code, desc, _ in rs.FAMILIES.values()), unsafe_allow_html=True)
            st.caption("Each family is crossed with: longs / shorts / both · with Nifty or not · opening volume ≥ 0, 1.2 "
                       "or 1.5× · minimum stop 0, 0.4 or 0.8% (a wider stop keeps costs small relative to risk) · target "
                       "1, 1.5, 2 or 3× risk.")
        st.caption("Why the unseen-days check matters: with thousands of strategies, some always look great on past data "
                   "by luck. The share that held up tells you how much luck was involved — if few hold up, treat the "
                   "winners with suspicion. 59 sessions is still a short history; paper trade an adopted strategy for a "
                   "few weeks before trusting it."
                   + (" Demo data is synthetic, so these numbers only prove the machinery works." if demo else ""))


with tab_g:
    ss = st.session_state
    can_edit = (not ADMIN_PIN) or ss.get("admin_ok", False)
    st.markdown('<div class="sl note">Every agent is tested <b>walk-forward</b>: each day\'s signals are judged by agents '
                'that only learned from earlier days, so the scores show how they would really have done. '
                'Agents can only <b>propose</b> changes — you approve, reject or fine-tune them here, and every '
                'decision is versioned and logged.</div>', unsafe_allow_html=True)
    with st.spinner("Scoring the agents on recent sessions…"):
        SC = score_for(CFG, demo, ss.get("tok", 0) // 10, lab=True)
    if SC is None:
        st.markdown('<div class="sl empty">Not enough history yet to score the agents (needs about 12 sessions with '
                    'signals). Try more symbols or a lower relative-volume filter in Settings.</div>',
                    unsafe_allow_html=True)
    else:
        b_, s_ = SC["base"], SC["system"]
        if SC.get("losing"):
            st.markdown('<div class="sl warnbox"><b>The signal engine itself is losing money on recent sessions</b> '
                        f'({b_["exp"]:+.2f}R per trade, {b_["win"] * 100:.0f}% won). The agents below can only cut losses — '
                        'they cannot create an edge that the signals don\'t have. See <b>🔭 Strategy lab</b> for strategies '
                        'that held up on unseen days.</div>', unsafe_allow_html=True)
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
                    [n_ for n_ in ["Coach", "Exit", "Trader", "Risk", "Pulse", "Sentinel", "Narrator"]
                     if n_ in SC["agents"]]) + '</div>', unsafe_allow_html=True)
        if SC.get("windows") is not None:
            with st.expander("Execution windows compared on the same recommendations"):
                wt = SC["windows"]
                st.dataframe(pd.DataFrame({"Close after": wt.label, "R per trade": wt.exp.round(3),
                                           "vs current": wt.vs_current.round(3), "Evidence z": wt.z.round(1),
                                           "Win %": (wt.win * 100).round(0)}), hide_index=True, width="stretch")
        with st.expander("Exit policies compared on the same trades"):
            et = SC["exits"].copy()
            st.dataframe(pd.DataFrame({"Policy": et.policy, "How it exits": et.desc, "R per trade": et.exp.round(3),
                                       "vs fixed": et.vs_fixed.round(3), "Evidence z": et.z.round(1),
                                       "Win %": (et.win * 100).round(0)}), hide_index=True, width="stretch")

        # ---------------- model lab: which brain predicts winners best
        LAB = SC.get("lab")
        section("🧠 Model lab", "which brain gives the Coach the best win odds")
        if not LAB or not len(LAB["table"]):
            st.markdown('<div class="sl empty">Not enough finished signals yet to train the models '
                        '(needs about 60 practice signals over 12+ sessions).</div>', unsafe_allow_html=True)
        else:
            lt = LAB["table"]
            act = LAB["active"]
            best = lt.sort_values("win_top30", ascending=False).iloc[0]
            cur = lt[lt.kind == act].iloc[0]
            st.markdown(
                f'<div class="sl note">Every brain scores each signal with a win probability before the result is known; '
                f'models are retrained as sessions are added and only ever tested on days they had not seen. '
                f'All signals won <b>{cur.win_all * 100:.0f}%</b>. Taking only the top 30% by '
                f'{html.escape(best.label.lower())} would have won <b>{best.win_top30 * 100:.0f}%</b> '
                f'({best.r_top30:+.2f}R per trade vs {best.r_all:+.2f}R for all). '
                f'Models learn from {LAB.get("n_study") or 0:,} practice signals (both directions, lower volume bar) '
                f'so they have enough examples; they are judged on the signals the engine really gives.</div>',
                unsafe_allow_html=True)
            st.dataframe(pd.DataFrame({
                "Brain": [("● " if k == act else "") + l for k, l in zip(lt.kind, lt.label)],
                "Win % top 30%": (lt.win_top30 * 100).round(0),
                "Win % top half": (lt.win_top50 * 100).round(0),
                "Win % all": (lt.win_all * 100).round(0),
                "R/trade top 30%": lt.r_top30.round(2),
                "Skill (AUC)": lt.auc.round(2),
                "Evidence z": lt.z.round(1),
                "Skill on practice set": [("—" if pd.isna(v) else f"{v:.2f} (n={int(n_)})") for v, n_ in zip(lt.auc_wide, lt.n_wide)],
                "Recent-half skill": lt.auc_recent.round(2)}), hide_index=True, width="stretch")
            st.caption(f"● = brain in use · {int(cur.n)} real signals tested. Skill (AUC) is the chance a winner was scored "
                       "above a loser: 0.50 is guessing, 0.55–0.60 is useful, above 0.65 is rare on real markets. "
                       "A higher win rate only matters if R per trade rises with it.")
            c1, c2 = st.columns([1, 2], vertical_alignment="bottom")
            pick = c1.selectbox("How selective?", list(lt.kind), list(lt.kind).index(act),
                                format_func=ag.BRAINS.get, key="lab_pick")
            sel = LAB["selectivity"][pick]
            st.dataframe(pd.DataFrame({
                "Take": [f"Top {q * 100:.0f}%" if q < 1 else "All signals" for q in sel.share],
                "Win-odds bar": [("—" if q == 1 else f"≥ {v * 100:.0f}%") for q, v in zip(sel.share, sel.min_p)],
                "Model said": (sel.said * 100).round(0).map("{:.0f}%".format),
                "Actually won": (sel.win * 100).round(0).map("{:.0f}%".format),
                "R per trade": sel.exp.round(2), "Total R": sel.total.round(1),
                "Trades / month": sel.per_month.round(0)}), hide_index=True, width="stretch")
            c2.caption("Each row keeps only the signals this brain liked most. “Win-odds bar” is the value to put in "
                       "“Skip if win odds below” (Fine-tune → Coach) to get that row. If “Model said” and “Actually won” "
                       "are far apart, trust the ranking but not the percentage.")
            if demo:
                st.caption("Demo data is synthetic with trends planted in it, so models look better here than they will "
                           "on the real market.")

        with st.expander("🛡️ Sentinel — sources, today's flags and evidence"):
            job = ensure_daily_scan(demo, symbols, STORE)
            stat = {"running": "⏳ Today's watchlist scan is running in the background — refresh in a minute.",
                    "done": "✓ Today's watchlist scan is complete.", "skipped": "Weekend — no scan today.",
                    "failed": "✕ Today's scan failed: " + str(job.get("note", ""))}[job["status"]]
            st.markdown(f'<div class="sl note">{html.escape(stat)}</div>', unsafe_allow_html=True)
            res = job.get("results") or {}
            if res:
                src = {}
                for v in res.values():
                    for nm, stt in v["sources"].items():
                        src.setdefault(nm, [0, 0])[0 if stt == "ok" else 1] += 1
                st.markdown('<div class="sl" style="font-weight:700;margin-top:6px">Sources checked today</div>',
                            unsafe_allow_html=True)
                st.dataframe(pd.DataFrame([{"Source": n, "Reached": f"{ok_} of {ok_ + bad_} stocks",
                                            "Status": "✓ working" if bad_ == 0 else "✕ unavailable" if ok_ == 0 else "⚠ partial"}
                                           for n, (ok_, bad_) in src.items()]), hide_index=True, width="stretch")
                fl = [v for v in res.values() if v["level"] in ("HIGH", "WATCH", "UNCHECKED")]
                st.markdown(f'<div class="sl" style="font-weight:700;margin-top:6px">Flagged today · {len(fl)} of {len(res)}</div>',
                            unsafe_allow_html=True)
                if fl:
                    order = {"HIGH": 0, "WATCH": 1, "UNCHECKED": 2}
                    st.dataframe(pd.DataFrame([{"Symbol": v["symbol"], "Level": v["level"].title().replace("Unchecked", "Not checked"),
                                                "Why": v["reasons"][0], "Read by": "Claude" if v.get("llm") else "rules"}
                                               for v in sorted(fl, key=lambda v: order[v["level"]])]),
                                 hide_index=True, width="stretch")
                else:
                    st.caption("Nothing flagged across the watchlist.")
            _, _, stats_ = history_cached(demo, symbols, f"{now_ist():%Y-%m-%d}", ARGS_KEY, A)
            ev = ag.sentinel_evidence(STORE.sentinel_list(), pd.DataFrame(stats_))
            st.markdown('<div class="sl" style="font-weight:700;margin-top:6px">Do flagged days really move more?</div>',
                        unsafe_allow_html=True)
            if ev["n_flag"]:
                st.markdown(f'<div class="sl note">{ev["n_flag"]} flagged stock-days moved <b>{ev["ratio_flag"]:.2f}×</b> their normal '
                            f'daily range; {ev["n_base"]} other stock-days moved <b>{ev["ratio_base"]:.2f}×</b>. '
                            f'1.00× is a normal day for that stock.</div>', unsafe_allow_html=True)
                if len(ev["by_type"]):
                    bt = ev["by_type"]
                    st.dataframe(pd.DataFrame({"Event type": bt.label, "Stock-days": bt.n, "Range vs normal": bt.ratio.round(2),
                                               "Evidence z": bt.z.round(1)}), hide_index=True, width="stretch")
            else:
                st.caption("No flagged stock-days in the price window yet. Each trading day adds the whole watchlist to the "
                           "archive; past results days are added when the exchange calendar is reachable.")
            st.caption(f"Archive: {ev['scanned']} stock-days scanned over {ev['days']} day(s). Sentinel is scored on market "
                       "behaviour — whether stocks really move more on the days it flags — so it can be judged in weeks, "
                       "without waiting for enough trades. Headlines are read by "
                       + ("Claude, with rules as a fallback." if API_KEY else "rules; add ANTHROPIC_API_KEY in Secrets for Claude to read them."))

        # ---------------- human reviews: how each agent lines up with the reviewer
        PL = paper_frame(STORE.paper_list())
        HS = ln.hitl_scores(PL)
        nrev = len(ln.reviewed(PL))
        section("👤 Your reviews", f"{nrev} reviewed trades · how each agent agrees with you")
        if not nrev:
            st.markdown('<div class="sl empty">No reviews yet. In 📒 Trade log → Review a trade, mark recommendations '
                        '👍 / 👎 and tag what drove them. Each agent is then scored on whether it had warned about the '
                        'trades you disagreed with, and your tags can turn into proposals below.</div>', unsafe_allow_html=True)
        else:
            def _hitl_card(name, h):
                a_ = dict(score=h["score"], tone=h.get("tone", "mid"), grade=h["grade"],
                          detail=(f'Warned on {h["catch"] * 100:.0f}% of the {h["n_disagree"]} trades you disagreed with, '
                                  f'and on {h["false_alarm"] * 100:.0f}% of the {h["n_agree"]} you agreed with '
                                  f'({ln.AGENT_WARN_TEXT[name]}).') if pd.notna(h["catch"]) and pd.notna(h["false_alarm"])
                          else f'{h["n_disagree"]} disagree · {h["n_agree"]} agree reviews so far.',
                          setting="Score = 50 + 50 × (catch rate − false-alarm rate)")
                return score_card_html(name, a_)
            st.markdown('<div class="sgrid">' + "".join(_hitl_card(n_, HS[n_]) for n_ in HS) + '</div>',
                        unsafe_allow_html=True)
            TT = ln.tag_table(PL)
            if len(TT):
                with st.expander(f"What you said drove the trades · {int(TT.n.sum())} tags"):
                    st.dataframe(pd.DataFrame({"Tag": TT.tag, "Times": TT.n, "On 👎 trades": TT.disagree,
                                               "Avg result (R)": TT.avg_r.round(2)}), hide_index=True, width="stretch")
                    st.caption(f"A tag used on {ln.MIN_TAG_REVIEWS}+ trades becomes a Reviewer proposal when the trade "
                               "data backs it up (window, target, news blocking, mood sizing, pausing a setup).")
            for n_ in ln.reviewer_notes(PL, 5):
                st.markdown(f'<div class="sl note">“{html.escape(n_["note"])}” — {html.escape(n_["by"] or "Reviewer")}, '
                            f'{html.escape(n_["symbol"])} {n_["when"]:%d %b} ({n_["verdict"]})</div>', unsafe_allow_html=True)

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
        try:
            decisions = STORE.kv_get("proposal_decisions", {}, strict=True) or {}
            dec_ok = True
        except StoreError:               # don't let an outage wipe the decision history on the next click
            decisions, dec_ok = {}, False
            st.warning("Past approve / reject decisions could not be read, so deciding is paused for now.")
        can_decide = can_edit and dec_ok and not CFG_STATE.get("offline")

        def _hidden(p):
            d_ = decisions.get(p["id"], {})
            return d_.get("status") == "rejected" and d_.get("ts", "") >= (
                now_ist() - pd.Timedelta(days=ln.rejection_window_days(d_))).isoformat()
        _sp = rs.proposal(scout_for(CFG, demo), CFG)
        props = [p for p in ([_sp] if _sp else []) + ag.proposals(SC, CFG) + ln.reviewer_proposals(PL, CFG, SC, ag.WINDOWS)
                 if not _hidden(p)]
        st.markdown(f'<div class="sl" style="font-weight:700;margin:4px 0">Proposals from agents · {len(props)}</div>',
                    unsafe_allow_html=True)
        if not props:
            st.markdown('<div class="sl empty">No changes proposed — the evidence doesn\'t clearly favour any other '
                        'setting right now. Rejected proposals stay hidden for 7 days (30 if rejected twice).</div>', unsafe_allow_html=True)
        for k_, p in enumerate(props):
            cand = ag.merged({**CFG, **p["change"]})
            wi = score_for(cand, demo, ss.get("tok", 0) // 10)
            wis = wi["system"] if wi else None
            st.markdown(f'<div class="sl prop"><div class="t">{AG_ICON.get(p["agent"], "")} {html.escape(p["title"])}</div>'
                        f'<div class="w">{html.escape(p["why"])}</div>'
                        + (f'<div class="wi">What-if on the same days — now: {sys_line(s_)}<br>'
                           f'after: {sys_line(wis)}</div>' if wis else "") + '</div>', unsafe_allow_html=True)
            c1, c2, c3 = st.columns([3, 1, 1], vertical_alignment="bottom")
            note = c1.text_input("Note (optional)", key=f"pn_{k_}", disabled=not can_decide,
                                 placeholder="why you approve or reject")
            if c2.button("✓ Approve", key=f"pa_{k_}", disabled=not can_decide, type="primary", width="stretch"):
                v = save_cfg(STORE, CFG_STATE, cand, f"Approved: {p['title']}" + (f" — {note}" if note else ""),
                             reviewer, p["change"])
                decisions[p["id"]] = dict(status="approved", ts=now_ist().isoformat(), by=reviewer, note=note, version=v)
                STORE.kv_set("proposal_decisions", decisions)
                st.toast(f"Approved — settings v{v} are live", icon="✅")
                st.rerun()
            if c3.button("✕ Reject", key=f"pr_{k_}", disabled=not can_decide, width="stretch"):
                decisions[p["id"]] = ln.record_rejection(decisions.get(p["id"]), reviewer, note, now_ist().isoformat())
                STORE.kv_set("proposal_decisions", decisions)
                STORE.log("Reviewer", "reject", f"{reviewer} rejected: {p['title']}" + (f" — {note}" if note else ""),
                          dkey=f"rej|{p['id']}|{now_ist():%Y%m%d%H%M%S}")
                st.toast(f"Rejected — hidden for {ln.rejection_window_days(decisions[p['id']])} days", icon="✕")
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
                t4, t5 = st.columns(2)
                f_cm = t4.selectbox("Coach's brain (win-odds model)", list(ag.BRAINS),
                                    list(ag.BRAINS).index(CFG["coach_model"] if CFG["coach_model"] in ag.BRAINS else "bucket"),
                                    format_func=ag.BRAINS.get, help="Compare them in the Model lab above before switching.")
                f_mw = t5.slider("Skip if win odds below (%)", 0, 90, int(round(float(CFG["coach_min_win"]) * 100)), 1,
                                 help="0 = off. Only applies when 'Filter weak signals' is on.")
                st.markdown("**🧭 Pulse**")
                f_ps = st.toggle("Mood-based sizing", CFG["pulse_sizing"])
                mc = st.columns(3)
                f_m = {m: mc[i % 3].number_input(f"{m} ×", 0.0, 1.5, float(CFG["mults"][m]), 0.05, key=f"m_{m}")
                       for i, m in enumerate(ag.DEFAULT_MULTS)}
                st.markdown("**🚪 Exit**")
                f_win = st.select_slider("Execution window (paper trader closes the trade at the end)", ag.WINDOWS,
                                         int(CFG.get("paper_window_min", 0) or 0), format_func=ag.WINDOW_TEXT.get)
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
                f_ps2 = r6.multiselect("Paused setups", sorted(set(ENGINE_SETUPS + ["ORB", "VWAP"] + list(CFG["paused_setups"]))),
                                       CFG["paused_setups"])
                r7, r8, r9 = st.columns(3)
                f_es = r7.toggle("Edge-based sizing", CFG["edge_sizing"], help="Size up/down (0.5–1.5×) by Coach's expected R")
                f_ek = r8.slider("Edge sizing strength", 0.5, 3.0, float(CFG["edge_k"]), 0.25)
                f_sb = r9.toggle("Block Sentinel HIGH", CFG["sentinel_block"])
                r10, r11 = st.columns(2)
                f_mx = r10.slider("Total exposure cap (× capital)", 1.0, 15.0, float(CFG["max_exposure_x"]), 0.5,
                                  help="All open positions together may not exceed this multiple of capital.")
                f_lev = r11.slider("Max single position (× capital)", 1.0, 5.0, float(CFG["leverage"]), 0.5)
                st.markdown("**⚙️ Signal engine and paper account** — shared by everyone")
                e1, e2, e3 = st.columns(3)
                f_cap = e1.number_input("Paper capital (₹)", 10000, 100_000_000, int(CFG["capital"]), step=10000)
                f_rp = e2.slider("Risk per trade (%)", 0.1, 2.0, float(CFG["risk_pct"]), 0.1)
                f_cost = e3.number_input("Round-trip costs (%)", 0.0, 1.0, float(CFG["cost_pct"]), 0.01)
                e4, e5, e6 = st.columns(3)
                f_setup = e4.selectbox("Setups", ["both", "orb", "vwap"], ["both", "orb", "vwap"].index(CFG["setup"]),
                                       format_func={"both": "ORB + VWAP pullback", "orb": "Opening range breakout",
                                                    "vwap": "VWAP pullback"}.get)
                f_rr = e5.slider("Target (× risk)", 1.0, 4.0, float(CFG["rr"]), 0.5)
                f_rv = e6.slider("Min relative volume", 1.0, 3.0, float(CFG["min_relvol"]), 0.1)
                f_sh = st.toggle("Include short trades", bool(CFG["shorts"]))
                f_wl = st.text_area("Watchlist (NSE symbols, comma-separated)", ", ".join(CFG["watchlist"]), height=110)
                sim = st.form_submit_button("Simulate on recent sessions")
            if sim:
                ss.tune_cand = ag.merged(dict(coach_filter=f_cf, coach_threshold=f_th, half_life=f_hl, coach_model=f_cm,
                                              coach_min_win=f_mw / 100, pulse_sizing=f_ps,
                                              mults=f_m, exit_policy=f_ex, risk_enabled=f_re, daily_loss_R=f_dl,
                                              max_trades=f_mt, max_open=f_mo, corr_limit=f_cl, paused_setups=f_ps2,
                                              edge_sizing=f_es, edge_k=f_ek, sentinel_block=f_sb,
                                              paper_window_min=int(f_win), max_exposure_x=f_mx, leverage=f_lev,
                                              capital=float(f_cap), risk_pct=f_rp, cost_pct=f_cost, setup=f_setup,
                                              rr=f_rr, min_relvol=f_rv, shorts=f_sh,
                                              watchlist=sorted({x.strip().upper() for x in
                                                                f_wl.replace("\n", ",").split(",") if x.strip()})))
                if set(ss.tune_cand["watchlist"]) == set(CFG["watchlist"]):
                    ss.tune_cand["watchlist"] = list(CFG["watchlist"])      # same stocks → not a change
            cand = ss.get("tune_cand")
            if cand:
                diff = cfg_diff(CFG, cand)
                if not diff:
                    st.info("These are the current settings.")
                else:
                    wi = score_for(cand, demo, ss.get("tok", 0) // 10)
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
        _mi = MEMO.get("sig") or {}
        st.caption(f"Scores update daily from the last ~60 sessions Yahoo serves"
                   + (f" plus {_mi['memory_days']} older sessions remembered in the database" if _mi.get("memory_days") else
                      " — older sessions are archived each day, so the history grows past that limit")
                   + ". Treat anything under a few hundred trades as early evidence. Sentinel can only be scored on "
                   "live signals as they build up.")

# ============================================================================ audit log
with tab_a:
    store = STORE
    HL = store.health()
    if demo:
        st.info("Demo mode: this log holds demo activity only and is kept separate from the live log.")
    elif store.remote and HL["ok"]:
        st.success("Storage: Supabase (persistent) — the log, trade journal, settings, Sentinel archive and the agents' "
                   "learning memory survive restarts.")
    elif store.remote:
        st.error("Storage: Supabase is connected but not fully working — " + " ".join(HL["notes"] or
                 ["see the table check below."]))
        if HL.get("sql"):
            st.code(HL["sql"], language="sql")
    elif not store.remote:
        st.warning("The log is currently temporary: it is wiped whenever the hosted app restarts or redeploys. "
                   "Connect Supabase to keep a full week.")
        with st.expander("How to connect Supabase (5 minutes)"):
            st.markdown("1. In your Supabase project, open **SQL editor**, paste the SQL below and run it.\n"
                        "2. In Streamlit (Manage app → Settings → Secrets) add:\n"
                        "```\nSUPABASE_URL = \"https://<your-project>.supabase.co\"\nSUPABASE_KEY = \"<secret key (sb_secret_…) or legacy service_role key>\"\n"
                        "ANTHROPIC_API_KEY = \"<optional — richer agents>\"\n```\n"
                        "3. Save. The app restarts and the Audit log becomes permanent (events kept 7 days, "
                        "trade journal 90 days).")
            st.code(SUPABASE_SQL, language="sql")
    with st.expander("🔌 Storage check" + ("" if HL["ok"] else " · needs attention"), expanded=not HL["ok"] and not demo):
        if store.remote:
            st.markdown(f"Key: **{HL['key_kind']}** · URL: `{store.url}`")
        st.dataframe(pd.DataFrame({"Table": list(HL["tables"]), "Holds": [TABLE_ROLE.get(t, "") for t in HL["tables"]],
                                   "Status": [v["status"] for v in HL["tables"].values()],
                                   "Rows": [v.get("rows") for v in HL["tables"].values()]}), hide_index=True, width="stretch")
        st.caption(f"Writes this session: " + (", ".join(f"{t.replace('stocklab_', '')} {n:,}" for t, n in store.writes.items())
                                               or "none yet") + f" · audit events waiting to be written: {store.pending}")
        if store.errors:
            st.caption("Recent storage errors (newest last):")
            st.code("\n".join(f"{t[11:19]}Z  {m}" for t, m in list(store.errors)[-6:]), language=None)
        if st.button("Re-check storage", key="hc_btn"):
            store.health(force=True)
            store_memory_stats.clear()
            st.rerun()

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
        icons = {"Pulse": "🧭", "Sentinel": "🛡️", "Narrator": "🗣️", "Coach": "🎓", "Alerts": "🔔", "Exit": "🚪", "Risk": "⚖️", "Reviewer": "👤",
                 "Trader": "📝", "Analyst": "🔎"}
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

    # ---------------- learning memory: what the database adds to the agents' training
    section("🧠 Learning memory", f"what the database feeds back into training · kept {MEMORY_DAYS} days")
    AT, AT_tot = distill_audit(demo, f"{now_ist():%Y%m%d%H}")
    mi = MEMO.get("sig") or {}
    mstat = store_memory_stats(demo, f"{now_ist():%Y%m%d%H}")
    PL_a = paper_frame(store.paper_list())
    kpis([("Sessions in history", (mi.get("fresh_days", 0) + mi.get("memory_days", 0)) if mi else "—",
           f"{mi.get('memory_days', 0)} beyond Yahoo's ~60" if mi else "builds once a day"),
          ("Signals studied", f"{mi.get('n', 0):,}" if mi else "—", f"since {mi.get('first_day') or '—'}"),
          ("News filled in", mi.get("news_backfilled", "—") if mi else "—", "history rows tagged from the Sentinel archive"),
          ("Audit days digested", AT_tot.get("days", 0) if AT_tot else 0, f"{AT_tot.get('post_mortems', 0) if AT_tot else 0} losses diagnosed"),
          ("Your reviews", len(ln.reviewed(PL_a)), "scoring agents in 🎯 Agent scores")])
    if not store.memory_ok:
        st.warning("The learning-memory table is missing, so history still stops at ~60 sessions and audit digests are "
                   "not kept. Run this once in Supabase → SQL editor:")
        st.code(MIGRATION_SQL, language="sql")
    st.markdown('<div class="sl note">How the stored logs train the agents: <b>(1)</b> every finished session\'s signals are '
                'archived per signal engine, so the 🎓 Coach, the model lab and the agent scores keep learning from sessions '
                'Yahoo no longer serves; <b>(2)</b> the 🛡️ Sentinel\'s pre-open archive fills in the news condition on those '
                'signals — only where it was recorded before the signal fired; <b>(3)</b> the audit log is digested daily so '
                'recurring loss causes outlive its 7-day window; <b>(4)</b> your reviews in the 📒 Trade log score each agent '
                'and can become proposals. Nothing changes the live settings without a reviewer\'s approval.</div>',
                unsafe_allow_html=True)
    KIND_TXT = {"sig": "Live-engine signals", "study": "Practice signals (models)", "corr": "Daily correlations",
                "dstat": "Daily movement per stock", "audit": "Audit-log digests"}
    if mstat:
        mrows = [dict(What=KIND_TXT.get(k, k), Engine=("current" if e in (ENG_ID, (MEMO.get("study") or {}).get("eng"))
                                                         else "all" if e == "all" else f"older settings ({e[:6]})"),
                      Rows=n, From=a, To=b) for k, by in mstat.items() for e, (n, a, b) in by.items()]
        with st.expander(f"What is stored · {sum(r['Rows'] for r in mrows):,} rows"):
            st.dataframe(pd.DataFrame(mrows), hide_index=True, width="stretch")
            st.caption("Signals from older engine settings are kept but not mixed into training — they come back "
                       "into use if those settings are restored.")
    if AT is not None and len(AT):
        st.markdown('<div class="sl" style="font-weight:700;margin:8px 0 2px">Recurring loss causes · last 30 days vs the 30 before</div>',
                    unsafe_allow_html=True)
        st.dataframe(pd.DataFrame({"Main reason (Coach post-mortems)": AT.text, "Last 30 days": AT.recent,
                                   "30 days before": AT.before,
                                   "Trend": AT.change.map(lambda c: f"▲ +{c}" if c > 0 else f"▼ {c}" if c < 0 else "•")}).head(10),
                     hide_index=True, width="stretch")

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
            try:
                n = train_coach(demo, symbols, f"{pd.Timestamp.now(tz=ce.IST):%Y-%m-%d}", ARGS_KEY, A)
                st.success(f"Coach studied {n} signals.")
            except StoreError:
                st.error(f"The trade journal could not be read, so training was skipped: {store.last_error}")
    st.caption("Agents: 🧭 Pulse market mood · 🛡️ Sentinel news & events · 🗣️ Narrator explanations · "
               "🎓 Coach learning & post-mortems · 🚪 Exit · ⚖️ Risk · 🔔 Alerts · 👤 Reviewer decisions. "
               "Events are kept 7 days (digested into learning memory for 180); the trade journal 90 days.")
STORE.flush()

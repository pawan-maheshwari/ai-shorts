"""
Learning memory for Stock Lab agents — what turns the database into training data.

1. Signal archive   Yahoo only serves ~60 sessions of 5-minute bars, so every replay was forgetting older days.
                    Each day's replayed signals (live engine and the wider study set) are archived per signal engine
                    and merged back in, so the Coach, the models, the scorecard and Scout's evidence keep growing
                    up to MEMORY_DAYS instead of rolling off after 60 sessions.
2. News backfill    Replayed history had news = "unknown". The Sentinel archive records what was known about each
                    stock-day before the session; that view is written into history (point-in-time only), so the
                    Coach learns the news condition from all history, not just live signals.
3. Audit digests    The audit log is kept 7 days. Every hour its days are distilled (agent activity, loss causes,
                    risk blocks, reviewer decisions) into memory kept 180 days, so recurring problems stay visible.
4. Human reviews    Reviews in the Trade log book score each agent (did it warn on the trades you disagreed with,
                    and stay quiet on the ones you agreed with?) and, with enough reviews backed by the numbers,
                    become proposals — still approved by a human before anything changes.
"""
import hashlib
import json
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

MIN_TAG_REVIEWS = 4          # reviews carrying the same tag before the Reviewer agent proposes anything
MIN_HITL = 5                 # agree and disagree reviews each, before an agent gets a human-agreement score


def eng_id(key):
    return hashlib.md5(str(key).encode()).hexdigest()[:10]


def _feats(f):
    return f if isinstance(f, dict) else json.loads(f or "{}")


def _as_date(d):
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    return pd.Timestamp(str(d)[:10]).date()


# ============================================================================ 1. signal archive
def archive_rows(rows):
    """Replay rows → memory rows (one per signal)."""
    return [dict(key=r["key"], day=str(r["day"]), symbol=r["symbol"],
                 data={k: (str(v) if k == "ts" else v) for k, v in r.items() if k not in ("day",)})
            for r in rows]


def restore_rows(mem):
    """Memory rows → replay rows, with the same types the replay produces (ts naive IST, day a date)."""
    out = []
    for m in mem:
        d = dict(m["data"] or {})
        if not d.get("features"):
            continue
        d["ts"] = pd.Timestamp(d["ts"]).tz_localize(None) if d.get("ts") else None
        d["day"] = _as_date(m["day"])
        d["features"] = _feats(d["features"])
        d["side"] = int(d.get("side") or 0)
        out.append(d)
    return out


def merge_history(fresh, archived):
    """Fresh replay wins for every day it covers; archived days outside it are added back. Oldest first."""
    have = {str(r["day"]) for r in fresh}
    old = [r for r in archived if str(r["day"]) not in have]
    seen, out = set(), []
    for r in sorted(old + list(fresh), key=lambda x: (str(x["day"]), str(x.get("ts")), x["key"])):
        if r["key"] in seen:
            continue
        seen.add(r["key"])
        out.append(r)
    return out


def remember_replay(store, kind, eng, rows, today=None):
    """Archive finished days of a replay (today's partial session is left out) and return the merged history.
    Returns (rows, info) — info says how many sessions came from memory."""
    today = str(today or date.today())
    done = [r for r in rows if str(r["day"]) < today]
    wrote = store.memory_upsert(kind, eng, archive_rows(done)) if done else False
    archived = restore_rows(store.memory_list(kind, eng))
    merged = merge_history(rows, archived)
    fresh_days = {str(r["day"]) for r in rows}
    mem_days = sorted({str(r["day"]) for r in merged} - fresh_days)
    return merged, dict(fresh_days=len(fresh_days), memory_days=len(mem_days), wrote=bool(wrote),
                        first_day=min([str(r["day"]) for r in merged], default=None), n=len(merged),
                        n_memory=sum(str(r["day"]) in set(mem_days) for r in merged))


def remember_corr(store, corr_by_day, today=None):
    today = str(today or date.today())
    rows = [dict(key=d, day=d, data=v) for d, v in corr_by_day.items() if d < today]
    if rows:
        store.memory_upsert("corr", "all", rows)
    out = {m["key"]: m["data"] for m in store.memory_list("corr", "all")}
    out.update(corr_by_day)
    return out


def remember_day_stats(store, stats, today=None):
    """Per stock-day movement (for scoring the Sentinel) — archived raw, the 'normal day' ratio is recomputed
    over everything remembered."""
    today = str(today or date.today())
    cols = ["day", "symbol", "range_pct", "gap_pct", "move_pct"]
    if stats:
        rows = [dict(key=f"{s['day']}|{s['symbol']}", day=s["day"], symbol=s["symbol"],
                     data={c: (None if pd.isna(s.get(c)) else s.get(c)) for c in cols})
                for s in stats if str(s["day"]) < today]
        if rows:
            store.memory_upsert("dstat", "all", rows)
    mem = pd.DataFrame([m["data"] for m in store.memory_list("dstat", "all")])
    cur = pd.DataFrame(stats or [])
    if mem.empty:
        return stats
    allx = pd.concat([mem[cols], cur[cols]] if not cur.empty else [mem[cols]], ignore_index=True)
    allx = allx.drop_duplicates(["day", "symbol"], keep="last")
    allx["range_ratio"] = allx.range_pct / allx.groupby("symbol").range_pct.transform("median")
    return allx.sort_values(["day", "symbol"]).to_dict("records")


def tag_engine(rows, eng):
    for r in rows:
        r["features"] = dict(_feats(r["features"]), _eng=eng)
    return rows


# ============================================================================ 2. news backfill (point-in-time)
def backfill_news(rows, archive):
    """Set news = event/clear on history rows from the Sentinel archive, only when the archive entry was
    recorded before the signal fired (or is an exchange-calendar results day, which is known in advance).
    Returns the number of rows changed."""
    if archive is None or len(archive) == 0 or not rows:
        return 0
    lut = {}
    for a in archive.itertuples():
        lut[(str(a.day)[:10], a.symbol)] = (a.level, bool(a.scanned), a.updated, list(a.types or []))
    n = 0
    for r in rows:
        f = r["features"]
        if f.get("news") not in (None, "unknown"):
            continue
        hit = lut.get((str(r["day"]), r["symbol"]))
        if hit is None:
            continue
        level, scanned, upd, types = hit
        known_before = not scanned and "results_day" in types
        if scanned and upd is not None and pd.notna(upd) and r.get("ts") is not None:
            sig_utc = pd.Timestamp(r["ts"]).tz_localize("Asia/Kolkata") if pd.Timestamp(r["ts"]).tzinfo is None \
                else pd.Timestamp(r["ts"])
            known_before = upd <= sig_utc.tz_convert("UTC")
        if not known_before or level == "UNCHECKED":
            continue
        f["news"] = "event" if level in ("WATCH", "HIGH") else "clear"
        f["_news_src"] = "sentinel-archive"
        n += 1
    return n


# ============================================================================ 3. audit digests
def distill_events(ev):
    """One digest per day of the audit log: activity by agent, loss causes, risk verdicts, reviewer decisions."""
    if ev is None or ev.empty:
        return []
    e = ev.copy()
    e["day"] = e.ts.dt.strftime("%Y-%m-%d")
    out = []
    for d, g in e.groupby("day"):
        pm = g[g.kind == "post-mortem"]
        causes = {}
        for det, msg in zip(pm.detail, pm.message):
            det = det if isinstance(det, dict) else {}
            k = det.get("feature") or "normal-variance"
            c = causes.setdefault(k, dict(n=0, text=(det.get("primary") or msg).split(" — ")[0][:120]))
            c["n"] += 1
        rv = g[g.agent == "Reviewer"]
        out.append(dict(key=d, day=d, data=dict(
            events=int(len(g)), by_agent={k: int(v) for k, v in g.agent.value_counts().items()},
            causes=causes,
            risk={k: int(v) for k, v in g[g.agent == "Risk"].kind.value_counts().items()},
            sentinel={k: int(v) for k, v in g[(g.agent == "Sentinel") & g.kind.isin(["high", "watch"])].kind.value_counts().items()},
            trader={k: int(v) for k, v in g[g.agent == "Trader"].kind.value_counts().items()},
            reviewer={k: int(v) for k, v in rv.kind.value_counts().items()},
            lessons=sorted({m.split(" — ")[0] for m in g[(g.agent == "Coach") & (g.kind == "lesson")].message})[:12])))
    return out


def audit_trends(digests, today=None, window=30):
    """Recurring loss causes: last `window` days vs the `window` before, from the audit digests."""
    if not digests:
        return pd.DataFrame(columns=["cause", "text", "recent", "before", "change"]), {}
    today = pd.Timestamp(today or date.today())
    cut, cut2 = today - pd.Timedelta(days=window), today - pd.Timedelta(days=2 * window)
    rows, tot = {}, dict(days=len(digests), events=0, post_mortems=0, risk_blocks=0, reviews=0, first=None)
    for dg in digests:
        d, x = pd.Timestamp(dg["day"]), dg["data"] or {}
        tot["events"] += x.get("events", 0)
        tot["risk_blocks"] += (x.get("risk") or {}).get("block", 0)
        tot["reviews"] += (x.get("reviewer") or {}).get("trade-review", 0)
        tot["first"] = min(tot["first"] or dg["day"], dg["day"])
        for k, c in (x.get("causes") or {}).items():
            tot["post_mortems"] += c["n"]
            r = rows.setdefault(k, dict(cause=k, text=c["text"], recent=0, before=0))
            if d > cut:
                r["recent"] += c["n"]
            elif d > cut2:
                r["before"] += c["n"]
    t = pd.DataFrame(list(rows.values()), columns=["cause", "text", "recent", "before"])
    if t.empty:
        return pd.DataFrame(columns=["cause", "text", "recent", "before", "change"]), tot
    t["change"] = t.recent - t.before
    return t.sort_values(["recent", "change"], ascending=False).reset_index(drop=True), tot


# ============================================================================ 4. human reviews → scores and proposals
AGENT_WARN = {
    "Coach": lambda s: s.get("coach_verdict") == "Caution",
    "Risk": lambda s: s.get("risk_verdict") in ("REDUCE", "BLOCK"),
    "Sentinel": lambda s: s.get("sentinel") in ("WATCH", "HIGH"),
    "Pulse": lambda s: (s.get("size_mult") or 1.0) < 0.999,
}
AGENT_WARN_TEXT = {"Coach": "flagged Caution", "Risk": "reduced or blocked it", "Sentinel": "flagged news / an event",
                   "Pulse": "cut the size for market mood"}


def reviewed(L):
    """Paper-log rows (from paper_frame) that carry a human verdict of agree or disagree."""
    if L is None or L.empty or "verdict" not in L:
        return pd.DataFrame()
    return L[L.verdict.isin(["agree", "disagree"])].copy()


def hitl_scores(L):
    """How each agent lines up with the human reviewer: on trades you disagreed with, how often had the agent
    already warned (catch rate); on trades you agreed with, how often did it warn anyway (false alarms).
    Score = 50 + 50 × (catch − false alarm): 50 = no better than chance, 100 = always matches you."""
    R = reviewed(L)
    out = {}
    nd = int((R.verdict == "disagree").sum()) if len(R) else 0
    na = int((R.verdict == "agree").sum()) if len(R) else 0
    for name, warn in AGENT_WARN.items():
        if not len(R):
            out[name] = dict(score=None, n_agree=0, n_disagree=0, catch=np.nan, false_alarm=np.nan,
                             grade="No reviews yet")
            continue
        w = R.snapshot.map(lambda s: bool(warn(s or {})))
        catch = float(w[R.verdict == "disagree"].mean()) if nd else np.nan
        fa = float(w[R.verdict == "agree"].mean()) if na else np.nan
        if nd < MIN_HITL or na < MIN_HITL:
            out[name] = dict(score=None, n_agree=na, n_disagree=nd, catch=catch, false_alarm=fa,
                             grade=f"Needs {MIN_HITL}+ agree and {MIN_HITL}+ disagree reviews")
            continue
        j = catch - fa
        sc = int(round(50 + 50 * j))
        out[name] = dict(score=sc, n_agree=na, n_disagree=nd, catch=catch, false_alarm=fa,
                         grade=("Matches your judgement well" if j >= 0.4 else "Partly matches you" if j >= 0.15 else
                                "No better than chance" if j > -0.15 else "Often opposite to you"),
                         tone="good" if j >= 0.15 else "bad" if j <= -0.15 else "mid")
    return out


def tag_table(L):
    """Every tag reviewers used: how often, on agree vs disagree, and the trades' average result."""
    R = reviewed(L)
    rows = []
    for x in R.itertuples():
        for t in (x.human or {}).get("tags") or []:
            rows.append(dict(tag=t, verdict=x.verdict, r=x.r, setup=x.setup, key=x.key))
    if not rows:
        return pd.DataFrame(columns=["tag", "n", "disagree", "avg_r", "setups"])
    t = pd.DataFrame(rows)
    g = t.groupby("tag").agg(n=("key", "size"), disagree=("verdict", lambda v: int((v == "disagree").sum())),
                             avg_r=("r", "mean"), setups=("setup", lambda s: dict(s.value_counts())))
    return g.reset_index().sort_values("n", ascending=False).reset_index(drop=True)


def reviewer_notes(L, n=8):
    R = reviewed(L)
    out = []
    for x in R.sort_values("rec_time", ascending=False).itertuples():
        h = x.human or {}
        if (h.get("note") or "").strip():
            out.append(dict(when=x.rec_time, symbol=x.symbol, verdict=x.verdict, note=h["note"].strip(), by=h.get("by", "")))
        if len(out) >= n:
            break
    return out


def reviewer_proposals(L, cfg, sc=None, windows=(0, 15, 30, 60, 120)):
    """Proposals from what reviewers keep tagging — only when the trade data agrees. A human still approves."""
    T = tag_table(L)
    if T.empty:
        return []
    P = []

    def add(title, why, change):
        P.append(dict(id="Reviewer:" + json.dumps(change, sort_keys=True), agent="Reviewer", title=title, why=why,
                      change=change))
    tags = T.set_index("tag")

    def n(tag):
        return int(tags.loc[tag, "n"]) if tag in tags.index else 0
    closed = L[(L.status == "CLOSED") & L.r.notna()] if not L.empty else L
    cw = int(cfg.get("paper_window_min", 0) or 0)
    wt = (sc or {}).get("windows")
    # window too short — holding to the plan would have done better
    if n("Window too short") >= MIN_TAG_REVIEWS and cw and cw != windows[-1]:
        w = closed[(closed.reason == "window") & closed.hold_r.notna()]
        gain = float((w.hold_r - w.r).mean()) if len(w) >= 3 else np.nan
        if pd.notna(gain) and gain >= 0.1:
            nxt = next((x for x in windows if x > cw), 0)
            add(f"Lengthen the execution window to {nxt or 'hold to plan'}{' min' if nxt else ''}",
                f"Reviewers tagged 'Window too short' on {n('Window too short')} trades, and across {len(w)} trades closed "
                f"by the window, holding to the plan would have added {gain:+.2f}R each.", {"paper_window_min": nxt})
    # window too long — replay says a shorter window is no worse
    if n("Window too long") >= MIN_TAG_REVIEWS and wt is not None and len(wt):
        shorter = [x for x in windows if x and (cw == 0 or x < cw)]
        if shorter:
            cand = wt[wt.window.isin(shorter)]
            cur = wt[wt.window == cw].exp
            if len(cand) and len(cur):
                b = cand.sort_values("exp", ascending=False).iloc[0]
                if b.exp >= float(cur.iloc[0]) - 0.02:
                    add(f"Shorten the execution window to {int(b.window)} min",
                        f"Reviewers tagged 'Window too long' on {n('Window too long')} trades; on replayed recommendations "
                        f"a {int(b.window)}-minute window averaged {b.exp:+.2f}R vs {float(cur.iloc[0]):+.2f}R now.",
                        {"paper_window_min": int(b.window)})
    # target too far — few trades reach it
    if n("Target too far") >= MIN_TAG_REVIEWS and float(cfg.get("rr", 2.0)) > 1.0 and len(closed) >= 8:
        hit = float((closed.reason == "target").mean())
        if hit < 0.3:
            add(f"Bring the target in to {float(cfg['rr']) - 0.5:g}× risk",
                f"Reviewers tagged 'Target too far' on {n('Target too far')} trades and only {hit * 100:.0f}% of "
                f"{len(closed)} closed paper trades reached the target.", {"rr": float(cfg["rr"]) - 0.5})
    # news / event — block Sentinel HIGH
    if n("News / event") >= MIN_TAG_REVIEWS and not cfg.get("sentinel_block"):
        r_ = float(tags.loc["News / event", "avg_r"])
        if pd.notna(r_) and r_ < 0:
            add("Block signals the Sentinel rates HIGH-risk",
                f"Reviewers blamed news or an event on {n('News / event')} trades, which averaged {r_:+.2f}R.",
                {"sentinel_block": True})
    # market direction — size by mood
    if n("Market direction") >= MIN_TAG_REVIEWS and not cfg.get("pulse_sizing"):
        r_ = float(tags.loc["Market direction", "avg_r"])
        if pd.notna(r_) and r_ < 0:
            add("Turn on mood-based sizing",
                f"Reviewers blamed market direction on {n('Market direction')} trades, which averaged {r_:+.2f}R; "
                f"Pulse shrinks positions on weak market days.", {"pulse_sizing": True})
    # bad setup — concentrated on one setup that is losing
    if n("Bad setup") >= MIN_TAG_REVIEWS:
        st_ = tags.loc["Bad setup", "setups"] or {}
        top = max(st_, key=st_.get) if st_ else None
        if top and st_[top] / sum(st_.values()) >= 0.75 and top not in (cfg.get("paused_setups") or []):
            s_r = closed[closed.setup == top].r
            if len(s_r) >= 5 and s_r.mean() < 0:
                add(f"Pause the {top} setup",
                    f"{st_[top]} of {sum(st_.values())} 'Bad setup' tags were on {top}, and its {len(s_r)} closed paper "
                    f"trades averaged {s_r.mean():+.2f}R.", {"paused_setups": sorted(set(cfg.get("paused_setups") or []) | {top})})
    return P


def rejection_window_days(decision):
    """A proposal rejected once stays hidden 7 days; rejected again within 30 days, it stays hidden 30."""
    return 30 if (decision or {}).get("n_rej", 1) >= 2 else 7


def record_rejection(prev, by, note, now_iso):
    prev = prev or {}
    recent = prev.get("status") == "rejected" and prev.get("ts", "") >= (pd.Timestamp(now_iso) - pd.Timedelta(days=30)).isoformat()
    return dict(status="rejected", ts=now_iso, by=by, note=note, n_rej=(prev.get("n_rej", 1) + 1) if recent else 1)

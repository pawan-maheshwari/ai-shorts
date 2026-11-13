#!/usr/bin/env python3
"""
CHEETAH ENGINE — NSE stock selection toolkit
============================================
Two engines in one file:

SWING (enhanced CHEETAH, holding weeks to months)
  screen          Rank today's stocks: safety gates + quality/growth/value/momentum score
  backtest-swing  Walk-forward test: original CHEETAH vs enhanced vs score vs DNN,
                  plus an ablation that tests EACH added rule on its own and checks
                  whether it helps consistently across three separate periods.

INTRADAY (5-minute bars, flat by 3:15 pm)
  scan            Live scanner: gap, relative volume, opening range, VWAP, and any
                  triggered setup with entry / stop / target / quantity
  backtest-intraday  Tests Opening Range Breakout and VWAP Pullback on recent 5-min data,
                  after brokerage/STT/slippage, with daily trade and loss limits.

QUICK START (on your laptop, Python 3.9+)
  pip install pandas numpy scikit-learn yfinance
  python cheetah_engine.py screen --demo            # every command has --demo
  python cheetah_engine.py scan                     # live, during market hours
  python cheetah_engine.py backtest-intraday --days 59
  python cheetah_engine.py screen --fundamentals current.csv
  python cheetah_engine.py backtest-swing --fundamentals history.csv --ml

FUNDAMENTALS CSV COLUMNS
  Required: symbol, pe, roe, eps, profit_growth_3y, sales_growth_3y, debt_to_equity,
            promoter_holding, mcap_cr          (+ date for backtest-swing: the date
            results were ANNOUNCED, not the quarter end)
  Optional (each unlocks an enhanced rule; missing ones are skipped and reported):
            roce, roe_5y_avg, cfo, net_profit, pledge_pct, promoter_change, piotroski,
            qtr_profit_var, interest_coverage, peg, sector
  Common Screener.in export headers are auto-mapped (see ALIASES); rename others.
  For backtests, include delisted/merged companies or results will be too rosy.

DATA LIMITS: Yahoo gives 5-min bars for only the last ~60 days, so intraday backtests
are short. Treat fewer than ~200 trades as inconclusive.

Educational tool. Not investment advice.
"""
import argparse
import re
import sys
import warnings
from datetime import time as dtime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)

IST = "Asia/Kolkata"

WATCHLIST = [  # liquid NSE names; edit freely or pass --watchlist file.txt (one symbol per line)
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK",
    "LT", "ITC", "BHARTIARTL", "HINDUNILVR", "BAJFINANCE", "MARUTI", "M&M", "TATASTEEL",
    "SUNPHARMA", "ADANIENT", "ADANIPORTS", "NTPC", "POWERGRID", "ONGC", "COALINDIA",
    "HCLTECH", "WIPRO", "TECHM", "ULTRACEMCO", "TITAN", "ASIANPAINT", "JSWSTEEL",
    "HINDALCO", "BAJAJ-AUTO", "DLF", "BEL", "TRENT",
]

# =============================================================================
#  SHARED: data loading
# =============================================================================
ALIASES = {  # normalised header -> engine column
    "symbol": "symbol", "nsecode": "symbol", "nsesymbol": "symbol", "ticker": "symbol",
    "date": "date", "announcementdate": "date", "resultdate": "date",
    "pe": "pe", "priceearning": "pe", "pricetoearning": "pe",
    "roe": "roe", "returnonequity": "roe", "roe5yr": "roe_5y_avg", "averagereturnonequity5years": "roe_5y_avg",
    "roce": "roce", "returnoncapitalemployed": "roce",
    "eps": "eps", "epsrs": "eps", "epsttm": "eps",
    "profitgrowth3years": "profit_growth_3y", "profitvar3yrs": "profit_growth_3y", "profitgrowth3y": "profit_growth_3y",
    "salesgrowth3years": "sales_growth_3y", "salesvar3yrs": "sales_growth_3y", "salesgrowth3y": "sales_growth_3y",
    "debttoequity": "debt_to_equity", "debteq": "debt_to_equity", "de": "debt_to_equity",
    "promoterholding": "promoter_holding", "promhold": "promoter_holding",
    "marketcapitalization": "mcap_cr", "marcaprscr": "mcap_cr", "mcap": "mcap_cr", "mcapcr": "mcap_cr",
    "cashfromoperationslastyear": "cfo", "cfo": "cfo", "netprofitlastyear": "net_profit", "netprofit": "net_profit",
    "pledgedpercentage": "pledge_pct", "pledge": "pledge_pct", "pledgepct": "pledge_pct",
    "changeinpromoterholding": "promoter_change", "chginpromhold": "promoter_change",
    "piotroskiscore": "piotroski", "piotski": "piotroski",
    "qtrprofitvar": "qtr_profit_var", "interestcoverageratio": "interest_coverage", "intcoverage": "interest_coverage",
    "pegratio": "peg", "peg": "peg", "sector": "sector", "industry": "sector",
}


def read_fundamentals(path):
    df = pd.read_csv(path)
    ren = {}
    for c in df.columns:
        key = re.sub(r"[^a-z0-9]", "", c.lower())
        if key in ALIASES:
            ren[c] = ALIASES[key]
        elif c in ALIASES.values():
            ren[c] = c
    df = df.rename(columns=ren)
    df = df.loc[:, ~df.columns.duplicated()]
    if "symbol" not in df:
        sys.exit("Fundamentals file needs a 'symbol' column (NSE code).")
    df["symbol"] = df.symbol.astype(str).str.strip().str.upper()
    if "date" in df:
        df["date"] = pd.to_datetime(df.date)
    for c in df.columns:
        if c not in ("symbol", "date", "sector"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def yahoo_daily(symbols, start, bench=None):
    import yfinance as yf
    tickers = [s + ".NS" for s in symbols] + ([bench] if bench else [])
    raw = yf.download(tickers, start=start, auto_adjust=True, progress=False, threads=True)["Close"]
    if isinstance(raw, pd.Series):
        raw = raw.to_frame(tickers[0])
    raw.columns = [c.replace(".NS", "") for c in raw.columns]
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    return raw.sort_index()


# =============================================================================
#  SWING ENGINE
# =============================================================================
def daily_price_features(px):
    """Daily wide price frame -> dict of wide feature frames (each uses only past data)."""
    ema200 = px.ewm(span=200, adjust=False, min_periods=200).mean()
    sma50, sma200 = px.rolling(50).mean(), px.rolling(200).mean()
    return {
        "px_vs_ema200": px / ema200 - 1,
        "px_vs_sma50": px / sma50 - 1,
        "sma50_vs_sma200": sma50 / sma200 - 1,
        "mom_12_1": px.shift(21) / px.shift(252) - 1,   # 12-month momentum skipping last month
        "mom_6": px / px.shift(126) - 1,
        "vol_6m": px.pct_change().rolling(126).std() * np.sqrt(252),
    }


PRICE_COLS = ["px_vs_ema200", "px_vs_sma50", "sma50_vs_sma200", "mom_12_1", "mom_6", "vol_6m"]


def _is_fin(d):
    if "sector" not in d:
        return pd.Series(False, index=d.index)
    return d.sector.astype(str).str.contains("bank|financ|nbfc|insur|lend", case=False, na=False)


# name -> (columns needed, test). Financials are exempt from leverage rules.
RULES = {
    "pe_lt_30":            (["pe"], lambda d: (d.pe > 0) & (d.pe < 30)),
    "roe_gt_15":           (["roe"], lambda d: d.roe > 15),
    "eps_pos":             (["eps"], lambda d: d.eps > 0),
    "profit_g_gt_15":      (["profit_growth_3y"], lambda d: d.profit_growth_3y > 15),
    "sales_g_gt_15":       (["sales_growth_3y"], lambda d: d.sales_growth_3y > 15),
    "sales_g_gt_12":       (["sales_growth_3y"], lambda d: d.sales_growth_3y > 12),
    "de_lt_0_5":           (["debt_to_equity"], lambda d: (d.debt_to_equity < 0.5) | _is_fin(d)),
    "promoter_gt_50":      (["promoter_holding"], lambda d: d.promoter_holding > 50),
    "mcap_gt_500":         (["mcap_cr"], lambda d: d.mcap_cr > 500),
    "mcap_gt_1000":        (["mcap_cr"], lambda d: d.mcap_cr > 1000),
    "above_ema200":        (["px_vs_ema200"], lambda d: d.px_vs_ema200 > 0),
    # ---- enhancements ----
    "roce_gt_15":          (["roce"], lambda d: (d.roce > 15) | _is_fin(d)),
    "roe5y_gt_15":         (["roe_5y_avg"], lambda d: d.roe_5y_avg > 15),
    "cash_conversion":     (["cfo", "net_profit"], lambda d: (d.cfo >= 0.8 * d.net_profit) | _is_fin(d)),
    "pledge_lt_5":         (["pledge_pct"], lambda d: d.pledge_pct.fillna(0) < 5),
    "promoter_not_falling": (["promoter_change"], lambda d: d.promoter_change.fillna(0) >= 0),
    "piotroski_ge_7":      (["piotroski"], lambda d: d.piotroski >= 7),
    "qtr_accel":           (["qtr_profit_var", "profit_growth_3y"], lambda d: d.qtr_profit_var > d.profit_growth_3y),
    "int_cov_gt_5":        (["interest_coverage"], lambda d: (d.interest_coverage > 5) | _is_fin(d)),
    "peg_lt_1_5":          (["peg"], lambda d: (d.peg > 0) & (d.peg < 1.5)),
    "trend_50_200":        (["px_vs_sma50", "sma50_vs_sma200"], lambda d: (d.px_vs_sma50 > 0) & (d.sma50_vs_sma200 > 0)),
}

ORIGINAL = ["pe_lt_30", "roe_gt_15", "eps_pos", "profit_g_gt_15", "sales_g_gt_15",
            "de_lt_0_5", "promoter_gt_50", "mcap_gt_500", "above_ema200"]
ADDITIONS = ["roce_gt_15", "roe5y_gt_15", "cash_conversion", "pledge_lt_5", "promoter_not_falling",
             "piotroski_ge_7", "qtr_accel", "int_cov_gt_5", "peg_lt_1_5", "trend_50_200"]
ENHANCED = ["mcap_gt_1000", "eps_pos", "roe5y_gt_15", "roce_gt_15", "peg_lt_1_5", "profit_g_gt_15",
            "sales_g_gt_12", "qtr_accel", "cash_conversion", "de_lt_0_5", "int_cov_gt_5",
            "pledge_lt_5", "promoter_not_falling", "piotroski_ge_7", "trend_50_200"]
SAFETY = ["mcap_gt_1000", "eps_pos", "de_lt_0_5", "int_cov_gt_5", "cash_conversion",
          "pledge_lt_5", "above_ema200"]


def apply_rules(d, names):
    mask, skipped = pd.Series(True, index=d.index), []
    for n in names:
        cols, fn = RULES[n]
        if not all(c in d.columns for c in cols):
            skipped.append(n)
            continue
        mask &= fn(d).fillna(False).astype(bool)
    return mask, skipped


def failed_rules(d, names):
    out = pd.Series("", index=d.index)
    for n in names:
        cols, fn = RULES[n]
        if all(c in d.columns for c in cols):
            bad = ~fn(d).fillna(False).astype(bool)
            out[bad] = out[bad] + n + " "
    return out.str.strip()


SCORE_BLOCKS = {
    "quality": ["roce", "roe_5y_avg", "roe"],
    "growth": ["profit_growth_3y", "sales_growth_3y", "qtr_profit_var"],
    "value": ["earn_yield", "peg_inv"],
    "momentum": ["mom_12_1", "mom_6"],
}


def add_score(d, by="date"):
    d = d.copy()
    d["earn_yield"] = np.where(d.pe > 0, 1 / d.pe, 0)
    if "peg" in d:
        d["peg_inv"] = np.where(d.peg > 0, 1 / d.peg, 0)
    g = d.groupby(by) if by in d else None
    for block, cols in SCORE_BLOCKS.items():
        cols = [c for c in cols if c in d]
        if not cols:
            continue
        ranks = [(g[c].rank(pct=True) if g is not None else d[c].rank(pct=True)).fillna(0.5) for c in cols]
        d["s_" + block] = sum(ranks) / len(ranks)
    sc = [c for c in d if c.startswith("s_")]
    d["score"] = d[sc].mean(axis=1) * 100
    return d


# ---------------- live screen ----------------
def run_screen(fund, px):
    """Current fundamentals + daily prices -> scored table with pass flags, failed rules and swing entry/stop levels; also returns skipped rules."""
    feats = daily_price_features(px)
    last = pd.DataFrame({k: v.iloc[-1] for k, v in feats.items()})
    last.index.name = "symbol"
    d = fund.drop(columns=[c for c in PRICE_COLS if c in fund], errors="ignore").merge(
        last.reset_index(), on="symbol", how="left")
    d["price"] = d.symbol.map(px.iloc[-1])

    d["pass_original"], _ = apply_rules(d, ORIGINAL)
    d["pass_enhanced"], sk_e = apply_rules(d, ENHANCED)
    d["pass_safety"], sk_s = apply_rules(d, SAFETY)
    d = add_score(d, by="__none__")
    d["fails"] = failed_rules(d, ENHANCED)

    ema200 = d.price / (1 + d.px_vs_ema200)
    sma50 = d.price / (1 + d.px_vs_sma50)
    # swing levels: stop at the 200-day EMA but never wider than 15%; review level at 2x risk
    d["ema200"] = ema200
    d["stop"] = np.maximum(ema200, d.price * 0.85)
    d.loc[d.stop >= d.price, "stop"] = np.nan          # below the trend line: no long entry
    d["review_at"] = d.price + 2 * (d.price - d.stop)
    d["entry_zone"] = np.where(d.price > sma50 * 1.08, "Wait for pullback to ~" + sma50.round(1).astype(str),
                               "Near current price")
    return d.sort_values("score", ascending=False).reset_index(drop=True), sorted(set(sk_e + sk_s))


def cmd_screen(a):
    if a.demo:
        fund, px, _ = demo_swing(panel=False)
        print("DEMO MODE — synthetic companies.\n")
    else:
        if not a.fundamentals:
            sys.exit("Pass --fundamentals current.csv (or --demo).")
        fund = read_fundamentals(a.fundamentals)
        start = (pd.Timestamp.today() - pd.Timedelta(days=500)).strftime("%Y-%m-%d")
        px = yahoo_daily(sorted(fund.symbol.unique()), start)
    d, skipped = run_screen(fund, px)
    ranked = d[d.pass_safety].sort_values("score", ascending=False)
    cols = ["symbol", "price", "score", "s_quality", "s_growth", "s_value", "s_momentum",
            "pass_original", "pass_enhanced", "fails"]
    cols = [c for c in cols if c in ranked]
    show = ranked[cols].head(a.top).copy()
    for c in ["s_quality", "s_growth", "s_value", "s_momentum"]:
        if c in show:
            show[c] = (show[c] * 100).round(0)
    show["score"] = show.score.round(1)
    show["price"] = show.price.round(1)

    print(f"Universe: {len(d)}  |  pass original CHEETAH: {int(d.pass_original.sum())}  |  "
          f"pass enhanced: {int(d.pass_enhanced.sum())}  |  pass safety gates: {len(ranked)}")
    if skipped:
        print("Rules skipped (columns missing): " + ", ".join(skipped))
    print(f"\nTop {a.top} by composite score (safety gates passed). Block scores are 0-100 percentiles:\n")
    print(show.to_string(index=False))
    d.to_csv(a.out, index=False)
    print(f"\nFull ranked list saved to {a.out}")
    print("Next step: read the annual report, recent results and governance news for each name.")


# ---------------- swing backtest ----------------
def build_panel(fund, px, bench, horizon):
    stocks = px.drop(columns=[bench], errors="ignore")
    feats = daily_price_features(stocks)
    m = stocks.resample("ME").last()
    fe = pd.concat({k: v.resample("ME").last().stack() for k, v in feats.items()}, axis=1)
    fe.index.names = ["date", "symbol"]
    fe = fe.reset_index().sort_values("date")
    fund = fund.drop(columns=[c for c in PRICE_COLS if c in fund], errors="ignore").sort_values("date")
    panel = pd.merge_asof(fe, fund, on="date", by="symbol", direction="backward",
                          tolerance=pd.Timedelta(days=200))
    panel = panel.dropna(subset=["pe", "roe", "px_vs_ema200"])

    fwd = pd.concat([(m.shift(-1) / m - 1).stack().rename("fwd_1m"),
                     (m.shift(-horizon) / m - 1).stack().rename("fwd_h")], axis=1)
    fwd.index.names = ["date", "symbol"]
    panel = panel.merge(fwd.reset_index(), on=["date", "symbol"], how="left")
    bm = px[bench].resample("ME").last()
    bdf = pd.DataFrame({"date": bm.index, "bench_1m": (bm.shift(-1) / bm - 1).values,
                        "bench_h": (bm.shift(-horizon) / bm - 1).values})
    panel = panel.merge(bdf, on="date", how="left")
    panel["label"] = (panel.fwd_h > panel.bench_h).astype(int)
    return add_score(panel)


def portfolio(panel, picks, cost_bps):
    look = panel.set_index(["date", "symbol"]).fwd_1m
    rets, sizes, prev = {}, {}, set()
    for d, syms in sorted(picks.items()):
        sizes[d] = len(syms)
        if not syms:
            rets[d] = 0.0               # nothing qualifies -> sit in cash
            turnover = 1.0 if prev else 0.0
            rets[d] -= turnover * cost_bps / 1e4 / 2
            prev = set()
            continue
        r = look.reindex([(d, s) for s in syms]).dropna()
        turnover = len(set(syms) ^ prev) / max(len(syms), len(prev), 1)
        rets[d] = (r.mean() if len(r) else 0.0) - turnover * cost_bps / 1e4
        prev = set(syms)
    return pd.Series(rets).sort_index(), pd.Series(sizes).sort_index()


def perf(r, b):
    if len(r) < 2:
        return {}
    eq = (1 + r).cumprod()
    yrs = len(r) / 12
    return {"CAGR %": (eq.iloc[-1] ** (1 / yrs) - 1) * 100,
            "Max DD %": (eq / eq.cummax() - 1).min() * 100,
            "Sharpe": r.mean() / r.std() * np.sqrt(12) if r.std() > 0 else np.nan,
            "Beat idx months %": (r > b.reindex(r.index)).mean() * 100}


def ml_picks(panel, horizon, top, train_months, retrain_every):
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    cand = ["earn_yield", "roe", "eps", "profit_growth_3y", "sales_growth_3y", "debt_to_equity",
            "promoter_holding", "mcap_cr", "roce", "roe_5y_avg", "pledge_pct", "promoter_change",
            "piotroski", "qtr_profit_var", "interest_coverage", "peg"] + PRICE_COLS
    feats = [c for c in cand if c in panel and panel[c].notna().mean() > 0.5]

    def X(df):
        x = df[feats].copy()
        x["date"] = df.date.values
        return x.groupby("date").rank(pct=True).fillna(0.5).values

    months = sorted(panel.date.unique())
    picks, model = {}, None
    for i, d in enumerate(months):
        if i < train_months:
            continue
        tr = panel[(panel.date <= months[i - horizon]) & panel.fwd_h.notna()]
        te = panel[panel.date == d]
        if te.empty or tr.label.nunique() < 2:
            continue
        if model is None or (i - train_months) % retrain_every == 0:
            model = make_pipeline(StandardScaler(), MLPClassifier(
                hidden_layer_sizes=(64, 32, 16), alpha=1e-3, early_stopping=True,
                max_iter=300, random_state=7))
            model.fit(X(tr), tr.label.values)
        p = model.predict_proba(X(te))[:, 1]
        picks[d] = te.assign(p=p).nlargest(top, "p").symbol.tolist()
    return picks


def cmd_backtest_swing(a):
    if a.demo:
        fund, px, bench = demo_swing(panel=True)
        print("DEMO MODE — synthetic data; numbers only prove the pipeline runs.\n")
    else:
        if not a.fundamentals:
            sys.exit("Pass --fundamentals history.csv (or --demo).")
        fund = read_fundamentals(a.fundamentals)
        if "date" not in fund:
            sys.exit("History file needs a 'date' column (results announcement date).")
        bench = a.bench
        px = yahoo_daily(sorted(fund.symbol.unique()), a.start, bench)
    panel = build_panel(fund, px, bench, a.horizon)
    months = sorted(panel.date.unique())[a.train_months:]
    panel_bt = panel[panel.date.isin(months)]
    bench1m = panel.groupby("date").bench_1m.first()

    def rule_picks(names):
        out = {}
        for d, g in panel_bt.groupby("date"):
            m, _ = apply_rules(g, names)
            out[d] = g[m].symbol.tolist()
        return out

    def score_picks():
        out = {}
        for d, g in panel_bt.groupby("date"):
            m, _ = apply_rules(g, SAFETY)
            out[d] = g[m].nlargest(a.top, "score").symbol.tolist()
        return out

    _, skipped = apply_rules(panel_bt.head(1), ENHANCED + SAFETY)
    if skipped:
        print("Rules skipped (columns missing): " + ", ".join(sorted(set(skipped))) + "\n")

    variants = {"Original CHEETAH": rule_picks(ORIGINAL),
                "Enhanced (all rules)": rule_picks(ENHANCED),
                f"Score top {a.top}": score_picks()}
    if a.ml:
        print("Training DNN walk-forward (may take a few minutes)...")
        variants[f"DNN top {a.top}"] = ml_picks(panel, a.horizon, a.top, a.train_months, a.retrain_every)

    res = {k: portfolio(panel, v, a.cost_bps) for k, v in variants.items()}
    common = sorted(set.intersection(*[set(r.index) for r, _ in res.values()]) & set(bench1m.dropna().index))
    b = bench1m.loc[common]
    chunks = np.array_split(np.array(common), 3)

    def period_cagr(r):
        out = []
        for c in chunks:
            rr, bb = r.loc[list(c)], b.loc[list(c)]
            out.append(((1 + rr).prod() ** (12 / len(rr)) - (1 + bb).prod() ** (12 / len(bb))) * 100)
        return out

    rows = {}
    for k, (r, n) in res.items():
        r = r.loc[common]
        p = perf(r, b)
        pc = period_cagr(r)
        p.update({"Avg holdings": n.loc[common].mean(), "Empty months": int((n.loc[common] == 0).sum()),
                  "Excess P1": pc[0], "Excess P2": pc[1], "Excess P3": pc[2]})
        rows[k] = p
    rows["Benchmark"] = perf(b, b)
    print(f"=== Main comparison, {len(common)} months, after {a.cost_bps:.0f} bps round-trip costs ===")
    print("(Excess P1/P2/P3 = CAGR minus benchmark CAGR in three equal sub-periods)\n")
    print(pd.DataFrame(rows).T.round(2).to_string())

    # -------- ablation: does each added rule help on its own? --------
    base_r = res["Original CHEETAH"][0].loc[common]
    base_p, base_pc = perf(base_r, b), period_cagr(base_r)
    abl = {}
    for add in ADDITIONS:
        if add in skipped:
            continue
        r, n = portfolio(panel, rule_picks(ORIGINAL + [add]), a.cost_bps)
        r = r.loc[common]
        p, pc = perf(r, b), period_cagr(r)
        improved = sum(x > y for x, y in zip(pc, base_pc))
        better_sharpe = p["Sharpe"] > base_p["Sharpe"]
        if np.allclose(r.values, base_r.values):
            verdict = "NO EFFECT"
        else:
            verdict = "KEEP" if (improved >= 2 and better_sharpe) else ("MIXED" if improved >= 2 or better_sharpe else "DROP")
        abl["+ " + add] = {"ΔCAGR": p["CAGR %"] - base_p["CAGR %"], "ΔSharpe": p["Sharpe"] - base_p["Sharpe"],
                           "ΔMaxDD": p["Max DD %"] - base_p["Max DD %"], "Periods improved": f"{improved}/3",
                           "Avg holdings": n.loc[common].mean(), "Verdict": verdict}
    if abl:
        print("\n=== Ablation: original CHEETAH + one added rule at a time ===")
        print("KEEP = better Sharpe AND better in at least 2 of 3 periods.\n")
        print(pd.DataFrame(abl).T.to_string(float_format=lambda x: f"{x:.2f}"))
    eq = pd.DataFrame({k: (1 + r.loc[common]).cumprod() for k, (r, _) in res.items()})
    eq["Benchmark"] = (1 + b).cumprod()
    eq.to_csv(a.out)
    print(f"\nEquity curves saved to {a.out}")


# =============================================================================
#  INTRADAY ENGINE
# =============================================================================
def yahoo_intraday(symbols, days):
    import yfinance as yf
    out = {}
    raw = yf.download([s + ".NS" for s in symbols], period=f"{min(days, 59)}d", interval="5m",
                      auto_adjust=False, progress=False, group_by="ticker", threads=True)
    for s in symbols:
        try:
            df = raw[s + ".NS"] if isinstance(raw.columns, pd.MultiIndex) else raw
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            idx = df.index.tz_convert(IST) if df.index.tz else df.index.tz_localize("UTC").tz_convert(IST)
            df.index = idx.tz_localize(None)
            if len(df):
                out[s] = df
        except KeyError:
            pass
    missing = [s for s in symbols if s not in out]
    if missing and len(missing) < len(symbols):   # e.g. yfinance's own cache "database is locked" under threads
        for s in missing:
            try:
                df = yf.download(s + ".NS", period=f"{min(days, 59)}d", interval="5m", auto_adjust=False,
                                 progress=False, threads=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(df):
                    idx = df.index.tz_convert(IST) if df.index.tz else df.index.tz_localize("UTC").tz_convert(IST)
                    df.index = idx.tz_localize(None)
                    out[s] = df
            except Exception:
                pass
    return out


def read_intraday_csv(path):
    df = pd.read_csv(path, parse_dates=["datetime"])
    df.columns = [c.lower() for c in df.columns]
    out = {}
    for s, g in df.groupby("symbol"):
        g = g.set_index("datetime").sort_index()
        out[s] = g.rename(columns=str.capitalize)[["Open", "High", "Low", "Close", "Volume"]]
    return out


def session_days(df):
    df = df[(df.index.time >= dtime(9, 15)) & (df.index.time < dtime(15, 30))]
    return [(d, g) for d, g in df.groupby(df.index.date)]


def day_signals(g, prev_close, avg_or_vol, p):
    """Return list of candidate trades for one stock-day (first ORB and first VWAP pullback per side).
    When a Scout strategy has been approved (p.strategy), that strategy generates the signals instead."""
    if getattr(p, "race", None):              # first-to-fire: every armed specialist watches at once
        import research
        return research.race_signals(g, prev_close, avg_or_vol, p)
    if getattr(p, "strategy", None):
        import research
        return research.day_signals(g, prev_close, avg_or_vol, p)
    if len(g) < p.or_bars + 6 or prev_close is None:
        return [], {}
    o, h, l, c, v = (g[k].values for k in ["Open", "High", "Low", "Close", "Volume"])
    t = g.index
    tp = (h + l + c) / 3
    cumv = np.cumsum(v)
    vwap = np.where(cumv > 0, np.cumsum(tp * v) / np.maximum(cumv, 1), c)
    orh, orl = h[:p.or_bars].max(), l[:p.or_bars].min()
    or_vol = v[:p.or_bars].sum()
    gap = (o[0] / prev_close - 1) * 100
    relvol = or_vol / avg_or_vol if avg_or_vol and avg_or_vol > 0 else np.nan
    or_pct = (orh - orl) / o[0] * 100
    info = dict(gap_pct=gap, relvol=relvol, or_high=orh, or_low=orl, or_pct=or_pct,
                last=c[-1], vwap=vwap[-1], prev_close=prev_close, chg_pct=(c[-1] / prev_close - 1) * 100,
                last_bar=t[-1])

    ok_day = (np.isnan(relvol) or relvol >= p.min_relvol) and abs(gap) <= p.max_gap
    if not ok_day:
        return [], info
    sigs = []
    last_entry = dtime(*map(int, p.last_entry.split(":")))
    sides = [1, -1] if p.shorts else [1]

    # --- Opening Range Breakout ---
    if p.setup in ("orb", "both") and p.min_or_pct <= or_pct <= p.max_or_pct:
        for side in sides:
            for i in range(p.or_bars, len(g) - 1):
                if t[i + 1].time() > last_entry:
                    break
                vol_ok = v[i] > v[:i].mean()
                if side == 1 and c[i] > orh and c[i] > vwap[i] and vol_ok:
                    entry, stop = o[i + 1], orl
                elif side == -1 and c[i] < orl and c[i] < vwap[i] and vol_ok:
                    entry, stop = o[i + 1], orh
                else:
                    continue
                if (entry - stop) * side > 0:
                    sigs.append(dict(setup="ORB", side=side, i=i + 1, entry=entry, stop=stop))
                break

    # --- VWAP Pullback in a trend ---
    if p.setup in ("vwap", "both"):
        k = 6  # trend must hold for 30 min
        for side in sides:
            for i in range(max(k, p.or_bars + 3), len(g) - 1):
                if t[i + 1].time() > last_entry:
                    break
                if t[i].time() < dtime(10, 0):
                    continue
                if side == 1:
                    trend = np.all(c[i - k:i] > vwap[i - k:i]) and vwap[i] > vwap[i - k]
                    touch = l[i] <= vwap[i] * 1.001 and c[i] > vwap[i]
                    stop = min(l[i], vwap[i]) * 0.999
                else:
                    trend = np.all(c[i - k:i] < vwap[i - k:i]) and vwap[i] < vwap[i - k]
                    touch = h[i] >= vwap[i] * 0.999 and c[i] < vwap[i]
                    stop = max(h[i], vwap[i]) * 1.001
                if trend and touch:
                    entry = o[i + 1]
                    if (entry - stop) * side > 0 and abs(entry - stop) / entry * 100 <= p.max_or_pct:
                        sigs.append(dict(setup="VWAP", side=side, i=i + 1, entry=entry, stop=stop))
                    break
    ms = float(getattr(p, "min_stop_pct", 0) or 0)   # stops closer than this make costs a big share of the risk
    if ms:
        sigs = [x for x in sigs if abs(x["entry"] - x["stop"]) / x["entry"] * 100 >= ms]
    for s in sigs:
        risk = abs(s["entry"] - s["stop"])
        s["target"] = s["entry"] + s["side"] * p.rr * risk
        s["time"] = t[s["i"]]
    return sigs, info


def exit_trade(g, s, p):
    o, h, l, c = (g[k].values for k in ["Open", "High", "Low", "Close"])
    t = g.index
    exit_t = dtime(*map(int, p.exit_time.split(":")))
    side, stop, tgt = s["side"], s["stop"], s["target"]
    for j in range(s["i"], len(g)):
        if t[j].time() >= exit_t:
            return o[j], t[j], "time"
        if side == 1:
            if l[j] <= stop:                       # stop checked first = conservative
                return min(o[j], stop), t[j], "stop"
            if h[j] >= tgt:
                return max(o[j], tgt) if j > s["i"] else tgt, t[j], "target"
        else:
            if h[j] >= stop:
                return max(o[j], stop), t[j], "stop"
            if l[j] <= tgt:
                return min(o[j], tgt) if j > s["i"] else tgt, t[j], "target"
    return c[-1], t[-1], "close"


def size(entry, stop, p):
    risk = abs(entry - stop)
    qty = int(p.capital * p.risk_pct / 100 / risk) if risk > 0 else 0
    return max(0, min(qty, int(p.capital * p.leverage / entry)))


def prep_days(data, lookback=10):
    """Per symbol: list of (date, bars, prev_close, avg opening-range volume of prior days)."""
    out = {}
    for s, df in data.items():
        days = session_days(df)
        rows, or_vols, prev_close = [], [], None
        for d, g in days:
            avg = np.mean(or_vols[-lookback:]) if or_vols else np.nan
            rows.append((d, g, prev_close, avg))
            or_vols.append(g.Volume.values[:3].sum())
            prev_close = g.Close.values[-1]
        out[s] = rows
    return out


def run_intraday_backtest(data, a):
    """Returns (trade log, summary table by setup/side)."""
    prepped = prep_days(data)
    trades = []
    for s, rows in prepped.items():
        for d, g, pc, avg in rows[1:]:
            sigs, _ = day_signals(g, pc, avg, a)
            for sg in sigs:
                px_out, t_out, why = exit_trade(g, sg, a)
                trades.append(dict(date=d, symbol=s, setup=sg["setup"], strategy=sg.get("strategy", f"Classic {sg['setup']}"),
                                   side=sg["side"], entry_t=sg["time"],
                                   exit_t=t_out, entry=sg["entry"], stop=sg["stop"], target=sg["target"],
                                   exit=px_out, reason=why))
    if not trades:
        return pd.DataFrame(), pd.DataFrame()
    T = pd.DataFrame(trades).sort_values("entry_t")

    # daily portfolio limits: max trades/day, stop after N losses (only losses already closed count)
    keep = []
    for d, g in T.groupby("date"):
        taken = []
        for idx, r in g.iterrows():
            closed_losses = sum(1 for x in taken if T.loc[x, "exit_t"] <= r.entry_t and
                                (T.loc[x, "exit"] - T.loc[x, "entry"]) * T.loc[x, "side"] < 0)
            if len(taken) >= a.max_trades or closed_losses >= a.max_losses:
                continue
            taken.append(idx)
        keep += taken
    T = T.loc[keep].copy()

    T["qty"] = [size(e, s, a) for e, s in zip(T.entry, T.stop)]
    T = T[T.qty > 0]
    T["gross"] = T.qty * (T.exit - T.entry) * T.side
    T["costs"] = T.qty * T.entry * a.cost_pct / 100
    T["pnl"] = T.gross - T.costs
    T["R"] = T.pnl / (T.qty * (T.entry - T.stop).abs())

    def summ(g):
        eq = g.sort_values("exit_t").pnl.cumsum()
        wins, losses = g.pnl[g.pnl > 0].sum(), -g.pnl[g.pnl < 0].sum()
        return pd.Series({"Trades": len(g), "Win %": (g.pnl > 0).mean() * 100, "Avg R": g.R.mean(),
                          "Profit factor": wins / losses if losses > 0 else np.nan,
                          "Gross ₹": g.gross.sum(), "Costs ₹": g.costs.sum(), "Net ₹": g.pnl.sum(),
                          "Max DD ₹": (eq - eq.cummax()).min()})

    tab = T.groupby(["setup", "side"]).apply(summ)
    tab.loc[("ALL", ""), :] = summ(T)
    return T, tab


def cmd_backtest_intraday(a):
    if a.demo:
        data = demo_intraday()
        print("DEMO MODE — synthetic 5-min bars; numbers only prove the pipeline runs.\n")
    elif a.csv:
        data = read_intraday_csv(a.csv)
    else:
        data = yahoo_intraday(load_watchlist(a.watchlist), a.days)
    if not data:
        sys.exit("No intraday data loaded.")
    T, tab = run_intraday_backtest(data, a)
    if T.empty:
        print("No trades triggered. Loosen --min-relvol / --max-or-pct or add more symbols / days.")
        return
    T.to_csv(a.out, index=False)
    days = T.date.nunique()
    print(f"=== Intraday backtest: {len(data)} symbols, {days} trading days with trades, "
          f"capital ₹{a.capital:,.0f}, risk {a.risk_pct}%/trade, costs {a.cost_pct}% round trip ===\n")
    print(tab.round(2).to_string())
    print(f"\nExits: " + ", ".join(f"{k} {v}" for k, v in T.reason.value_counts().items()))
    net, n = T.pnl.sum(), len(T)
    verdict = ("Positive after costs" if net > 0 else "Negative after costs")
    print(f"\nVerdict: {verdict}. " + ("Under 200 trades — treat as INCONCLUSIVE, keep paper trading."
                                      if n < 200 else "Sample size is reasonable; still paper trade first."))
    print(f"Trade log saved to {a.out}")


def load_watchlist(path):
    if path:
        return [x.strip().upper() for x in open(path) if x.strip()]
    return WATCHLIST


def bars_needed(a):
    """5-minute bars a session must have before day_signals will read it."""
    return 15 if (getattr(a, "strategy", None) or getattr(a, "race", None)) else a.or_bars + 6


def session_progress(data, a):
    """(latest session date, most bars any symbol has in it, bars needed) — lets the caller tell
    'the session has only just opened' apart from 'there is no usable history'."""
    latest, got = None, 0
    for df in data.values():
        days = session_days(df)
        if not days:
            continue
        d, g = days[-1]
        if latest is None or d > latest:
            latest, got = d, len(g)
        elif d == latest:
            got = max(got, len(g))
    return latest, got, bars_needed(a)


def run_scan(data, a):
    """Latest session per symbol -> (scan table, {symbol: today's 5-min bars})."""
    rows, bars = [], {}
    for s, days in prep_days(data).items():
        if len(days) < 2:
            continue
        d, g, pc, avg = days[-1]
        sigs, info = day_signals(g, pc, avg, a)
        if not info:
            continue
        bars[s] = g
        r = dict(symbol=s, date=d, **{k: info[k] for k in ["gap_pct", "relvol", "or_high", "or_low", "last", "vwap",
                                                    "prev_close", "chg_pct", "last_bar"]})
        r["vs_vwap"] = "above" if info["last"] > info["vwap"] else "below"
        r.update(setup=None, strategy=None, side=0, signal_time=None, status=None, exit_price=np.nan,
                 entry=np.nan, stop=np.nan, target=np.nan, qty=np.nan)
        if sigs:
            sg = min(sigs, key=lambda x: x["time"])
            px_out, t_hit, why = exit_trade(g, sg, a)
            status = {"stop": f"STOPPED {t_hit:%H:%M}", "target": f"TARGET HIT {t_hit:%H:%M}",
                      "time": "CLOSED (time)"}.get(why, "OPEN")
            r.update(setup=sg["setup"], strategy=sg.get("strategy", f"Classic {sg['setup']}"), side=sg["side"],
                     signal_time=sg["time"], status=status,
                     exit_price=px_out if why != "close" else np.nan,
                     entry=sg["entry"], stop=sg["stop"], target=sg["target"], qty=size(sg["entry"], sg["stop"], a))
            r["signal"] = f'{sg["setup"]} {"LONG" if sg["side"] == 1 else "SHORT"} @ {sg["time"]:%H:%M} · {status}'
        else:
            r["signal"] = (f'watch: >{info["or_high"]:.2f} long / <{info["or_low"]:.2f} short'
                           if a.shorts else f'watch: >{info["or_high"]:.2f}')
        r["in_play"] = bool(not np.isnan(info["relvol"]) and info["relvol"] >= a.min_relvol
                            and abs(info["gap_pct"]) <= a.max_gap)
        rows.append(r)
    if not rows:
        return pd.DataFrame(), bars
    S = pd.DataFrame(rows).sort_values(["in_play", "relvol"], ascending=[False, False]).reset_index(drop=True)
    return S, bars


def cmd_scan(a):
    if a.demo:
        data = demo_intraday(days=15)
        print("DEMO MODE — synthetic bars, showing the latest synthetic session.\n")
    else:
        now = pd.Timestamp.now(tz=IST)
        if now.time() < dtime(9, 30) and now.weekday() < 5:
            print("Note: the opening range completes at 9:30 IST — run again after that for signals.\n")
        data = yahoo_intraday(load_watchlist(a.watchlist), 15)
    S, _ = run_scan(data, a)
    if S.empty:
        sys.exit("No data. Check symbols / market hours.")
    S.to_csv(a.out, index=False)
    show = S[["symbol", "gap_pct", "relvol", "last", "vs_vwap", "signal", "entry", "stop", "target", "qty", "in_play"]]
    print(f"Session {S.date.iloc[0]} — sorted by 'in play' then relative volume "
          f"(relvol = first-15-min volume vs prior 10-day average)\n")
    print(show.head(a.top).round(2).to_string(index=False))
    print(f"\nQty sized for ₹{a.capital:,.0f} capital, {a.risk_pct}% risk per trade. Exit all by {a.exit_time}.")
    print(f"Saved to {a.out}. Yahoo 5-min data can lag several minutes — confirm on your broker's chart.")


# =============================================================================
#  DEMO DATA (synthetic — for checking the pipeline only)
# =============================================================================
def demo_swing(panel=True, n=150):
    rng = np.random.default_rng(1)
    days = pd.bdate_range("2012-01-01", "2026-10-30")
    syms = [f"DEMO{i:03d}" for i in range(n)]
    q = rng.normal(0, 1, n)
    rets = rng.normal(0.0003 + 0.00015 * q, 0.019, (len(days), n))
    px = pd.DataFrame(100 * np.exp(np.cumsum(rets, 0)), index=days, columns=syms)
    px["^NSEI"] = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.011, len(days))))
    dates = (pd.date_range("2012-01-01", "2026-10-30", freq="QE") + pd.Timedelta(days=45)) if panel else [None]
    rows = []
    for d in dates:
        for i, s in enumerate(syms):
            z = q[i] + rng.normal(0, 0.6)
            np_ = max(1, 100 + 40 * z)
            r = dict(symbol=s, pe=max(4, 28 + rng.normal(0, 12)), roe=15 + 6 * z, eps=10 + 5 * z,
                     profit_growth_3y=13 + 8 * z, sales_growth_3y=12 + 6 * z,
                     debt_to_equity=max(0, 0.5 - 0.25 * z + rng.normal(0, .2)),
                     promoter_holding=float(np.clip(50 + 12 * rng.normal(), 0, 75)),
                     mcap_cr=float(np.exp(rng.normal(8.5, 1.4))), roce=16 + 6 * z, roe_5y_avg=15 + 5 * z,
                     net_profit=np_, cfo=np_ * (0.9 + 0.3 * rng.normal()), pledge_pct=max(0, rng.normal(2, 5)),
                     promoter_change=rng.normal(0, 1), piotroski=int(np.clip(round(6 + 1.5 * z), 0, 9)),
                     qtr_profit_var=13 + 15 * rng.normal() + 6 * z, interest_coverage=max(0.5, 8 + 4 * z),
                     peg=max(0.2, 1.6 - 0.4 * z + rng.normal(0, 0.4)),
                     sector=rng.choice(["Industrials", "IT", "Bank", "FMCG", "Pharma"]))
            if d is not None:
                r["date"] = d
            rows.append(r)
    return pd.DataFrame(rows), px, "^NSEI"


def demo_intraday(n=12, days=45):
    rng = np.random.default_rng(2)
    out = {}
    sessions = pd.bdate_range(end="2026-11-06", periods=days)
    for k in range(n):
        frames, price = [], 500 + 300 * rng.random()
        for d in sessions:
            idx = pd.date_range(d + pd.Timedelta(hours=9, minutes=15), periods=75, freq="5min")
            drift = rng.normal(0, 0.0012) * (1 if rng.random() < 0.35 else 0)   # some trend days
            price *= np.exp(rng.normal(0, 0.012))                                # overnight gap
            r = rng.normal(drift, 0.003, 75)
            close = price * np.exp(np.cumsum(r))
            openp = np.r_[price, close[:-1]]
            hi = np.maximum(openp, close) * (1 + np.abs(rng.normal(0, 0.0012, 75)))
            lo = np.minimum(openp, close) * (1 - np.abs(rng.normal(0, 0.0012, 75)))
            vol = (rng.lognormal(10, 0.5, 75) * np.r_[np.linspace(3, 1, 12), np.ones(51), np.linspace(1, 2, 12)]
                   * (1.8 if drift else 1))
            frames.append(pd.DataFrame(dict(Open=openp, High=hi, Low=lo, Close=close, Volume=vol), index=idx))
            price = close[-1]
        out[f"DEMO{k:02d}"] = pd.concat(frames)
    return out


# =============================================================================
#  CLI
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="CHEETAH engine — swing screener + intraday scanner (NSE)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("screen", help="rank today's stocks (enhanced CHEETAH)")
    s1.add_argument("--fundamentals")
    s1.add_argument("--top", type=int, default=25)
    s1.add_argument("--out", default="cheetah_ranked.csv")
    s1.add_argument("--demo", action="store_true")

    s2 = sub.add_parser("backtest-swing", help="walk-forward test of original vs enhanced vs score vs DNN")
    s2.add_argument("--fundamentals")
    s2.add_argument("--start", default="2013-01-01")
    s2.add_argument("--bench", default="^NSEI", help="Nifty 50 by default; a Nifty 500 series is a fairer benchmark")
    s2.add_argument("--horizon", type=int, default=3)
    s2.add_argument("--top", type=int, default=20)
    s2.add_argument("--train-months", type=int, default=36)
    s2.add_argument("--retrain-every", type=int, default=6)
    s2.add_argument("--cost-bps", type=float, default=40)
    s2.add_argument("--ml", action="store_true", help="also train the neural network")
    s2.add_argument("--out", default="swing_equity_curves.csv")
    s2.add_argument("--demo", action="store_true")

    for name, hlp in [("scan", "live intraday scanner"), ("backtest-intraday", "test ORB / VWAP setups on 5-min data")]:
        s = sub.add_parser(name, help=hlp)
        s.add_argument("--watchlist", help="text file, one NSE symbol per line")
        s.add_argument("--setup", choices=["orb", "vwap", "both"], default="both")
        s.add_argument("--shorts", action="store_true", help="also take short trades (intraday shorting allowed in cash)")
        s.add_argument("--or-bars", type=int, default=3, help="opening range = first N 5-min bars (3 = 15 min)")
        s.add_argument("--min-relvol", type=float, default=1.5)
        s.add_argument("--max-gap", type=float, default=4.0, help="skip stocks gapping more than this %%")
        s.add_argument("--min-or-pct", type=float, default=0.3)
        s.add_argument("--max-or-pct", type=float, default=2.0, help="skip if opening range wider than this %% (stop too wide)")
        s.add_argument("--rr", type=float, default=2.0, help="target as multiple of risk")
        s.add_argument("--last-entry", default="14:30")
        s.add_argument("--exit-time", default="15:15")
        s.add_argument("--capital", type=float, default=200000)
        s.add_argument("--risk-pct", type=float, default=0.5)
        s.add_argument("--leverage", type=float, default=5.0, help="max position value = capital x this")
        s.add_argument("--cost-pct", type=float, default=0.10, help="round-trip brokerage+STT+charges+slippage %%")
        s.add_argument("--max-trades", type=int, default=3)
        s.add_argument("--max-losses", type=int, default=2)
        s.add_argument("--top", type=int, default=20)
        s.add_argument("--demo", action="store_true")
        if name == "backtest-intraday":
            s.add_argument("--days", type=int, default=59)
            s.add_argument("--csv", help="your own 5-min data: symbol,datetime,open,high,low,close,volume")
            s.add_argument("--out", default="intraday_trades.csv")
        else:
            s.add_argument("--out", default="intraday_scan.csv")

    a = ap.parse_args()
    {"screen": cmd_screen, "backtest-swing": cmd_backtest_swing,
     "scan": cmd_scan, "backtest-intraday": cmd_backtest_intraday}[a.cmd](a)


if __name__ == "__main__":
    main()

"""
Stock Lab agents
================
  Sentinel  — news & events risk per stock (results dates, F&O ban, headline red flags)
  Pulse     — market mood from Nifty trend and India VIX; sets position-size multiplier and direction bias
  Narrator  — plain-English explanation of each signal
  Coach     — learns which conditions make or lose money (recency-weighted), predicts expected R for each
              signal, and diagnoses WHY losing trades lost
  Exit      — tests alternative exit policies (breakeven, trail, partial, time-stop) and runs the approved one
  Risk      — daily loss cap, trade and open-position caps, correlation check, optional edge-based sizing
  Trader    — paper-trades every recommendation inside its execution window and records the result
  Analyst   — reads the trade log, reviews each recommendation and finds what drove wins and losses

Every agent is measured walk-forward (trained only on earlier days) in scorecard(); agents only PROPOSE
changes (proposals()) and a human approves them — the approved settings live in a versioned config.

All agents work rule-based with no API key. If ANTHROPIC_API_KEY is set in Streamlit secrets,
Sentinel, Narrator and Coach also use Claude for richer reading of headlines and explanations.
The Coach never changes the engine's rules on its own: it re-scores confidence and flags signals,
and every lesson it draws is written to the audit log for you to review.
"""
import json
import math
import re
from datetime import time as dtime

import numpy as np
import pandas as pd
import requests

import cheetah_engine as ce
import models as ml
import research as rs

MODEL = "claude-haiku-5-5"


# ============================================================================ LLM helper
def ask_claude(prompt, api_key, max_tokens=300, system=None):
    if not api_key:
        return None
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=25,
                          headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json={"model": MODEL, "max_tokens": max_tokens,
                                "system": system or "You are a careful equity-market analyst for Indian stocks. "
                                                    "Be concise and factual. Never promise returns.",
                                "messages": [{"role": "user", "content": prompt}]})
        if r.status_code != 200:
            return None
        return "".join(b.get("text", "") for b in r.json().get("content", [])).strip()
    except Exception:
        return None


# ============================================================================ PULSE — market mood
def fetch_index(demo, stock_data=None):
    """Returns dict(intraday=5-min Nifty bars (IST naive), daily=Nifty daily close, vix=VIX daily close)."""
    if demo:
        frames = [df.Close / df.Close.iloc[0] for df in stock_data.values()]
        c = pd.concat(frames, axis=1).mean(axis=1) * 24000
        bars = pd.DataFrame({"Open": c.shift(1).fillna(c), "High": c * 1.0004, "Low": c * 0.9996, "Close": c,
                             "Volume": 1.0})
        daily = c.groupby(c.index.date).last()
        daily.index = pd.to_datetime(daily.index)
        vix = pd.Series(14.0 + np.sin(np.arange(len(daily)) / 3), index=daily.index)
        return dict(intraday=bars, daily=daily, vix=vix)
    import yfinance as yf
    out = {}
    try:
        b = yf.download("^NSEI", period="59d", interval="5m", progress=False, auto_adjust=False)
        if isinstance(b.columns, pd.MultiIndex):
            b.columns = b.columns.get_level_values(0)
        idx = b.index.tz_convert(ce.IST) if b.index.tz else b.index.tz_localize("UTC").tz_convert(ce.IST)
        b.index = idx.tz_localize(None)
        out["intraday"] = b[["Open", "High", "Low", "Close", "Volume"]].dropna()
    except Exception:
        out["intraday"] = pd.DataFrame()
    try:
        d = yf.download(["^NSEI", "^INDIAVIX"], period="6mo", interval="1d", progress=False, auto_adjust=True)["Close"]
        d.index = pd.to_datetime(d.index).tz_localize(None)
        out["daily"], out["vix"] = d["^NSEI"].dropna(), d["^INDIAVIX"].dropna()
    except Exception:
        out["daily"], out["vix"] = pd.Series(dtype=float), pd.Series(dtype=float)
    return out


def index_day(idx, day):
    b = idx.get("intraday")
    if b is None or b.empty:
        return pd.DataFrame()
    b = b[b.index.date == day]
    return b[(b.index.time >= dtime(9, 15)) & (b.index.time < dtime(15, 30))]


DEFAULT_MULTS = {"Risk-on": 1.0, "Mildly positive": 1.0, "Choppy": 0.75, "Mildly negative": 0.75,
                 "Risk-off": 0.75, "Volatile": 0.5}


def pulse(idx, day=None, asof=None, mults=None):
    """Market regime for a session. With `asof` (a timestamp) it only uses data available at that moment:
    index bars up to `asof`, and daily trend / VIX from previous sessions — no look-ahead."""
    mults = mults or DEFAULT_MULTS
    b = idx.get("intraday", pd.DataFrame())
    daily, vix = idx.get("daily", pd.Series(dtype=float)), idx.get("vix", pd.Series(dtype=float))
    if day is None and not b.empty:
        day = b.index[-1].date()
    g = index_day(idx, day) if day else pd.DataFrame()
    if asof is not None and not g.empty:
        g = g[g.index <= asof]
    keep = (lambda s: s[s.index.date < day]) if (asof is not None and day) else \
           (lambda s: s[s.index.date <= day] if day else s)
    daily_h, vix_h = keep(daily), keep(vix)
    reasons, bias, score = [], 0, 0
    nifty_chg = np.nan
    if not g.empty:
        prev = daily[daily.index.date < day]
        base = prev.iloc[-1] if len(prev) else g.Open.iloc[0]
        nifty_chg = (g.Close.iloc[-1] / base - 1) * 100
        tp = (g.High + g.Low + g.Close) / 3
        vw = tp.cumsum() / np.arange(1, len(tp) + 1)
        above = g.Close.iloc[-1] > vw.iloc[-1]
        if nifty_chg > 0.25 and above:
            score += 1; reasons.append(f"Nifty up {nifty_chg:.2f}% and above its intraday average")
        elif nifty_chg < -0.25 and not above:
            score -= 1; reasons.append(f"Nifty down {abs(nifty_chg):.2f}% and below its intraday average")
        else:
            reasons.append(f"Nifty flat-ish ({nifty_chg:+.2f}%) — no clear intraday direction")
    trend = "unknown"
    if len(daily_h) >= 50:
        s20, s50, last = daily_h.rolling(20).mean().iloc[-1], daily_h.rolling(50).mean().iloc[-1], daily_h.iloc[-1]
        if last > s20 > s50:
            trend = "up"; score += 1; reasons.append("Daily trend up (above 20 and 50-day averages)")
        elif last < s20 < s50:
            trend = "down"; score -= 1; reasons.append("Daily trend down (below 20 and 50-day averages)")
        else:
            trend = "sideways"; reasons.append("Daily trend sideways")
    v = vchg = np.nan
    if len(vix_h) >= 2:
        v, vchg = vix_h.iloc[-1], (vix_h.iloc[-1] / vix_h.iloc[-2] - 1) * 100
        reasons.append(f"India VIX {v:.1f} ({vchg:+.1f}% vs previous day)")
    volatile = (not np.isnan(v) and v >= 20) or (not np.isnan(vchg) and vchg >= 8)
    if volatile:
        regime = "Volatile"; reasons.append("Fear gauge elevated")
    elif score >= 2:
        regime, bias = "Risk-on", 1
    elif score <= -2:
        regime, bias = "Risk-off", -1
    elif score == 1:
        regime, bias = "Mildly positive", 1
    elif score == -1:
        regime, bias = "Mildly negative", -1
    else:
        regime = "Choppy"
    return dict(regime=regime, mult=float(mults.get(regime, 1.0)), bias=bias, reasons=reasons, nifty_chg=nifty_chg,
                vix=v, vix_chg=vchg, trend=trend, day=str(day) if day else None)


# ============================================================================ SENTINEL — news & events
# Workflow: gather from several sources (each reports whether it could be checked) → read every item
# (is it about this company, is it fresh, what kind of event, how material) → combine with hard facts
# (results calendar, F&O ban) into a level: HIGH / WATCH / CLEAR, or UNCHECKED when nothing could be checked.

NAMES = {  # company names for the default watchlist (used for news search and to confirm a headline is about the stock)
    "RELIANCE": "Reliance Industries", "HDFCBANK": "HDFC Bank", "ICICIBANK": "ICICI Bank", "INFY": "Infosys",
    "TCS": "Tata Consultancy Services", "SBIN": "State Bank of India", "AXISBANK": "Axis Bank",
    "KOTAKBANK": "Kotak Mahindra Bank", "LT": "Larsen & Toubro", "ITC": "ITC", "BHARTIARTL": "Bharti Airtel",
    "HINDUNILVR": "Hindustan Unilever", "BAJFINANCE": "Bajaj Finance", "MARUTI": "Maruti Suzuki",
    "M&M": "Mahindra & Mahindra", "TATASTEEL": "Tata Steel", "SUNPHARMA": "Sun Pharma", "ADANIENT": "Adani Enterprises",
    "ADANIPORTS": "Adani Ports", "NTPC": "NTPC", "POWERGRID": "Power Grid", "ONGC": "ONGC", "COALINDIA": "Coal India",
    "HCLTECH": "HCL Technologies", "WIPRO": "Wipro", "TECHM": "Tech Mahindra", "ULTRACEMCO": "UltraTech Cement",
    "TITAN": "Titan", "ASIANPAINT": "Asian Paints", "JSWSTEEL": "JSW Steel", "HINDALCO": "Hindalco",
    "BAJAJ-AUTO": "Bajaj Auto", "DLF": "DLF", "BEL": "Bharat Electronics", "TRENT": "Trent",
}
EXTRA_ALIASES = {"TCS": ["tcs"], "SBIN": ["sbi"], "LT": ["l&t"], "M&M": ["m&m", "mahindra"], "HINDUNILVR": ["hul"],
                 "RELIANCE": ["ril"], "BEL": ["bel"], "INFY": ["infosys"], "HCLTECH": ["hcl tech", "hcltech"]}
_SUFFIX = r"\b(limited|ltd\.?|industries|corporation|corp\.?|company|co\.?|india)\b"


def company_name(symbol, hint=None):
    return hint or NAMES.get(symbol.upper()) or symbol.upper()


def aliases(symbol, name):
    core = re.sub(r"\s+", " ", re.sub(_SUFFIX, "", name.lower())).strip(" .,&")
    out = {symbol.lower(), name.lower(), core}
    out.update(EXTRA_ALIASES.get(symbol.upper(), []))
    return {a for a in out if len(a) >= 3}


# ---------------- sources: every fetcher returns (status, items); status is "ok" or "unavailable"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9"}


class NSE:
    """Small client for nseindia.com's public JSON endpoints. NSE often refuses cloud servers; every call
    reports whether it worked so the Sentinel never claims a check it could not make."""
    BASE = "https://www.nseindia.com"

    def __init__(self, timeout=7):
        self.s = requests.Session()
        self.s.headers.update(dict(_UA, Accept="application/json, text/plain, */*", Referer=self.BASE + "/"))
        self.timeout, self.warm, self.dead, self.fails = timeout, False, False, 0

    def get(self, path, params=None):
        if self.dead:
            return None
        try:
            if not self.warm:
                self.s.get(self.BASE + "/", timeout=self.timeout)
                self.warm = True
            r = self.s.get(self.BASE + path, params=params, timeout=self.timeout)
            if r.status_code == 200:
                self.fails = 0
                return r.json()
            self.fails += 3 if r.status_code in (401, 403) else 1
        except Exception:
            self.fails += 1
        if self.fails >= 3:               # blocked or timing out from this server — stop trying for this scan
            self.dead = True
        return None

    @staticmethod
    def _ts(txt, fmt="%d-%b-%Y %H:%M:%S"):
        try:
            return pd.Timestamp(pd.to_datetime(txt, format=fmt)).tz_localize(ce.IST).tz_convert("UTC")
        except Exception:
            return None

    def announcements(self, symbol, days=3):
        to, frm = pd.Timestamp.now(tz=ce.IST), pd.Timestamp.now(tz=ce.IST) - pd.Timedelta(days=days)
        j = self.get("/api/corporate-announcements", {"index": "equities", "symbol": symbol,
                                                     "from_date": frm.strftime("%d-%m-%Y"), "to_date": to.strftime("%d-%m-%Y")})
        if j is None:
            return "unavailable", [], None
        rows = j if isinstance(j, list) else j.get("data", []) if isinstance(j, dict) else []
        cut = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
        items, name = [], None
        for x in rows[:200]:
            when = self._ts(x.get("an_dt") or x.get("exchdisstime"))
            name = name or x.get("sm_name")
            if when is None or when < cut:
                continue
            items.append(dict(title=((x.get("desc") or "") + ": " + (x.get("attchmntText") or "")).strip(": ")[:300],
                              when=when, publisher="NSE filing", link=x.get("attchmntFile") or "", kind="filing",
                              category=x.get("desc") or ""))
        return "ok", items, name

    def board_meetings(self, symbol, back=95, fwd=12):
        now = pd.Timestamp.now(tz=ce.IST)
        j = self.get("/api/corporate-board-meetings", {"index": "equities", "symbol": symbol,
                                                      "from_date": (now - pd.Timedelta(days=back)).strftime("%d-%m-%Y"),
                                                      "to_date": (now + pd.Timedelta(days=fwd)).strftime("%d-%m-%Y")})
        if j is None:
            return "unavailable", []
        rows = j if isinstance(j, list) else j.get("data", []) if isinstance(j, dict) else []
        out = []
        for x in rows[:60]:
            try:
                d = pd.to_datetime(x.get("bm_date") or x.get("meetingdate"), dayfirst=True).date()
            except Exception:
                continue
            purpose = " ".join(str(x.get(k) or "") for k in ("bm_purpose", "bm_desc", "purpose"))
            out.append(dict(date=d, purpose=purpose[:200], results=bool(re.search(r"financial result|quarterly result|results", purpose, re.I))))
        return "ok", out

    def actions(self, symbol):
        j = self.get("/api/corporates-corporateActions", {"index": "equities", "symbol": symbol})
        if j is None:
            return "unavailable", []
        rows = j if isinstance(j, list) else j.get("data", []) if isinstance(j, dict) else []
        out = []
        for x in rows[:40]:
            try:
                d = pd.to_datetime(x.get("exDate") or x.get("ex_date"), dayfirst=True).date()
            except Exception:
                continue
            out.append(dict(date=d, subject=str(x.get("subject") or x.get("purpose") or "")[:120]))
        return "ok", out


def fo_ban_list():
    """(status, set of symbols in the F&O ban today)."""
    try:
        r = requests.get("https://nsearchives.nseindia.com/content/fo/fo_secban.csv", timeout=8, headers=_UA)
        if r.status_code == 200 and "<html" not in r.text[:200].lower():
            return "ok", {ln.split(",")[-1].strip().upper() for ln in r.text.splitlines()[1:] if ln.strip()}
    except Exception:
        pass
    return "unavailable", set()


def google_news(name, days=2, limit=25):
    """Google News RSS search for the company name. (status, items)."""
    import urllib.parse
    import xml.etree.ElementTree as ET
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(f'"{name}" when:{days}d')
           + "&hl=en-IN&gl=IN&ceid=IN:en")
    try:
        r = requests.get(url, timeout=8, headers=_UA)
        if r.status_code != 200:
            return "unavailable", []
        root = ET.fromstring(r.content)
    except Exception:
        return "unavailable", []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            when = pd.to_datetime(it.findtext("pubDate"), utc=True)
        except Exception:
            when = None
        if title:
            items.append(dict(title=title, when=when, publisher=src or "Google News", link=it.findtext("link") or "",
                              kind="news", category=""))
        if len(items) >= limit:
            break
    return "ok", items


def yahoo_news(symbol):
    try:
        import yfinance as yf
        raw = yf.Ticker(symbol + ".NS").news
    except Exception:
        return "unavailable", []
    out = []
    for n in raw or []:
        c = n.get("content", n)
        title = c.get("title") or ""
        when = c.get("pubDate") or c.get("displayTime") or n.get("providerPublishTime")
        try:
            when = pd.to_datetime(when, unit="s", utc=True) if isinstance(when, (int, float)) else pd.to_datetime(when, utc=True)
        except Exception:
            when = None
        pub = (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else n.get("publisher")
        link = ((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else None) or n.get("link")
        if title:
            out.append(dict(title=title, when=when, publisher=pub or "Yahoo", link=link or "", kind="news", category=""))
    return "ok", out[:10]


# ---------------- reading an item: noise → about this company → fresh → event type → materiality, direction
NOISE = re.compile(r"share price|stock price|live nse|nse/bse live|quote of the day|\bbuy, sell or hold\b|stocks? to (buy|watch)|"
                   r"\btop \d+ (stocks|picks)|picks \d+ stocks|\bstocks in (focus|news)\b|market (live|highlights|updates)|"
                   r"nifty[, ] sensex|sensex[, ] nifty|\bhoroscope\b|\bipo gmp\b", re.I)

EVENT_TYPES = [   # (type, pattern, materiality 0-3, direction −1/0/+1) — first match wins, most serious first
    ("regulatory", r"\bsebi\b.{0,40}\b(order|bans?|barred|notice|penalt|probe)|show[- ]cause|\b(cbi|ed|sfio|income[- ]tax|i-t)\b.{0,25}\b(raid|search|probe|summon|notice)|"
                   r"\braids?\b|\bfraud\b|\bscam\b|\bpenalt(y|ies)\b.{0,30}\b(crore|lakh|imposed|slapped)|tax demand|\bnclt\b|\binsolvency\b|money[- ]laundering", 3, -1),
    ("credit", r"\bdefault(s|ed)? on\b|\b(loan|payment|bond|debt) default|\bpledg(e|ed|es)\b.{0,30}\bshares|\brating\b.{0,25}\bdowngrad|downgrad.{0,25}\b(rating|outlook|debt)", 3, -1),
    ("results_bad", r"\b(profit|revenue|earnings|margins?)\b.{0,30}\b(falls?|drops?|declines?|slumps?|plunges?|misses|miss)\b|\bmisses estimates|\bnet loss\b|posts? (a )?loss", 2, -1),
    ("results_good", r"\b(profit|revenue|earnings)\b.{0,30}\b(rises?|jumps?|surges?|grows?|beats?|up \d)|\bbeats? (street|estimates)|record (profit|revenue)", 2, 1),
    ("results", r"\b(q[1-4]|quarterly|financial|annual)\s?(fy\s?\d\d(\d\d)?\s)?(results?|earnings|numbers)\b|\bq[1-4]\s?fy\s?\d\d|results (today|tomorrow|on|date)|"
                r"financial result updates|outcome of board meeting", 2, 0),
    ("management", r"\b(ceo|cfo|coo|md|chairman|chairperson|managing director|auditors?|company secretary|kmp)\b.{0,40}\b(resign|quits?|steps? down|exits?|sacked|removed)|"
                   r"\bresign(s|ed|ation)\b.{0,30}\b(ceo|cfo|md|chairman|director|auditor)|change in (director|auditor|management)|cessation", 2, -1),
    ("operations", r"\bfire (at|in|breaks)\b|\b(plant|factory|unit|mine|refinery)\b.{0,25}\b(shut|shutdown|halt|closure|blast|accident|fire)|"
                   r"\b(workers?|union|employees?|staff)\b.{0,15}\bstrike\b|\bon strike\b|\brecalls?\b.{0,20}\b(vehicles|units|cars|batch|product)|\bdata breach\b|\bcyber ?attack", 2, -1),
    ("downgrade", r"\bdowngrad(e|es|ed)\b|\bcuts? (the )?(target|rating)|target (price )?cut|\bsell rating\b", 2, -1),
    ("stake_sale", r"\bblock deals?\b|\bbulk deals?\b|\bstake sale\b|\bofs\b|offer for sale|promoters? (sell|sells|sold|offload|pare)|sells? .{0,20}stake", 2, -1),
    ("upgrade", r"\bupgrad(e|es|ed)\b|\braises? (the )?target|target (price )?(raised|hike)|\bbuy rating\b|initiates? coverage", 1, 1),
    ("orders", r"\b(wins?|bags?|secures?|receives?)\b.{0,40}\b(order|contract|deal|loi)\b|\border (win|inflow|worth)|\bapproval\b.{0,20}\b(usfda|fda|regulator|cabinet|board)", 1, 1),
    ("corporate_action", r"\b(bonus (issue|shares)|stock split|sub-division|buyback|rights issue|record date|demerger|merger|amalgamation|acquisition|acquires?|to acquire)\b", 1, 0),
    ("dividend", r"\bdividend\b", 1, 0),
    ("board_meeting", r"\bboard meeting\b|\bboard to (meet|consider)", 1, 0),
    ("investor_meet", r"analysts?/institutional investor|investor meet|con\. ?call|earnings call|investor presentation", 0, 0),
]
_EVENT_RX = [(t, re.compile(p, re.I), m, d) for t, p, m, d in EVENT_TYPES]

# Exchange filings are classified by their official category first — most are routine paperwork, and their free
# text often mentions words like "financial results" without being a results event (e.g. a call transcript).
FILING_ROUTINE = re.compile(
    r"loss of share certificate|duplicate share|newspaper publication|trading window|esop|esos|esps|allotment of|"
    r"shareholders? meeting|annual general meeting|book closure|investor presentation|analysts?/institutional|"
    r"monitoring agency|related party|insider trading|takeover|substantial acq|appointment|postal ballot|"
    r"certificate under|compliance|record of proceedings|change in registered office|statement of (deviation|investor)", re.I)
FILING_CATS = [   # (category pattern, type, materiality, direction)
    (r"action\(?s?\)? taken or orders passed|order passed|penalt", "regulatory", 3, -1),
    (r"financial result|outcome of board meeting", "results", 2, 0),
    (r"news (verification|clarification)|clarification of news|spurt in (price|volume)|price movement", "clarification", 2, 0),
    (r"upsi|material event", "material_event", 1, 0),
    (r"cessation|resignation", "management", 2, -1),
    (r"change in director|change in management|change in auditor", "management", 1, 0),
    (r"acquisition|scheme of arrangement|restructuring|sale or disposal|amalgamation|demerger", "corporate_action", 1, 0),
    (r"buyback|record date|bonus|split|rights issue", "corporate_action", 1, 0),
    (r"dividend", "dividend", 1, 0),
    (r"board meeting", "board_meeting", 1, 0),
]
_FILING_RX = [(re.compile(p, re.I), t, m, d) for p, t, m, d in FILING_CATS]
SEVERE_IN_TEXT = {"regulatory", "credit", "operations", "management", "stake_sale", "orders", "downgrade"}


def read_filing(category, text):
    """(type, materiality, direction) for an exchange filing."""
    cat = category or ""
    if re.search(r"credit rating", cat, re.I):
        return ("credit", 3, -1) if re.search(r"downgrad|default|negative outlook|rating watch with negative", text, re.I) else ("routine", 0, 0)
    hit = None
    for rx, t, m, d in _FILING_RX:
        if rx.search(cat):
            hit = (t, m, d)
            break
    if hit and hit[0] != "material_event":
        return hit
    if hit is None and FILING_ROUTINE.search(cat):
        return "routine", 0, 0
    # generic categories (Updates, Press Release, material event…): trust the text only for serious event types,
    # and at most WATCH level — the company's own wording is too ambiguous for rules to call it HIGH
    for t, rx, m, d in _EVENT_RX:
        if t in SEVERE_IN_TEXT and rx.search(text):
            return t, min(m, 2), d
    return hit or ("other", 0, 0)
TYPE_TEXT = {"regulatory": "Regulatory or legal action", "credit": "Credit or pledge trouble", "results_bad": "Weak results",
             "results_good": "Strong results", "results": "Results event", "management": "Management change",
             "operations": "Operational disruption", "downgrade": "Broker downgrade", "stake_sale": "Stake sale or large deal",
             "upgrade": "Broker upgrade", "orders": "Order win or approval", "corporate_action": "Corporate action",
             "dividend": "Dividend", "board_meeting": "Board meeting", "investor_meet": "Investor meeting",
             "results_day": "Results day (exchange calendar)", "fo_ban": "F&O ban", "other": "Other news",
             "clarification": "News clarification with the exchange", "material_event": "Material event disclosed",
             "routine": "Routine filing"}


def read_item(item, alias_set, now=None, fresh_hours=48):
    """Rule-based reading of one headline or filing."""
    now = now or pd.Timestamp.now(tz="UTC")
    title = item["title"]
    low = title.lower()
    filing = item.get("kind") == "filing"
    noise = (not filing) and bool(NOISE.search(low))
    about = filing or any(a in low for a in alias_set)
    age_h = (now - item["when"]).total_seconds() / 3600 if item.get("when") is not None else None
    old_year = re.search(r"\b(19|20)\d\d\b", title)
    stale = (age_h is not None and age_h > (72 if filing else fresh_hours)) or \
            bool(old_year and not filing and int(old_year.group(0)) <= now.year - 2)   # an old story resurfacing
    etype, mat, direction = "other", 0, 0
    if filing:
        etype, mat, direction = read_filing(item.get("category"), low.split(": ", 1)[-1])
        if etype == "results" and age_h is not None and age_h > 30:
            mat = 1                                   # results already out for more than a session
    else:
        for t, rx, m, d in _EVENT_RX:
            if rx.search(low):
                etype, mat, direction = t, m, d
                break
    if noise or not about or stale:
        mat = 0
    return dict(item, about=about, noise=noise, stale=stale, age_h=age_h, type=etype, materiality=mat, direction=direction,
                by="rules")


def read_with_claude(items, symbol, name, api_key):
    """Ask Claude to read the candidate headlines. Returns {index: fields} or {} if it could not be parsed."""
    lines = "\n".join(f'{i}. [{it["publisher"]}{", " + str(int(it["age_h"])) + "h ago" if it.get("age_h") is not None else ""}] {it["title"][:220]}'
                      for i, it in enumerate(items))
    prompt = (f"Stock: {name} (NSE: {symbol}). For an INTRADAY trader today, read each item below.\n{lines}\n\n"
              "Items marked 'NSE filing' are official exchange filings, shown as 'category: text'; most are routine.\n"
              "Reply with JSON only, a list with one object per item in the same order:\n"
              '[{"i":0,"about":true|false (is this specifically about this company, not a passing mention or a list),'
              '"stale":true|false (old story or already-known information resurfacing),'
              '"type":"regulatory|credit|results_bad|results_good|results|management|operations|downgrade|stake_sale|upgrade|orders|corporate_action|dividend|board_meeting|investor_meet|clarification|material_event|routine|other",'
              '"materiality":0-3 (0 none, 1 minor, 2 could move the stock today, 3 serious risk of a sharp move),'
              '"direction":-1|0|1}]')
    txt = ask_claude(prompt, api_key, 900, system="You are a careful equity news analyst for Indian stocks. Output valid JSON only.")
    try:
        arr = json.loads(re.search(r"\[.*\]", txt, re.S).group(0))
        out = {}
        for o in arr:
            i = int(o["i"])
            if 0 <= i < len(items) and o.get("type") in TYPE_TEXT:
                out[i] = dict(about=bool(o.get("about")), stale=bool(o.get("stale")), type=o["type"],
                              materiality=int(max(0, min(3, o.get("materiality", 0)))),
                              direction=int(max(-1, min(1, o.get("direction", 0)))))
        return out
    except Exception:
        return {}


def gather(symbol, nse=None, use_yahoo=True):
    """Collect everything known about one stock. Returns dict(items, meetings, actions, name, sources)."""
    sources, items, meetings, acts, name = {}, [], [], [], None
    if nse is not None:
        st_, it_, nm = nse.announcements(symbol)
        sources["NSE filings"] = st_; items += it_; name = nm
        st_, meetings = nse.board_meetings(symbol)
        sources["Results calendar"] = st_
        st_, acts = nse.actions(symbol)
        sources["Corporate actions"] = st_
    name = company_name(symbol, re.sub(r"\s+(Limited|Ltd\.?)$", "", name) if name else None)
    st_, it_ = google_news(name)
    sources["Google News"] = st_; items += it_
    if use_yahoo and st_ != "ok":
        st_, it_ = yahoo_news(symbol)
        sources["Yahoo News"] = st_; items += it_
    return dict(items=items, meetings=meetings, actions=acts, name=name, sources=sources)


def assess(symbol, g, ban=("unavailable", set()), api_key=None, today=None):
    """Turn gathered material into a Sentinel verdict."""
    today = today or pd.Timestamp.now(tz=ce.IST).date()
    name = g["name"]
    al = aliases(symbol, name)
    sources = dict(g["sources"])
    sources["F&O ban list"] = ban[0]
    read = [read_item(it, al) for it in g["items"]]
    cand = [r for r in read if not r["noise"] and (r["about"] or r["kind"] == "filing")][:14]
    llm_used = False
    if api_key and cand:
        got = read_with_claude(cand, symbol, name, api_key)
        if got:
            llm_used = True
            for i, f in got.items():
                cand[i].update(f, by="claude")
                if not f["about"] or f["stale"]:
                    cand[i]["materiality"] = 0
    events = sorted([r for r in cand if r["materiality"] >= 1 and not r["stale"]],
                    key=lambda r: (-r["materiality"], r.get("age_h") or 0))
    # de-duplicate near-identical headlines from different outlets
    seen, uniq = set(), []
    for e in events:
        k = (e["type"], re.sub(r"[^a-z0-9 ]", "", e["title"].lower())[:40])
        if k not in seen:
            seen.add(k); uniq.append(e)
    events = uniq
    reasons, level, types = [], "CLEAR", []

    def bump(lv):
        nonlocal level
        level = max(level, lv, key=["CLEAR", "WATCH", "HIGH"].index)
    # hard facts first — these are never downgraded by headline reading
    res_days = [m for m in g["meetings"] if m["results"]]
    for m in res_days:
        dd = (m["date"] - today).days
        if 0 <= dd <= 1:
            bump("HIGH"); types.append("results_day")
            reasons.append(f"Results board meeting {'today' if dd == 0 else 'tomorrow'} ({m['date']:%d %b}) — gap risk; avoid holding into the announcement")
        elif 2 <= dd <= 5:
            bump("WATCH"); types.append("results_day")
            reasons.append(f"Results on {m['date']:%d %b} — volatility may rise")
    if ban[0] == "ok" and symbol.upper() in ban[1]:
        bump("HIGH"); types.append("fo_ban")
        reasons.append("In the NSE F&O ban today — open interest at the limit, moves can be erratic")
    for a_ in g["actions"]:
        if 0 <= (a_["date"] - today).days <= 1:
            bump("WATCH"); types.append("corporate_action")
            reasons.append(f"Ex-date {a_['date']:%d %b}: {a_['subject']} — price adjusts")
    for e in events[:6]:
        if e["materiality"] >= 3 and e["direction"] <= 0:
            bump("HIGH")
        elif e["materiality"] >= 2:
            bump("WATCH")
        types.append(e["type"])
        reasons.append(f"{TYPE_TEXT.get(e['type'], e['type'])}: “{e['title'][:110]}” ({e['publisher']})")
    tone_score = sum(e["direction"] * e["materiality"] for e in events)
    tone = "positive" if tone_score >= 2 else "negative" if tone_score <= -2 else "neutral"
    ok = [k for k, v in sources.items() if v == "ok"]
    bad = [k for k, v in sources.items() if v != "ok"]
    news_ok = any(sources.get(k) == "ok" for k in ("NSE filings", "Google News", "Yahoo News"))
    if not ok:
        level, checked = "UNCHECKED", False
        reasons = ["Could not reach any news or exchange source — treat as not checked"]
    else:
        checked = True
        if level == "CLEAR":
            reasons.append(("Nothing that should move the stock sharply today" if reasons else "Nothing material found")
                           + ("" if news_ok else " in the exchange data (news search was unavailable)"))
    coverage = "Checked: " + ", ".join(ok) if ok else "Checked: nothing"
    if bad:
        coverage += " · Unavailable: " + ", ".join(bad)
    return dict(symbol=symbol, name=name, level=level, tone=tone, reasons=reasons, types=sorted(set(types)),
                llm=("Headlines read by Claude" if llm_used else None), checked=checked, sources=sources, coverage=coverage,
                past_results=sorted({str(m["date"]) for m in res_days if m["date"] < today}),
                events=[dict(title=e["title"][:160], publisher=e["publisher"], link=e.get("link", ""), type=e["type"],
                             materiality=e["materiality"], direction=e["direction"], by=e["by"],
                             when=e["when"].isoformat() if e.get("when") is not None else None) for e in events[:8]],
                headlines=[dict(title=r["title"][:160], publisher=r["publisher"], link=r.get("link", ""),
                                when=r["when"].isoformat() if r.get("when") is not None else None)
                           for r in read if not r["noise"] and r["about"] and not r["stale"]
                           and (r["kind"] == "news" or r["materiality"] >= 1)][:5])


def _demo_sentinel(symbol, day):
    h = sum(map(ord, symbol + str(day))) % 9     # stable, varies by day
    g = dict(items=[], meetings=[], actions=[], name=symbol, sources={"Demo wire": "ok"})
    now = pd.Timestamp.now(tz="UTC")
    if h == 0:
        g["items"] = [dict(title=f"{symbol} Q2 profit falls 18%, misses estimates", when=now, publisher="Demo Wire", link="",
                           kind="news", category="")]
    elif h == 1:
        g["items"] = [dict(title=f"SEBI issues show-cause notice to {symbol} over disclosure lapses", when=now,
                           publisher="Demo Wire", link="", kind="news", category="")]
    elif h == 2:
        g["items"] = [dict(title=f"{symbol} share price live: stock in focus today", when=now, publisher="Demo Wire",
                           link="", kind="news", category="")]
    return assess(symbol, g, ban=("ok", set()), today=pd.Timestamp(day).date() if day else None)


def sentinel(symbol, demo, api_key=None, ban=None, today=None, nse=None):
    """One stock's verdict. `ban` is the (status, set) from fo_ban_list(); `nse` a shared NSE client."""
    if demo:
        return _demo_sentinel(symbol, today or pd.Timestamp.now(tz=ce.IST).date())
    return assess(symbol, gather(symbol, nse if nse is not None else NSE()), ban or ("unavailable", set()), api_key, today)


def sentinel_many(symbols, demo, api_key=None, day=None, workers=4):
    """Scan many stocks (shared NSE session, small thread pool). Returns {symbol: verdict}."""
    from concurrent.futures import ThreadPoolExecutor
    if demo:
        return {s: _demo_sentinel(s, day) for s in symbols}
    nse, ban = NSE(), fo_ban_list()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(lambda s: (s, sentinel(s, False, api_key, ban, day, nse)), symbols))
    return dict(res)


# ---------------- archive + evidence: do flagged days really move more?
def day_stats(data):
    """Per stock-day movement from 5-minute bars: range, gap and close-to-close move, plus the day's range
    relative to that stock's own typical (median) range — 1.0 = a normal day for that stock."""
    rows = []
    for s, df in data.items():
        df = df[(df.index.time >= dtime(9, 15)) & (df.index.time < dtime(15, 30))]
        prev = None
        for d, g in df.groupby(df.index.date):
            if len(g) < 30:
                prev = g.Close.iloc[-1]; continue
            o, h, l, c = g.Open.iloc[0], g.High.max(), g.Low.min(), g.Close.iloc[-1]
            rows.append(dict(day=str(d), symbol=s, range_pct=(h - l) / o * 100,
                             gap_pct=abs(o / prev - 1) * 100 if prev else np.nan,
                             move_pct=abs(c / prev - 1) * 100 if prev else np.nan))
            prev = c
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["range_ratio"] = out.range_pct / out.groupby("symbol").range_pct.transform("median")
    return out


def sentinel_evidence(archive, stats):
    """Compare how much stocks moved on days the Sentinel flagged vs other days."""
    empty = dict(n_flag=0, n_base=0, z=0.0, ratio_flag=np.nan, ratio_base=np.nan, by_type=pd.DataFrame(), days=0, scanned=0)
    if archive is None or len(archive) == 0 or stats is None or len(stats) == 0:
        return empty
    a = archive.copy()
    a["day"] = a.day.astype(str)
    m = stats.merge(a[["day", "symbol", "level", "types", "scanned"]], on=["day", "symbol"], how="left")
    m["flag"] = m.level.isin(["WATCH", "HIGH"])
    f, b = m[m.flag].range_ratio.dropna(), m[~m.flag].range_ratio.dropna()
    rows = []
    for t in sorted({t for ts in m[m.flag].types.dropna() for t in ts}):
        x = m[m.flag & m.types.map(lambda ts: isinstance(ts, list) and t in ts)].range_ratio.dropna()
        if len(x):
            rows.append(dict(type=t, label=TYPE_TEXT.get(t, t), n=len(x), ratio=x.mean(), z=_welch(x, b)))
    return dict(n_flag=len(f), n_base=len(b), z=_welch(f, b), ratio_flag=f.mean() if len(f) else np.nan,
                ratio_base=b.mean() if len(b) else np.nan,
                by_type=pd.DataFrame(rows).sort_values("ratio", ascending=False) if rows else pd.DataFrame(),
                days=int(a[a.scanned == True].day.nunique()), scanned=int((a.scanned == True).sum()))  # noqa: E712


# ============================================================================ COACH — features, learning, post-mortems
FEATURE_TEXT = {   # neutral descriptions — the data decides whether a condition is good or bad
    "market": {"against market": "Against the market direction", "flat market": "Market had no clear direction",
               "with market": "With the market direction"},
    "timing": {"late": "Late-day signal (after 12:30)", "midday": "Midday signal (10:30–12:30)",
               "early": "Early signal (before 10:30)"},
    "volume": {"weak": "Weak volume (under 1.5× normal)", "normal": "Normal volume (1.5–2.5×)",
               "strong": "Strong volume (over 2.5×)"},
    "gap": {"against gap": "Against the opening gap", "flat gap": "No meaningful opening gap",
            "with gap": "With the opening gap"},
    "chase": {"extended": "Far from VWAP at entry (extended move)", "moderate": "Moderately away from VWAP at entry",
              "near": "Close to VWAP at entry"},
    "range": {"wide": "Wide opening range (stop far away)", "tight": "Tight opening range (stop inside normal noise)",
              "normal": "Normal opening-range width"},
    "news": {"event": "News or corporate-event day", "clear": "No news flags", "unknown": "News not checked"},
    "setup": {"ORB": "Opening range breakout", "VWAP": "VWAP pullback"},
    "side": {"long": "Long trades", "short": "Short trades"},
    "mood": {k: f"{k} market mood" for k in DEFAULT_MULTS},
}
LEARN_FEATURES = ["market", "mood", "timing", "volume", "gap", "chase", "range", "news", "setup", "side"]


def signal_features(g, sig, info, idx_day=None, news="unknown", mood="unknown"):
    i = sig["i"]
    t = g.index[i]
    side = sig["side"]
    tp = (g.High + g.Low + g.Close) / 3
    vw = (tp * g.Volume).cumsum() / g.Volume.cumsum().replace(0, np.nan)
    vwap_at = vw.iloc[max(i - 1, 0)]
    ext = abs(sig["entry"] / vwap_at - 1) * 100 if vwap_at and not np.isnan(vwap_at) else np.nan
    mkt = "unknown"
    if idx_day is not None and not idx_day.empty:
        upto = idx_day[idx_day.index < t]
        if len(upto):
            ch = (upto.Close.iloc[-1] / idx_day.Open.iloc[0] - 1) * 100 * side
            mkt = "with market" if ch > 0.15 else "against market" if ch < -0.15 else "flat market"
    rv = info.get("relvol", np.nan)
    gap = info.get("gap_pct", 0) * side
    orp = info.get("or_pct", np.nan)
    return {
        "market": mkt, "mood": mood,
        "timing": "early" if t.time() < dtime(10, 30) else "midday" if t.time() < dtime(12, 30) else "late",
        "volume": "unknown" if pd.isna(rv) else "weak" if rv < 1.5 else "normal" if rv < 2.5 else "strong",
        "gap": "with gap" if gap > 0.2 else "against gap" if gap < -0.2 else "flat gap",
        "chase": "unknown" if pd.isna(ext) else "near" if ext < 0.3 else "moderate" if ext < 0.8 else "extended",
        "range": "unknown" if pd.isna(orp) else "tight" if orp < 0.6 else "normal" if orp < 1.3 else "wide",
        "news": news, "setup": sig["setup"], "side": "long" if side == 1 else "short",
    }


def _slope(v):
    """Least-squares slope per bar of a short series (NaN if fewer than 3 points)."""
    v = np.asarray(v, float)
    v = v[~np.isnan(v)]
    if len(v) < 3:
        return np.nan
    x = np.arange(len(v)) - (len(v) - 1) / 2
    return float((x * (v - v.mean())).sum() / (x ** 2).sum())


def model_features(g, sig, info, idx_day=None, mood=None):
    """Continuous inputs for the win-probability models, using only bars completed before the entry bar.
    Direction-dependent inputs are signed so that positive always means 'in the trade's direction'."""
    i, side, entry = sig["i"], sig["side"], float(sig["entry"])
    h = g.iloc[:max(i, 1)]
    t = g.index[i]
    c = h.Close.values.astype(float)
    tp = (h.High + h.Low + h.Close) / 3
    vw = ((tp * h.Volume).cumsum() / h.Volume.cumsum().replace(0, np.nan)).values
    atr = float(((h.High - h.Low) / h.Close).tail(12).mean() * 100) or np.nan      # normal 5-min bar size, %
    pct = lambda a, b: (a / b - 1) * 100 if b and not np.isnan(b) else np.nan
    sl = lambda v, n: _slope(np.log(np.clip(np.asarray(v[-n:], float), 1e-9, None))) * 100       # % per bar
    s6, s12 = sl(c, 6), sl(c, 12)
    s3a, s3b = sl(c, 3), sl(c[:-3], 3) if len(c) >= 6 else np.nan
    hi, lo = float(h.High.max()), float(h.Low.min())
    pos = (entry - lo) / (hi - lo) if hi > lo else 0.5
    lb = h.iloc[-1]
    body = (lb.Close - lb.Low) / (lb.High - lb.Low) if lb.High > lb.Low else 0.5
    stop_pct = abs(entry - sig["stop"]) / entry * 100
    day_move = pct(entry, float(g.Open.iloc[0])) * side
    n_move = n_slope = np.nan
    if idx_day is not None and not idx_day.empty:
        u = idx_day[idx_day.index < t]
        if len(u):
            n_move = pct(float(u.Close.iloc[-1]), float(idx_day.Open.iloc[0])) * side
            n_slope = sl(u.Close.values, 6) * side
    rv = info.get("relvol", np.nan)
    m = mood or {}
    x = dict(
        mins=(t.hour * 60 + t.minute) - 555, gap=info.get("gap_pct", np.nan) * side,
        relvol=np.log(rv) if rv and not pd.isna(rv) and rv > 0 else np.nan, or_pct=info.get("or_pct", np.nan),
        stop_pct=stop_pct, stop_atr=stop_pct / atr if atr and not np.isnan(atr) else np.nan,
        vwap_dist=pct(entry, vw[-1]) * side / atr if atr and not np.isnan(atr) else np.nan,
        slope_px=s6 * side / atr if atr else np.nan, slope_px_long=s12 * side / atr if atr else np.nan,
        slope_accel=(s3a - s3b) * side / atr if atr else np.nan,
        slope_vwap=sl(vw, 6) * side / atr if atr else np.nan,
        slope_vol=_slope(np.log(np.clip(h.Volume.values[-6:].astype(float), 1, None))),
        day_pos=pos if side == 1 else 1 - pos, day_move=day_move, last_bar=body if side == 1 else 1 - body,
        nifty_move=n_move, nifty_slope=n_slope,
        rel_strength=day_move - n_move if not pd.isna(n_move) else np.nan,
        mood_bias=m.get("bias", 0) * side, vix=m.get("vix", np.nan), vix_chg=m.get("vix_chg", np.nan),
        is_orb=1.0 if sig["setup"] == "ORB" else 0.0, is_long=1.0 if side == 1 else 0.0)
    return {k: (None if v is None or pd.isna(v) or not np.isfinite(v) else round(float(v), 5)) for k, v in x.items()}


def trade_key(day, symbol, setup, side):
    return f"{day}|{symbol}|{setup}|{'L' if side == 1 else 'S'}"


# ============================================================================ EXIT AGENT
WINDOWS = [0, 15, 30, 60, 120]      # execution window in minutes; 0 = hold to stop / target / 15:15
WINDOW_TEXT = {0: "Hold to stop, target or 15:15", 15: "15 minutes", 30: "30 minutes", 60: "1 hour", 120: "2 hours"}

EXIT_POLICIES = {
    "fixed": "Fixed — stop, target or 15:15, whichever first",
    "breakeven": "Breakeven — move the stop to entry once the trade is +1R",
    "trail": "Trail — once +1R, trail the stop behind each 5-min bar's low (high for shorts)",
    "partial": "Partial — book half at +1R, move stop to entry, let the rest run to target",
    "time": "Time-stop — exit at 60 minutes if the trade hasn't reached +0.5R",
}


def simulate_exit(g, sig, policy, a, deadline=None):
    """Walk the bars after entry under one exit policy. Stops are checked before targets inside a bar
    (conservative), and stop moves only take effect from the next bar. With `deadline` (a timestamp) the trade
    is closed at the open of the first bar at or after it — the end of the recommendation's execution window."""
    o, h, l, c = (g[k].values for k in ["Open", "High", "Low", "Close"])
    t = g.index
    i0, side, entry = sig["i"], sig["side"], sig["entry"]
    stop, tgt = sig["stop"], sig["target"]
    risk = abs(entry - stop)
    if risk <= 0:
        return dict(exit=entry, t=t[i0], reason="close", r=0.0, stop_now=stop, note="")
    exit_t = dtime(*map(int, a.exit_time.split(":")))
    best, worst, half, note = 0.0, 0.0, False, ""
    px, tx, reason = c[-1], t[-1], "close"
    for j in range(i0, len(g)):
        if deadline is not None and t[j] >= deadline and j > i0:
            px, tx, reason = o[j], t[j], "window"; break
        worst = max(worst, ((entry - l[j]) if side == 1 else (h[j] - entry)) / risk)
        if t[j].time() >= exit_t:
            px, tx, reason = o[j], t[j], "time"; break
        if (l[j] <= stop) if side == 1 else (h[j] >= stop):
            px = min(o[j], stop) if side == 1 else max(o[j], stop)
            tx, reason = t[j], "stop"; break
        if policy == "partial" and not half:
            lvl = entry + side * risk
            if (h[j] >= lvl) if side == 1 else (l[j] <= lvl):
                half, note = True, f"half booked at +1R {t[j]:%H:%M}"
        if (h[j] >= tgt) if side == 1 else (l[j] <= tgt):
            px = (max(o[j], tgt) if side == 1 else min(o[j], tgt)) if j > i0 else tgt
            tx, reason = t[j], "target"; break
        best = max(best, ((h[j] - entry) if side == 1 else (entry - l[j])) / risk)
        if policy in ("breakeven", "partial") and best >= 1:
            new = entry
            if (new > stop) if side == 1 else (new < stop):
                stop, note = new, (note + "; " if note else "") + f"stop to entry {t[j]:%H:%M}"
        if policy == "trail" and best >= 1:
            new = l[j] if side == 1 else h[j]
            if (new > stop) if side == 1 else (new < stop):
                stop, note = new, f"trailing since +1R (stop ₹{new:,.2f})"
        if policy == "time" and (t[j] - t[i0]) >= pd.Timedelta(minutes=60) and best < 0.5:
            px, tx, reason = c[j], t[j], "time-stop"; break
    move = (px - entry) * side / risk
    r = 0.5 * 1.0 + 0.5 * move if half else move
    r -= entry * a.cost_pct / 100 / risk
    best = max(best, ((h[j] - entry) if side == 1 else (entry - l[j])) / risk) if len(g) > i0 else best
    return dict(exit=px, t=tx, reason=reason, r=float(r), stop_now=stop, note=note, mfe=float(best),
                mae=float(min(worst, max(0.0, -move) if reason == "stop" else worst)))


def exit_status(sim):
    lab = {"stop": "STOPPED", "target": "TARGET HIT", "time": "CLOSED (time)", "time-stop": "TIME-STOP",
           "window": "WINDOW CLOSE"}
    return "OPEN" if sim["reason"] == "close" else f"{lab[sim['reason']]} {sim['t']:%H:%M}"


# ============================================================================ RISK AGENT
def corr_pairs(data, symbols, before_day, lookback=10):
    """Correlation of 5-minute returns over the sessions before `before_day` (no look-ahead)."""
    rets = {}
    for s in symbols:
        df = data.get(s)
        if df is None:
            continue
        d = df[df.index.date < before_day]
        days = sorted(set(d.index.date))[-lookback:]
        d = d[np.isin(d.index.date, days)]
        rets[s] = d.Close.pct_change()
    if len(rets) < 2:
        return {}
    m = pd.DataFrame(rets).corr(min_periods=50)
    return {(a_, b_): float(m.loc[a_, b_]) for a_ in m.index for b_ in m.columns if a_ != b_ and pd.notna(m.loc[a_, b_])}


def risk_check(c, book, cfg, corr):
    """c: candidate (symbol, side, setup, t, exp_r, sentinel, value_x). book: earlier ALLOWED trades today with
    symbol, side, t, t_exit (None while open), r and value_x. value_x = position value as a multiple of capital
    (after sizing multipliers). Returns verdict ALLOW / REDUCE / BLOCK, size multiplier, reasons."""
    if not cfg.get("risk_enabled", True):
        return "ALLOW", 1.0, ["Risk agent switched off"]
    t = c["t"]
    reasons, mult = [], 1.0
    open_now = [b for b in book if b["t"] <= t and (b["t_exit"] is None or b["t_exit"] > t)]
    realized = sum(b["r"] for b in book if b["t_exit"] is not None and b["t_exit"] <= t)
    if c["setup"] in cfg.get("paused_setups", []):
        return "BLOCK", 0.0, [f"{c['setup']} setup is paused (approved by reviewer)"]
    if realized <= -cfg["daily_loss_R"]:
        return "BLOCK", 0.0, [f"Daily loss limit reached ({realized:+.1f}R vs −{cfg['daily_loss_R']:g}R cap)"]
    if len(book) >= cfg["max_trades"]:
        return "BLOCK", 0.0, [f"Already {len(book)} trades today (cap {cfg['max_trades']})"]
    if len(open_now) >= cfg["max_open"]:
        return "BLOCK", 0.0, [f"{len(open_now)} positions already open (cap {cfg['max_open']})"]
    if cfg.get("sentinel_block") and c.get("sentinel") == "HIGH":
        return "BLOCK", 0.0, ["Sentinel rated news risk HIGH"]
    same = [b for b in open_now if b["symbol"] == c["symbol"]]
    if same:
        return "BLOCK", 0.0, [f"Already holding {c['symbol']} from {same[0]['t']:%H:%M} — no second position in the same stock"]
    twins = [(b["symbol"], corr.get((c["symbol"], b["symbol"]), 0)) for b in open_now if b["side"] == c["side"]]
    twins = [x for x in twins if x[1] >= cfg["corr_limit"]]
    if twins:
        mult *= 0.5
        reasons.append(f"Moves with open {twins[0][0]} (correlation {twins[0][1]:.2f}) — half size")
    # total exposure across open positions, in multiples of capital
    cap_x = float(cfg.get("max_exposure_x", 0) or 0)
    val = c.get("value_x")
    if cap_x and val:
        used = sum(b.get("value_x", 0.0) for b in open_now)
        room = cap_x - used
        want = val * mult
        if room <= 0.02:
            return "BLOCK", 0.0, [f"Exposure limit reached — open positions already {used:.1f}× capital (cap {cap_x:g}×)"]
        if want > room:
            mult *= room / want
            reasons.append(f"Exposure cap {cap_x:g}× capital: {used:.1f}× already open, size cut to fit")
    if cfg.get("edge_sizing") and c.get("exp_r") is not None:
        m = float(np.clip(1 + cfg.get("edge_k", 1.0) * c["exp_r"], 0.5, 1.5))
        mult *= m
        reasons.append(f"Edge sizing ×{m:.2f} from Coach expected {c['exp_r']:+.2f}R")
    if not reasons:
        reasons.append(f"Within limits: {len(open_now)} open, {len(book)} taken, day {realized:+.1f}R")
    return ("REDUCE" if mult < 0.999 else "ALLOW"), mult, reasons


# ============================================================================ REPLAY (point-in-time history)
def replay(data, idx, a, mults=None):
    """Every recent signal with features known at signal time and the result under every exit policy."""
    rows, corr_by_day = [], {}
    if getattr(a, "strategy", None) or getattr(a, "race", None):
        import copy
        a = copy.copy(a)
        a.idx_bars = idx.get("intraday")          # strategies with a Nifty filter need the index bars
    for s, days in ce.prep_days(data).items():
        last_day = days[-1][0] if days else None
        for d, g, pc, avg in days[1:]:
            sigs, info = ce.day_signals(g, pc, avg, a)
            if not sigs:
                continue
            idd = index_day(idx, d)
            for sg in sigs:
                ex = {p: simulate_exit(g, sg, p, a) for p in EXIT_POLICIES}
                pw = {}
                for p in EXIT_POLICIES:
                    for w in WINDOWS:
                        v = ex[p] if w == 0 else simulate_exit(g, sg, p, a, sg["time"] + pd.Timedelta(minutes=w))
                        pw[f"{p}|{w}"] = [round(v["r"], 4), v["t"].isoformat()]
                if ex["fixed"]["reason"] == "close" and d == last_day:
                    continue  # still running today
                mood = pulse(idx, d, asof=sg["time"], mults=mults)
                feats = signal_features(g, sg, info, idd, mood=mood["regime"])
                feats["_exits"] = {p: [round(v["r"], 4), v["t"].isoformat()] for p, v in ex.items()}
                feats["_pw"] = pw
                feats["_bias"] = mood["bias"]
                feats["_x"] = model_features(g, sg, info, idd, mood)
                feats["_strategy"] = sg.get("strategy", f"Classic {sg['setup']}")
                rows.append(dict(key=trade_key(d, s, sg["setup"], sg["side"]), ts=sg["time"], day=d, source="backtest",
                                 symbol=s, setup=sg["setup"], side=sg["side"], entry=sg["entry"], stop=sg["stop"],
                                 target=sg["target"], exit=ex["fixed"]["exit"], r=ex["fixed"]["r"],
                                 status=ex["fixed"]["reason"], features=feats, reason=None))
    for d in sorted({r["day"] for r in rows}):
        syms = sorted({r["symbol"] for r in rows if r["day"] == d})
        if len(syms) > 1:
            corr_by_day[str(d)] = {f"{k[0]}|{k[1]}": v for k, v in corr_pairs(data, syms, d).items()}
    return rows, corr_by_day


history_trades = lambda data, idx, a, source="backtest": replay(data, idx, a)[0]  # backward compatible


def _feats(f):
    return f if isinstance(f, dict) else json.loads(f or "{}")


class Coach:
    """Learns, from recent trades weighted towards the latest sessions, which conditions make or lose money.
    Predicts expected R (profit per unit of risk after costs) and win odds for new signals,
    and explains losers by the conditions that historically lose most."""
    K = 20          # shrinkage: a condition needs ~20 (recency-weighted) trades before it moves the estimate much
    DAMP = 0.6      # dampens the sum of correlated conditions

    def __init__(self, trades, half_life=None, asof_day=None, plan=None):
        """plan = 'policy|window' (see plan_key): learn from each trade's result under the plan that is actually
        executed, not the plain hold-to-target plan. Rows without that variant fall back to their stored r."""
        t = trades.copy() if trades is not None else pd.DataFrame()
        self.plan = plan
        if not t.empty:
            t = t[t.r.notna()].copy()
            t["features"] = t.features.map(_feats)
            if plan:
                t["r"] = [f["_pw"][plan][0] if isinstance(f.get("_pw"), dict) and plan in f["_pw"] else r
                          for f, r in zip(t.features, t.r)]
            t["win"] = (t.r > 0).astype(float)
            if half_life and asof_day is not None and len(t):
                age = (pd.Timestamp(asof_day) - pd.to_datetime(t.day)).dt.days.clip(lower=0)
                t["w"] = 0.5 ** (age / float(half_life))
            else:
                t["w"] = 1.0
        self.t = t
        self.n = len(t)
        self.n_eff = float(t.w.sum()) if self.n else 0.0
        self.p0 = float((t.w * t.win).sum() / t.w.sum()) if self.n else 0.45
        self.r0 = float((t.w * t.r).sum() / t.w.sum()) if self.n else 0.0
        self.table = self._learn()
        self._lut = {(r.feature, r.bucket): r for r in self.table.itertuples(index=False)}

    def _learn(self):
        cols = ["feature", "bucket", "n", "n_eff", "win_rate", "avg_r", "shr_p", "shr_r", "edge", "redge"]
        if not self.n:
            return pd.DataFrame(columns=cols)
        rows = []
        for f in LEARN_FEATURES:
            vals = self.t.features.map(lambda d: d.get(f, "unknown"))
            for b, grp in self.t.groupby(vals):
                if b == "unknown":
                    continue
                w = grp.w
                nw = float(w.sum())
                wins, rs = float((w * grp.win).sum()), float((w * grp.r).sum())
                shr_p = (wins + self.K * self.p0) / (nw + self.K)
                shr_r = (rs + self.K * self.r0) / (nw + self.K)
                rows.append(dict(feature=f, bucket=b, n=len(grp), n_eff=nw, win_rate=wins / nw if nw else np.nan,
                                 avg_r=rs / nw if nw else np.nan, shr_p=shr_p, shr_r=shr_r,
                                 edge=shr_p - self.p0, redge=shr_r - self.r0))
        return pd.DataFrame(rows, columns=cols).sort_values("redge")

    @staticmethod
    def _logit(p):
        p = min(max(p, 0.02), 0.98)
        return math.log(p / (1 - p))

    def _rows(self, feats):
        for f in LEARN_FEATURES:
            b = feats.get(f, "unknown")
            row = self._lut.get((f, b))
            if row is not None:
                yield f, b, row

    def score(self, feats, threshold=0.0):
        if not self.n:
            return dict(conf=None, base=None, exp_r=None, r0=None, verdict="Learning", drivers=[])
        z, er, drivers = self._logit(self.p0), self.r0, []
        for f, b, row in self._rows(feats):
            z += self.DAMP * (self._logit(row.shr_p) - self._logit(self.p0))
            er += self.DAMP * row.redge
            if abs(row.redge) >= 0.03:
                drivers.append((row.redge, FEATURE_TEXT.get(f, {}).get(b, f"{f}: {b}"), int(row.n), row.win_rate, row.avg_r))
        conf = 1 / (1 + math.exp(-z))
        drivers.sort(key=lambda x: x[0])
        verdict = "Caution" if er < threshold else "Favourable" if er >= max(self.r0 + 0.1, 0.1) else "Neutral"
        return dict(conf=conf, base=self.p0, exp_r=float(er), r0=self.r0, verdict=verdict, drivers=drivers)

    def postmortem(self, feats, r):
        """Why did this trade lose? The present conditions that historically lose the most."""
        if r is None or pd.isna(r) or r > 0:
            return None
        bad = sorted([(row.redge, f, b, int(row.n), row.win_rate, row.avg_r) for f, b, row in self._rows(feats)
                      if row.redge <= -0.05])
        if not bad:
            return dict(primary="Normal variance — conditions were average; a loss like this is expected sometimes",
                        factors=[], feature=None)
        _, f0, b0, n0, wr0, ar0 = bad[0]
        return dict(primary=f"{FEATURE_TEXT.get(f0, {}).get(b0, b0)} — averages {ar0:+.2f}R across {n0} recent trades "
                            f"(won {wr0 * 100:.0f}%) vs {self.r0:+.2f}R overall",
                    feature=f"{f0}={b0}",
                    factors=[dict(text=FEATURE_TEXT.get(f, {}).get(b, b), win_rate=round(wr * 100), avg_r=round(ar, 2), n=n)
                             for _, f, b, n, wr, ar in bad[:3]])

    def lessons(self, min_n=15):
        if self.table.empty:
            return []
        out = []
        for _, r in self.table[self.table.n >= min_n].iterrows():
            if r.redge <= -0.08:
                direction = "avoid" if r.avg_r < 0 else "weaker"
            elif r.redge >= 0.08:
                direction = "favour"
            else:
                continue
            out.append(dict(feature=r.feature, bucket=r.bucket, n=int(r.n), win_rate=r.win_rate, avg_r=r.avg_r,
                            text=FEATURE_TEXT.get(r.feature, {}).get(r.bucket, r.bucket), direction=direction))
        return sorted(out, key=lambda x: x["avg_r"])


# ============================================================================ CONFIG (human-approved settings)
DEFAULT_CONFIG = dict(
    coach_filter=False, coach_threshold=0.0, half_life=10,
    coach_model="bucket", coach_min_win=0.0,     # which brain predicts win odds; skip signals below these odds (0 = off)
    pulse_sizing=True, mults=dict(DEFAULT_MULTS),
    exit_policy="fixed",
    risk_enabled=True, daily_loss_R=2.0, max_open=3, max_trades=3, corr_limit=0.7,
    edge_sizing=False, edge_k=1.0, sentinel_block=False, paused_setups=[],
    paper_window_min=15,
    scout_objective="win",  # what Scout optimises: win (highest win rate that is still profitable), exp, month
    strategy=None,          # None = classic ORB + VWAP engine; otherwise a Scout strategy approved by a reviewer
    min_stop_pct=0.0,       # skip signals whose stop is closer than this (% of price) — costs dominate tight stops
    panel=False,            # True = the recommendation panel picks the strategy each day (overrides `strategy`)
    panel_mode="race",      # race = every armed specialist trades the moment it fires; selector = one pick a day
    focus_n=10,             # race mode, phase 1: trade only the top-N Nifty heavyweights (0 = whole watchlist)
    race=None,              # filled each morning from the panel (never saved): the armed specialists
    # signal engine and paper account — shared by everyone, so the trade log and the learning use one system
    setup="both", shorts=False, rr=2.0, min_relvol=1.5, cost_pct=0.10,
    capital=200000.0, risk_pct=0.5, leverage=5.0, max_exposure_x=5.0,
    watchlist=list(ce.WATCHLIST),
)
ENGINE_KEYS = ["setup", "shorts", "rr", "min_relvol", "cost_pct", "capital", "risk_pct", "leverage", "strategy",
               "min_stop_pct", "race"]
OPTIONAL_ENGINE_KEYS = {"min_stop_pct", "race"}   # left out of the engine key while off, so existing history keeps its id


def engine_args(cfg):
    """The signal engine's parameters, taken from the shared settings."""
    import argparse
    c = merged(cfg)
    return argparse.Namespace(setup=c["setup"], shorts=bool(c["shorts"]), or_bars=3, min_relvol=float(c["min_relvol"]),
                              max_gap=4.0, min_or_pct=0.3, max_or_pct=2.0, rr=float(c["rr"]), last_entry="14:30",
                              exit_time="15:15", capital=float(c["capital"]), risk_pct=float(c["risk_pct"]),
                              leverage=float(c["leverage"]), cost_pct=float(c["cost_pct"]),
                              max_trades=int(c["max_trades"]), max_losses=2,       # was hard-coded to 3
                              strategy=c.get("strategy") or None, race=c.get("race") or None, idx_bars=None,
                              min_stop_pct=float(c.get("min_stop_pct", 0) or 0))


def engine_key(cfg):
    c = merged(cfg)
    return "|".join(f"{k}={json.dumps(c[k], sort_keys=True)}" for k in ENGINE_KEYS
                    if not (k in OPTIONAL_ENGINE_KEYS and not c.get(k)))


def plan_key(cfg):
    """The plan a recommendation is actually executed with: exit policy + execution window."""
    c = merged(cfg)
    return f"{c['exit_policy']}|{int(c.get('paper_window_min', 0) or 0)}"


def merged(cfg):
    c = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in (cfg or {}).items():
        if k == "mults":
            c["mults"].update(v or {})
        elif k in c:
            c[k] = v
    return c


# ============================================================================ SCORECARD (walk-forward)
def _welch(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return 0.0
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return float((a.mean() - b.mean()) / se) if se > 0 else 0.0


def _paired(d):
    d = np.asarray(d, float)
    if len(d) < 2 or d.std(ddof=1) == 0:
        return 0.0
    return float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d))))


def evidence(z, n, min_n=20):
    """0–100 agent score from the strength of evidence (z). 50 = no effect either way."""
    if n is None or n < min_n:
        return dict(score=None, grade="Too few trades to judge", tone="mid", z=z, n=n)
    score = int(round(50 + 50 * math.tanh(z / 2.5)))
    grade = ("Strong evidence it helps" if z >= 2 else "Likely helps" if z >= 1 else
             "No clear effect" if z > -1 else "Likely hurts" if z > -2 else "Strong evidence it hurts")
    tone = "good" if z >= 1 else "bad" if z <= -1 else "mid"
    return dict(score=score, grade=grade, tone=tone, z=z, n=n)


def _metrics(r, w, ts):
    r, w = np.asarray(r, float), np.asarray(w, float)
    m = w > 0
    if not m.any():
        return dict(trades=0, exp=np.nan, total=0.0, win=np.nan, maxdd=0.0)
    order = np.argsort(np.asarray(ts)[m])
    pnl = (r[m] * w[m])[order]
    eq = np.cumsum(pnl)
    dd = float((eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min()) if len(eq) else 0.0
    return dict(trades=int(m.sum()), exp=float(pnl.sum() / w[m].sum()), total=float(pnl.sum()),
                win=float((r[m] > 0).mean()), maxdd=min(dd, 0.0))


BRAINS = dict({"bucket": "Rule table (current Coach)"}, **ml.KINDS)


def _prep(rows, cfg):
    """Replay rows → frame with the result of every exit policy at the active execution window."""
    df = pd.DataFrame(rows)
    df["features"] = df.features.map(_feats)
    df = df[df.features.map(lambda f: "_exits" in f)].reset_index(drop=True)
    df["ts"] = pd.to_datetime(df.ts)
    W = int(cfg.get("paper_window_min", 0) or 0)
    has_pw = bool(len(df)) and df.features.map(lambda f: "_pw" in f).all()
    if not has_pw:
        W = 0
    for p in EXIT_POLICIES:
        df["r_" + p] = df.features.map(lambda f: f["_pw"][f"{p}|{W}"][0] if has_pw else f["_exits"][p][0])
        df["t_" + p] = df.features.map(lambda f: f["_pw"][f"{p}|{W}"][1] if has_pw else f["_exits"][p][1])
    if has_pw:
        for w in WINDOWS:
            df[f"w_{w}"] = df.features.map(lambda f: f["_pw"][f"{cfg['exit_policy']}|{w}"][0])
    df["r_plain"] = df.r
    df["r"] = df["r_" + cfg["exit_policy"]]          # the Coach is trained and judged on the executed plan
    return df, W, has_pw


def study_args(cfg):
    """A wider version of the signal engine used only to give the models more examples to learn from:
    both setups, both directions and a lower volume bar. Trades are still only taken on the real settings."""
    a = engine_args(cfg)
    a.min_stop_pct = 0.0                      # the practice set stays wide; the stop rule only filters real trades
    if a.race:
        a.race = [dict(x, spec=rs.study_spec(x["spec"])) for x in a.race]
    elif a.strategy:
        a.strategy = rs.study_spec(a.strategy)
    else:
        a.setup, a.shorts, a.min_relvol = "both", True, min(float(a.min_relvol), 0.8)
    return a


def _xyr(df):
    return ml.matrix([f.get("_x") for f in df.features]), (df.r > 0).astype(int).values, df.r.values, df.day.values


def model_predictions(rows, cfg, study=None, warmup=10):
    """Walk-forward win probability and expected R from every model for the executed plan.
    The models learn from `study` (a wider set of signals from the same sessions; defaults to `rows`) and are
    scored on `rows` — the signals the engine really gives — and on the study set itself.
    Depends only on the data and the plan (exit policy + window), so it can be cached across other settings."""
    cfg = merged(cfg)
    if not rows:
        return None
    df, _, _ = _prep(rows, cfg)
    sd = _prep(study, cfg)[0] if study else df
    has_x = lambda d: len(d) and d.features.map(lambda f: isinstance(f.get("_x"), dict)).all()
    if df.day.nunique() <= warmup + 2 or not has_x(df) or not has_x(sd):
        return None
    Xs, ys, rs, ds = _xyr(sd)
    Xa, _, _, da = _xyr(df)
    test_days = sorted(df.day.unique())
    step = max(3, -(-(len(test_days) - warmup) // 20))   # ~20 refits whatever the history length (memory grows it)
    wf = ml.walk_forward((Xs, ys, rs, ds), {"actual": (Xa, da), "study": (Xs, ds)}, test_days, warmup, step=step)
    out = pd.DataFrame({"key": df.key.values})
    st = pd.DataFrame({"y": ys, "r": rs, "day": ds})
    for k in ml.KINDS:
        out["p_" + k], out["er_" + k] = wf["actual"][k]
        st["p_" + k] = wf["study"][k][0]
    out = out.drop_duplicates("key", keep="last").set_index("key")
    out.attrs["study"] = st[st["p_ensemble"].notna()].reset_index(drop=True)
    out.attrs["n_study"] = len(sd)
    return out


def fit_brain(trades, plan, kind):
    """Fit the chosen model on every finished trade so far (for scoring today's live signals)."""
    if kind not in ml.KINDS or trades is None or len(trades) == 0:
        return None
    t = trades[trades.r.notna()].copy()
    t["features"] = t.features.map(_feats)
    t = t[t.features.map(lambda f: isinstance(f.get("_x"), dict))]
    if t.empty:
        return None
    r = np.array([f["_pw"][plan][0] if isinstance(f.get("_pw"), dict) and plan in f["_pw"] else r_
                  for f, r_ in zip(t.features, t.r)], float)
    return ml.Brain(kind).fit(ml.matrix([f["_x"] for f in t.features]), (r > 0).astype(int), r)


def score_signal(coach, brain, feats, cfg):
    """One verdict per signal. The rule table always supplies the reasons; when a model has been approved as
    the Coach's brain it supplies the win odds and expected R."""
    sc = coach.score(feats, cfg["coach_threshold"])
    sc["model"] = "bucket"
    if brain is not None and getattr(brain, "ok", False):
        p, er = brain.predict(feats.get("_x"))
        if p is not None:
            sc.update(conf_rules=sc.get("conf"), exp_r_rules=sc.get("exp_r"), conf=p, exp_r=er, base=brain.p0,
                      r0=brain.r0, model=brain.kind)
    if sc.get("exp_r") is None:
        return sc
    mw = float(cfg.get("coach_min_win", 0) or 0)
    why = (f"expected {sc['exp_r']:+.2f}R is below {cfg['coach_threshold']:+.2f}R" if sc["exp_r"] < cfg["coach_threshold"]
           else f"win odds {sc['conf'] * 100:.0f}% are below the {mw * 100:.0f}% bar" if mw > 0 and sc["conf"] < mw else None)
    sc["caution_why"] = why
    sc["verdict"] = "Caution" if why else "Favourable" if sc["exp_r"] >= max(sc["r0"] + 0.1, 0.1) else "Neutral"
    return sc


def model_lab(T, active):
    """How well each brain's walk-forward win odds separated winners from losers on the same trades."""
    y = (T.r > 0).astype(int).values
    r = T.r.values
    days = sorted(T.day.unique())
    recent = (T.day >= days[len(days) // 2]).values
    rows = []
    st = T.attrs.get("study")
    for k, label in BRAINS.items():
        if "p_" + k not in T or T["p_" + k].notna().sum() < 40:
            continue
        wide = dict(n_wide=0, auc_wide=np.nan, win_wide=np.nan, win_wide_top30=np.nan, r_wide_top30=np.nan, z_wide=0.0)
        if st is not None and "p_" + k in st and len(st) >= 60:
            ps, ysd, rsd = st["p_" + k].values, st.y.values, st.r.values
            o = np.argsort(-ps, kind="mergesort")[:max(int(round(len(ps) * .3)), 1)]
            h_ = ps >= np.median(ps)
            wide = dict(n_wide=len(st), auc_wide=ml.auc(ps, ysd), win_wide=float(ysd.mean()),
                        win_wide_top30=float(ysd[o].mean()), r_wide_top30=float(rsd[o].mean()),
                        z_wide=_welch(rsd[h_], rsd[~h_]))
        m = T["p_" + k].notna().values
        p, yy, rr = T["p_" + k].values[m], y[m], r[m]
        order = np.argsort(-p, kind="mergesort")
        top = lambda q: order[:max(int(round(len(p) * q)), 1)]
        hi = p >= np.median(p)
        rows.append(dict(kind=k, label=label, active=k == active, n=int(m.sum()), auc=ml.auc(p, yy),
                         auc_recent=ml.auc(p[recent[m]], yy[recent[m]]),
                         brier=float(((p - yy) ** 2).mean()), brier_base=float(yy.mean() * (1 - yy.mean())),
                         win_all=float(yy.mean()), r_all=float(rr.mean()),
                         win_top50=float(yy[top(.5)].mean()), r_top50=float(rr[top(.5)].mean()),
                         win_top30=float(yy[top(.3)].mean()), r_top30=float(rr[top(.3)].mean()),
                         z=_welch(rr[hi], rr[~hi]) if hi.any() and (~hi).any() else 0.0, **wide))
    tab = pd.DataFrame(rows)
    sel = {}
    for k in (tab.kind if len(tab) else []):
        m = T["p_" + k].notna().values
        p, yy, rr = T["p_" + k].values[m], y[m], r[m]
        n_days = T.day[m].nunique()
        order = np.argsort(-p, kind="mergesort")
        sel[k] = pd.DataFrame([dict(share=q, trades=len(ix), per_month=len(ix) / n_days * 21, min_p=float(p[ix].min()),
                                    said=float(p[ix].mean()), win=float(yy[ix].mean()), exp=float(rr[ix].mean()),
                                    total=float(rr[ix].sum()))
                               for q in (1.0, .7, .5, .3, .2) for ix in [order[:max(int(round(len(p) * q)), 1)]]])
    return dict(table=tab, selectivity=sel, active=active, n_study=T.attrs.get("n_study"))


def walk_forward(df, half_life, threshold, warmup=10):
    """Score each day's signals with a Coach trained only on earlier days."""
    days = sorted(df.day.unique())
    out = []
    for d in days[warmup:]:
        coach = Coach(df[df.day < d], half_life, d)
        te = df[df.day == d]
        for row in te.itertuples():
            sc = coach.score(row.features, threshold)
            out.append((row.Index, sc["exp_r"], sc["conf"], sc["verdict"]))
    res = pd.DataFrame(out, columns=["i", "exp_r", "conf", "verdict"]).set_index("i")
    return df.loc[res.index].join(res)


def _apply(T, cfg, corr_by_day, use_coach, use_pulse, policy, use_risk):
    """Weights (0 = not taken) and R for each trade under a combination of agents."""
    r = T["r_" + policy].values
    w = np.ones(len(T))
    if use_coach:
        w[T.caution.values] = 0
    if use_pulse:
        w *= T.features.map(lambda f: cfg["mults"].get(f.get("mood"), 1.0)).values
    reasons = [""] * len(T)
    if use_risk:
        rc = dict(cfg, risk_enabled=True)
        pos = {ix: k for k, ix in enumerate(T.index)}
        for d, g in T.groupby("day"):
            corr = {tuple(k.split("|")): v for k, v in corr_by_day.get(str(d), {}).items()}
            book = []
            for row in g.sort_values("ts").itertuples():
                k = pos[row.Index]
                if w[k] == 0:
                    continue
                c = dict(symbol=row.symbol, side=row.side, setup=row.setup, t=row.ts, exp_r=row.exp_r,
                         value_x=row.value_x * w[k])
                v, m, why = risk_check(c, book, rc, corr)
                reasons[k] = why[0] if v == "BLOCK" else ""
                w[k] *= m
                if v != "BLOCK":
                    book.append(dict(symbol=row.symbol, side=row.side, t=row.ts, value_x=row.value_x * w[k],
                                     t_exit=pd.Timestamp(getattr(row, "t_" + policy)), r=r[k] * w[k]))
    return r, w, reasons


def scorecard(rows, cfg, live=None, corr_by_day=None, risk_rs=1000.0, warmup=10, sentinel_ev=None, mp=None):
    """mp = model_predictions(...) for the same rows and plan. Needed for the model lab and whenever a model
    (not the rule table) is the Coach's brain; computed here if it is required and missing."""
    cfg = merged(cfg)
    if not rows or pd.DataFrame(rows).day.nunique() <= warmup + 2:
        return None
    df, W, has_pw = _prep(rows, cfg)
    if df.empty or df.day.nunique() <= warmup + 2:
        return None
    risk_frac = float(cfg["risk_pct"]) / 100.0
    df["value_x"] = [min(risk_frac * e / abs(e - s_), float(cfg["leverage"])) if abs(e - s_) > 0 else 0.0
                     for e, s_ in zip(df.entry, df.stop)]
    T = walk_forward(df, cfg["half_life"], cfg["coach_threshold"], warmup)
    if T.empty:
        return None
    T["p_bucket"], T["er_bucket"] = T.conf, T.exp_r
    brain = cfg["coach_model"] if cfg["coach_model"] in BRAINS else "bucket"
    if mp is None and brain != "bucket":
        mp = model_predictions(rows, cfg, warmup)
    if mp is not None:
        T = T.join(mp, on="key")
        T.attrs["study"], T.attrs["n_study"] = mp.attrs.get("study"), mp.attrs.get("n_study")
    if brain != "bucket" and ("p_" + brain not in T or T["p_" + brain].notna().sum() < 40):
        brain = "bucket"                                   # model not trainable on this data → rule table
    if brain != "bucket":
        ok = T["p_" + brain].notna()
        T.loc[ok, "conf"], T.loc[ok, "exp_r"] = T.loc[ok, "p_" + brain], T.loc[ok, "er_" + brain]
    min_win = float(cfg.get("coach_min_win", 0) or 0)
    T["caution"] = (T.exp_r < cfg["coach_threshold"]) | ((T.conf < min_win) if min_win > 0 else False)
    lab = model_lab(T, brain) if mp is not None else None
    corr_by_day = corr_by_day or {}
    n_days = T.day.nunique()
    per_month = len(T) / n_days * 21
    ts = T.ts.values
    base = _metrics(T.r_fixed, np.ones(len(T)), ts)
    agents = {}

    # Coach — does its expected R separate good trades from bad, and does its filter help?
    r_act = T["r_" + cfg["exit_policy"]]
    hi = T.exp_r >= T.exp_r.median()
    z_rank = _welch(r_act[hi], r_act[~hi])
    kept = ~T.caution
    m_c = _metrics(r_act, kept.astype(float).values, ts)
    m_all = _metrics(r_act, np.ones(len(T)), ts)
    agents["Coach"] = dict(evidence(z_rank, len(T)), on=cfg["coach_filter"],
                           setting=f"Brain: {BRAINS[brain]} · filter {'ON' if cfg['coach_filter'] else 'OFF'} · skip below "
                                   f"{cfg['coach_threshold']:+.2f}R" + (f" or under {min_win * 100:.0f}% win odds" if min_win > 0 else "")
                                   + f" · memory half-life {cfg['half_life']} days",
                           brain=brain, win_hi=float((r_act[hi] > 0).mean()), win_lo=float((r_act[~hi] > 0).mean()),
                           win_kept=float((r_act[kept] > 0).mean()) if kept.any() else np.nan, n_kept=int(kept.sum()),
                           effect=m_c["exp"] - m_all["exp"], effect_label="per trade if the filter is on",
                           detail=f"Judged on the plan that is executed ({cfg['exit_policy']} exit, "
                                  f"{WINDOW_TEXT.get(W, str(W) + ' min').lower()}). Top half of its picks won "
                                  f"{(r_act[hi] > 0).mean() * 100:.0f}% and averaged {r_act[hi].mean():+.2f}R vs bottom half "
                                  f"{(r_act[~hi] > 0).mean() * 100:.0f}% and {r_act[~hi].mean():+.2f}R. Filter would skip "
                                  f"{int((~kept).sum())} of {len(T)} trades (they averaged "
                                  f"{r_act[~kept].mean() if (~kept).any() else float('nan'):+.2f}R).")
    if r_act[hi].mean() < 0:
        c_ = agents["Coach"]
        c_.update(score=min(c_["score"], 50) if c_.get("score") is not None else None, tone="mid",
                  grade="Separates bad from worse — even its best picks lose money")
    # Pulse — are reduced-size trades really worse?
    w_p = T.features.map(lambda f: cfg["mults"].get(f.get("mood"), 1.0)).values
    full = w_p >= w_p.max() - 1e-9
    m_p = _metrics(T.r_fixed, w_p, ts)
    agents["Pulse"] = dict(evidence(_welch(T.r_fixed[full], T.r_fixed[~full]), int(min(full.sum(), (~full).sum())) * 2),
                           on=cfg["pulse_sizing"], setting="Sizing " + ("ON · " + ", ".join(
                               f"{k} ×{v:g}" for k, v in cfg["mults"].items() if v != 1) if cfg["pulse_sizing"] else "OFF"),
                           effect=m_p["exp"] - base["exp"], effect_label="per unit of risk vs equal sizing",
                           detail=f"Full-size moods averaged {T.r_fixed[full].mean():+.2f}R; reduced-size moods "
                                  f"{T.r_fixed[~full].mean() if (~full).any() else float('nan'):+.2f}R. "
                                  f"With-market trades {T.r_fixed[T.features.map(lambda f: f.get('market')) == 'with market'].mean():+.2f}R vs "
                                  f"against {T.r_fixed[T.features.map(lambda f: f.get('market')) == 'against market'].mean():+.2f}R.")
    # Exit — paired comparison of every policy against the fixed exit on the same trades
    ex_rows = []
    for p, desc in EXIT_POLICIES.items():
        d = T["r_" + p] - T.r_fixed
        ex_rows.append(dict(policy=p, desc=desc, exp=T["r_" + p].mean(), vs_fixed=d.mean(), z=_paired(d),
                            win=(T["r_" + p] > 0).mean(), n=len(T)))
    ex_tab = pd.DataFrame(ex_rows)
    cur = ex_tab[ex_tab.policy == cfg["exit_policy"]].iloc[0]
    best = ex_tab.sort_values("exp", ascending=False).iloc[0]
    alts = ex_tab[ex_tab.policy != "fixed"].sort_values("exp", ascending=False)
    if cfg["exit_policy"] != "fixed":
        ev_x = evidence(cur.z, len(T))
        det = f"Active policy averages {cur.exp:+.2f}R per trade vs fixed {T.r_fixed.mean():+.2f}R on the same trades."
    elif alts.iloc[0].z < 1:
        ev_x = dict(evidence(0.0, len(T)), grade="Fixed exit is best so far", tone="good")
        det = (f"Every alternative did worse on the same trades (from {alts.iloc[0].vs_fixed:+.2f}R to "
               f"{alts.iloc[-1].vs_fixed:+.2f}R per trade), so the Exit agent keeps the fixed exit.")
    else:
        ev_x = evidence(alts.iloc[0].z, len(T))
        det = f"{alts.iloc[0].policy} beats fixed by {alts.iloc[0].vs_fixed:+.2f}R per trade — see proposals."
    agents["Exit"] = dict(ev_x, on=True, setting=f"Policy: {cfg['exit_policy']}", effect=cur.vs_fixed,
                          effect_label="per trade vs fixed exit", detail=det)
    # Risk — daily P&L and drawdown with and without the rules
    r_r, w_r, why_r = _apply(T, cfg, corr_by_day, False, False, "fixed", True)
    m_r = _metrics(r_r, w_r, ts)
    daily = pd.DataFrame({"day": T.day.values, "with": r_r * w_r, "without": T.r_fixed.values}).groupby("day").sum()
    blocked = pd.Series(why_r, index=T.index)
    cap_hits = T.r_fixed[blocked.str.startswith("Already")]
    agents["Risk"] = dict(evidence(_paired(daily["with"] - daily["without"]), len(daily) * 3), on=cfg["risk_enabled"],
                          setting=f"{'ON' if cfg['risk_enabled'] else 'OFF'} · loss cap −{cfg['daily_loss_R']:g}R · "
                                  f"max {cfg['max_trades']} trades, {cfg['max_open']} open · one position per stock · "
                                  f"exposure ≤ {cfg['max_exposure_x']:g}× capital · corr ≥ {cfg['corr_limit']:g} halves size",
                          effect=(m_r["total"] - base["total"]) / n_days, effect_label="per day vs no risk rules",
                          dd_change=m_r["maxdd"] - base["maxdd"],
                          detail=f"Max drawdown {base['maxdd']:+.1f}R → {m_r['maxdd']:+.1f}R. Blocked "
                                 f"{int((blocked != '').sum())} trades; those blocked by the trade cap averaged "
                                 f"{cap_hits.mean() if len(cap_hits) else float('nan'):+.2f}R.",
                          cap_hits=cap_hits.values.tolist())
    # Sentinel — only measurable on live trades (historical headlines aren't available)
    lv = pd.DataFrame(live) if live is not None and len(live) else pd.DataFrame()
    if not lv.empty:
        lv = lv[lv.r.notna()]
        lv["news"] = lv.features.map(lambda f: _feats(f).get("news"))
    ev_r = lv[lv.news == "event"].r if not lv.empty else pd.Series(dtype=float)
    cl_r = lv[lv.news == "clear"].r if not lv.empty else pd.Series(dtype=float)
    agents["Sentinel"] = dict(evidence(_welch(cl_r, ev_r), int(min(len(ev_r), len(cl_r))) * 2), on=True,
                              setting="Block HIGH-risk: " + ("ON" if cfg["sentinel_block"] else "OFF (warn only)"),
                              effect=(cl_r.mean() - ev_r.mean()) if len(ev_r) and len(cl_r) else np.nan,
                              effect_label="per trade: clear-news minus flagged trades",
                              detail=f"Measured on live signals only (past headlines aren't available): {len(ev_r)} flagged, "
                                     f"{len(cl_r)} clear so far. Needs about 10 of each before it can be judged.")
    # Paper Trader — is the execution window helping or cutting winners short?
    win_tab = None
    if has_pw:
        cur_w = T[f"w_{W}"]
        rows_w = []
        for w in WINDOWS:
            d = T[f"w_{w}"] - cur_w
            rows_w.append(dict(window=w, label=WINDOW_TEXT[w], exp=T[f"w_{w}"].mean(), vs_current=d.mean(),
                               z=_paired(d), win=(T[f"w_{w}"] > 0).mean(), n=len(T)))
        win_tab = pd.DataFrame(rows_w)
        hold = T["w_0"]
        dz = _paired(cur_w - hold) if W else 0.0
        agents["Trader"] = dict(evidence(dz, len(T)) if W else dict(evidence(0.0, len(T)), grade="Holding to the plan"),
                                on=True, setting=f"Execution window: {WINDOW_TEXT[W]} · exit policy {cfg['exit_policy']}",
                                effect=(cur_w - hold).mean() if W else 0.0, effect_label="per trade vs holding to the plan",
                                detail=f"Closing at the window averaged {cur_w.mean():+.2f}R per trade vs {hold.mean():+.2f}R "
                                       f"if held to stop/target/15:15. Best window tested: "
                                       f"{win_tab.sort_values('exp', ascending=False).iloc[0].label} "
                                       f"({win_tab.exp.max():+.2f}R).")
    agents["Narrator"] = dict(score=None, grade="Informational — doesn't change trades", tone="mid", n=None, z=None,
                              on=True, setting="Explains each signal", effect=np.nan, effect_label="", detail=
                              "Measured by whether you find it useful, not by profit.")
    ev = sentinel_ev
    if ev and (ev.get("n_flag", 0) or ev.get("scanned", 0)):
        # Judged on market behaviour, not trades: do stocks really move more on days the Sentinel flags?
        by = ev["by_type"]
        top = (" Strongest: " + "; ".join(f"{r_.label} {r_.ratio:.2f}× (n={int(r_.n)})" for r_ in by.head(3).itertuples())
               + ".") if len(by) else ""
        ev_s = evidence(ev["z"], ev["n_flag"], min_n=15)
        if ev_s["score"] is None:
            ev_s["grade"] = f"Too few flagged days to judge ({ev['n_flag']} so far, needs 15)"
        agents["Sentinel"] = dict(ev_s, on=True, n_label="flagged stock-days",
                                  setting="Block HIGH-risk: " + ("ON" if cfg["sentinel_block"] else "OFF (warn only)"),
                                  effect=(ev["ratio_flag"] - ev["ratio_base"]) if ev["n_flag"] else np.nan,
                                  effect_unit="×", effect_label="normal daily range on flagged days vs other days",
                                  detail=(f"{ev['n_flag']} flagged stock-days moved {ev['ratio_flag']:.2f}× their normal range vs "
                                          f"{ev['ratio_base']:.2f}× on {ev['n_base']} other stock-days.{top} "
                                          if ev["n_flag"] else "No flagged days in the window yet. ")
                                         + f"Archive: {ev['scanned']} stock-days scanned over {ev['days']} day(s); "
                                           f"past results days are back-filled from the exchange calendar when it is reachable.",
                                  no_rs=True)
    for a_ in agents.values():
        if a_.get("no_rs"):
            a_["rs_month"] = np.nan
            continue
        a_["rs_month"] = (a_["effect"] * (n_days if a_ is agents["Risk"] else len(T)) / n_days * 21 * risk_rs
                          if pd.notna(a_.get("effect")) else np.nan)

    # Combined system with the active configuration
    r_s, w_s, _ = _apply(T, cfg, corr_by_day, cfg["coach_filter"], cfg["pulse_sizing"], cfg["exit_policy"], cfg["risk_enabled"])
    system = _metrics(r_s, w_s, ts)
    curves = pd.DataFrame({"ts": T.ts.values, "Baseline (no agents)": T.r_fixed.values,
                           "With agents (active config)": r_s * w_s}).sort_values("ts")
    curves[["Baseline (no agents)", "With agents (active config)"]] = curves[
        ["Baseline (no agents)", "With agents (active config)"]].cumsum()
    by_setup = {}
    for s_, g in T.sort_values("ts").groupby("setup"):
        rest = T[T.setup != s_].r_fixed
        last = g.tail(20).r_fixed
        by_setup[s_] = dict(n=len(g), exp=g.r_fixed.mean(), last20=last.mean(), z_last=_welch(last, T.r_fixed))
    return dict(cfg=cfg, base=base, system=system, agents=agents, exits=ex_tab, windows=win_tab, curves=curves, days=n_days,
                trades=len(T), per_month=per_month, risk_rs=risk_rs, by_setup=by_setup, lab=lab,
                losing=bool(base["exp"] < 0 and system["exp"] < 0),
                period=(str(T.day.min()), str(T.day.max())), warmup=warmup)


def proposals(sc, cfg):
    """Changes the agents suggest from the evidence. Nothing changes until a human approves."""
    cfg = merged(cfg)
    P = []

    def add(agent, title, why, change):
        cid = agent + ":" + json.dumps(change, sort_keys=True)
        P.append(dict(id=cid, agent=agent, title=title, why=why, change=change))
    ex = sc["exits"].set_index("policy")
    cur = cfg["exit_policy"]
    good = ex[(ex.z >= 1.5) & (ex.index != cur)]
    if len(good):
        b = good.sort_values("exp", ascending=False).iloc[0]
        if b.exp - ex.loc[cur].exp >= 0.03:
            add("Exit", f"Switch exit policy: {cur} → {b.name}",
                f"{EXIT_POLICIES[b.name]}. Over {int(b.n)} walk-forward trades it averaged {b.exp:+.2f}R vs "
                f"{ex.loc[cur].exp:+.2f}R for {cur} (evidence z={b.z:.1f}).", {"exit_policy": b.name})
    if cur != "fixed" and ex.loc[cur].z <= -1:
        add("Exit", f"Revert exit policy to fixed", f"{cur} is now doing worse than fixed exits "
            f"({ex.loc[cur].vs_fixed:+.2f}R per trade, z={ex.loc[cur].z:.1f}).", {"exit_policy": "fixed"})
    co = sc["agents"]["Coach"]
    if not cfg["coach_filter"] and (co.get("z") or 0) >= 1.5 and co["effect"] >= 0.02:
        add("Coach", "Turn on the Coach filter", f"Its top-half picks clearly beat its bottom half (z={co['z']:.1f}) "
            f"and skipping trades below {cfg['coach_threshold']:+.2f}R expected would add {co['effect']:+.2f}R per trade.",
            {"coach_filter": True})
    if cfg["coach_filter"] and ((co.get("z") or 0) < 0.5 or co["effect"] < 0):
        add("Coach", "Turn off the Coach filter", f"Its predictions no longer separate winners from losers "
            f"(z={co.get('z') or 0:.1f}, filter effect {co['effect']:+.2f}R per trade).", {"coach_filter": False})
    lab = sc.get("lab")
    if lab is not None and len(lab["table"]):
        lt = lab["table"].set_index("kind")
        act = lab["active"]
        a_ = lt.loc[act] if act in lt.index else None
        if a_ is not None:
            # a challenger must show skill on the wide study set (many trades) AND beat the active brain on real signals
            ch = lt[(lt.index != act) & (lt.index != "bucket") & (lt.auc_wide >= 0.54) & (lt.z_wide >= 2.0)
                    & (lt.auc >= 0.53) & (lt.auc >= a_.auc + 0.02) & (lt.win_top50 > a_.win_top50)
                    & (lt.r_top50 > a_.r_top50)]
            if act != "bucket":                            # between models, switch only on a clear gap (no flip-flopping)
                ch = ch[ch.auc_wide >= a_.auc_wide + 0.03]
            if len(ch):
                b = ch.sort_values("auc_wide", ascending=False).iloc[0]
                add("Coach", f"Switch the Coach's brain: {BRAINS[act]} → {b.label}",
                    f"On {int(b.n)} walk-forward trades its top-half picks won {b.win_top50 * 100:.0f}% and averaged "
                    f"{b.r_top50:+.2f}R, vs {a_.win_top50 * 100:.0f}% and {a_.r_top50:+.2f}R now. Ranking skill (AUC) "
                    f"{b.auc:.2f} vs {a_.auc:.2f} (0.50 = guessing), and {b.auc_wide:.2f} across {int(b.n_wide)} wider "
                    f"practice signals where its top 30% won {b.win_wide_top30 * 100:.0f}% vs {b.win_wide * 100:.0f}% overall.",
                    {"coach_model": b.name})
            if act != "bucket" and (a_.auc_wide < 0.51 or (a_.auc < 0.49 and a_.z < 0)):
                add("Coach", f"Return the Coach's brain to the rule table",
                    f"{BRAINS[act]} no longer separates winners from losers (AUC {a_.auc:.2f} on real signals, "
                    f"{a_.auc_wide:.2f} on practice signals; 0.50 = guessing).", {"coach_model": "bucket", "coach_min_win": 0.0})
    pu = sc["agents"]["Pulse"]
    if cfg["pulse_sizing"] and pu.get("score") is not None and pu["z"] <= -1.0:
        add("Pulse", "Turn off mood-based sizing", f"Trades it shrinks have actually done better than full-size ones "
            f"(z={pu['z']:.1f}), so cutting their size is costing profit.", {"pulse_sizing": False})
    if not cfg["pulse_sizing"] and pu.get("score") is not None and pu["z"] >= 1.5:
        add("Pulse", "Turn on mood-based sizing", f"Trades in weaker moods have done clearly worse than the rest "
            f"(z={pu['z']:.1f}); shrinking them would protect profit.", {"pulse_sizing": True})
    ri = sc["agents"]["Risk"]
    caps = np.asarray(ri.get("cap_hits", []), float)
    if cfg["risk_enabled"] and len(caps) >= 15 and caps.mean() > 0.1 and _paired(caps) >= 1.5:
        add("Risk", f"Raise the daily trade cap to {cfg['max_trades'] + 1}",
            f"{len(caps)} trades blocked by the cap averaged {caps.mean():+.2f}R (z={_paired(caps):.1f}).",
            {"max_trades": cfg["max_trades"] + 1})
    for s_, v in sc["by_setup"].items():
        if s_ not in cfg["paused_setups"] and v["n"] >= 30 and v["last20"] < -0.25 and v["z_last"] <= -1.5:
            add("Risk", f"Pause the {s_} setup", f"Its last 20 trades averaged {v['last20']:+.2f}R, well below normal "
                f"(z={v['z_last']:.1f}) — it may have stopped working in this market.",
                {"paused_setups": sorted(set(cfg["paused_setups"]) | {s_})})
        if s_ in cfg["paused_setups"] and v["last20"] > 0.1:
            add("Risk", f"Resume the {s_} setup", f"While paused, its simulated last 20 trades averaged {v['last20']:+.2f}R.",
                {"paused_setups": sorted(set(cfg["paused_setups"]) - {s_})})
    wt = sc.get("windows")
    if wt is not None and len(wt):
        cw = int(cfg.get("paper_window_min", 0) or 0)
        good = wt[(wt.z >= 1.5) & (wt.window != cw) & (wt.vs_current >= 0.03)]
        if len(good):
            b = good.sort_values("exp", ascending=False).iloc[0]
            add("Trader", f"Change execution window: {WINDOW_TEXT[cw]} → {b.label}",
                f"On {int(b.n)} replayed recommendations, closing after {b.label.lower()} averaged {b.exp:+.2f}R vs "
                f"{wt[wt.window == cw].exp.iloc[0]:+.2f}R now (z={b.z:.1f}).", {"paper_window_min": int(b.window)})
    return P


# ============================================================================ PAPER TRADER
def paper_trade(g, sd, a, policy, window_min, qty, idx_day=None, risk_rs=None):
    """Execute one recommendation on paper: fill at the entry bar's open, manage it with the approved exit policy,
    close at stop / target / end of execution window / 15:15. Also measures what happened during and after."""
    t = g.index
    i0, side, entry, stop, target = sd["i"], sd["side"], sd["entry"], sd["stop"], sd["target"]
    risk = abs(entry - stop)
    t0 = t[i0]
    deadline = t0 + pd.Timedelta(minutes=window_min) if window_min else None
    sim = simulate_exit(g, sd, policy, a, deadline)
    hold = simulate_exit(g, sd, policy, a)                     # same plan without the window
    closed = sim["reason"] != "close"
    if closed:
        r, px, tx = sim["r"], sim["exit"], sim["t"]
    else:                                                      # still running: mark to market
        px, tx = float(g.Close.iloc[-1]), t[-1]
        r = (px - entry) * side / risk - entry * a.cost_pct / 100 / risk if risk else 0.0
    # market move while the trade was on (Nifty, % in the trade's direction)
    mkt = np.nan
    if idx_day is not None and not idx_day.empty:
        a_ = idx_day[idx_day.index <= t0]
        b_ = idx_day[idx_day.index <= tx]
        if len(a_) and len(b_):
            mkt = (b_.Close.iloc[-1] / a_.Close.iloc[-1] - 1) * 100 * side
    # volume during the trade vs the session before entry
    during = g.Volume.iloc[i0:max(g.index.get_loc(tx), i0 + 1)]
    before = g.Volume.iloc[:i0]
    vol_ratio = float(during.mean() / before.mean()) if len(before) and before.mean() > 0 and len(during) else np.nan
    # after a stop or window close: did price go on to reach the target before 15:15?
    later_target = False
    if closed and sim["reason"] in ("stop", "window"):
        after = g[(g.index > tx) & (g.index.time < dtime(15, 15))]
        if len(after):
            later_target = bool((after.High >= target).any() if side == 1 else (after.Low <= target).any())
    return dict(status="CLOSED" if closed else "OPEN", entry_time=t0, deadline=deadline, exit_time=tx if closed else None,
                exit_price=float(px) if closed else None, last_price=float(g.Close.iloc[-1]), reason=sim["reason"] if closed else None,
                r=float(r), pnl=float(r * risk * qty), mfe=sim["mfe"], mae=sim["mae"], mkt_move=float(mkt) if pd.notna(mkt) else None,
                vol_ratio=vol_ratio if pd.notna(vol_ratio) else None, hold_r=float(hold["r"]) if hold["reason"] != "close" else None,
                later_target=later_target, note=sim["note"], stop_now=sim["stop_now"], minutes=int((tx - t0).total_seconds() // 60))


def review_factors(rec):
    """What drove this paper trade's result — the Analyst's review. Each factor: kind (pos/neg), code, text."""
    F = []
    d = rec
    snap = d.get("snapshot") or {}
    feats = snap.get("features") or {}

    def add(kind, code, text):
        F.append(dict(kind=kind, code=code, text=text))
    mm = d.get("mkt_move")
    if mm is not None:
        if mm > 0.1:
            add("pos", "mkt_with", f"Market moved with the trade (Nifty {mm:+.2f}% in its direction)")
        elif mm < -0.1:
            add("neg", "mkt_against", f"Market moved against the trade (Nifty {mm:+.2f}% in its direction)")
    mfe, mae = d.get("mfe") or 0, d.get("mae") or 0
    if mfe >= 1:
        add("pos", "follow_through", f"Strong follow-through — reached +{mfe:.1f}R at best")
    elif d.get("status") == "CLOSED" and mfe < 0.3:
        add("neg", "no_follow", f"No follow-through — never got beyond +{mfe:.1f}R")
    if mae <= 0.2 and d.get("status") == "CLOSED":
        add("pos", "clean_entry", f"Clean entry — went only −{mae:.1f}R against at worst")
    elif mae >= 0.7 and (d.get("r") or 0) > 0:
        add("neg", "deep_pullback", f"Entry timing — went −{mae:.1f}R against before working")
    vr = d.get("vol_ratio")
    if vr is not None:
        if vr >= 1.3:
            add("pos", "vol_strong", f"Volume stayed strong during the trade ({vr:.1f}× the session so far)")
        elif vr <= 0.7:
            add("neg", "vol_faded", f"Volume faded after entry ({vr:.1f}× the session so far)")
    hr, r = d.get("hold_r"), d.get("r") or 0
    if d.get("reason") == "window" and hr is not None:
        if hr - r >= 0.3:
            add("neg", "window_short", f"Window too short — closed at {r:+.2f}R; holding to the plan gave {hr:+.2f}R")
        elif hr - r <= -0.3:
            add("pos", "window_saved", f"Window protected the result — holding to the plan gave {hr:+.2f}R")
    if d.get("reason") == "stop" and d.get("later_target"):
        add("neg", "stop_tight", "Stop too tight — price hit the stop, then reached the target later")
    if d.get("reason") == "target":
        add("pos", "target_hit", f"Target reached in {d.get('minutes', 0)} minutes")
    cond = {("market", "with market"): ("pos", "c_with_mkt"), ("market", "against market"): ("neg", "c_against_mkt"),
            ("chase", "extended"): ("neg", "c_chased"), ("chase", "near"): ("pos", "c_near_vwap"),
            ("timing", "late"): ("neg", "c_late"), ("news", "event"): ("neg", "c_news"),
            ("volume", "strong"): ("pos", "c_vol_strong"), ("volume", "weak"): ("neg", "c_vol_weak"),
            ("range", "wide"): ("neg", "c_wide_range"), ("mood", "Volatile"): ("neg", "c_volatile")}
    for (f, b), (kind, code) in cond.items():
        if feats.get(f) == b:
            add(kind, code, "At entry: " + FEATURE_TEXT.get(f, {}).get(b, b).lower())
    if snap.get("risk_verdict") == "REDUCE":
        add("neg" if r < 0 else "pos", "risk_reduced", "Risk agent had reduced the size: " + (snap.get("risk_why") or [""])[0])
    return F


def driver_table(log):
    """Across closed paper trades: for every factor, how often it appeared and the average result when it did."""
    c = log[(log.status == "CLOSED") & log.r.notna()] if not log.empty else log
    rows = []
    for rec in c.itertuples():
        for f in (rec.factors or []):
            rows.append(dict(code=f["code"], kind=f["kind"], text=f["text"].split(" — ")[0].split(" (")[0], r=rec.r,
                             win=rec.r > 0))
    if not rows:
        return pd.DataFrame(columns=["code", "kind", "factor", "n", "wins", "losses", "avg_r", "total_r"])
    x = pd.DataFrame(rows)
    out = x.groupby(["code", "kind"]).agg(factor=("text", "first"), n=("r", "size"), wins=("win", "sum"),
                                          avg_r=("r", "mean"), total_r=("r", "sum")).reset_index()
    out["losses"] = out.n - out.wins
    return out.sort_values("total_r")


def analyst_summary(log, cfg, api_key=None):
    c = log[(log.status == "CLOSED") & log.r.notna()] if not log.empty else log
    if c.empty:
        return "No closed paper trades yet. The Analyst reviews each one as soon as it closes."
    n, wr, tot, avg = len(c), (c.r > 0).mean() * 100, c.r.sum(), c.r.mean()
    pnl = c.pnl.sum()
    parts = [f"{n} paper trades closed: {wr:.0f}% won, {tot:+.1f}R in total ({avg:+.2f}R per trade, "
             f"{'₹' if pnl >= 0 else '−₹'}{abs(pnl):,.0f})."]
    dt = driver_table(log)
    dt = dt[dt.n >= 3]
    if len(dt):
        worst, best = dt.iloc[0], dt.iloc[-1]
        if worst.total_r < 0:
            parts.append(f"Biggest drag: {worst.factor.lower()} — {int(worst.n)} trades averaging {worst.avg_r:+.2f}R.")
        if best.total_r > 0:
            parts.append(f"Biggest help: {best.factor.lower()} — {int(best.n)} trades averaging {best.avg_r:+.2f}R.")
    w = c[(c.reason == "window") & c.hold_r.notna()]
    if len(w) >= 3:
        diff = (w.hold_r - w.r).mean()
        parts.append(f"Window closes: holding to the plan would have changed the result by {diff:+.2f}R per trade "
                     f"over {len(w)} trades ({'consider a longer window' if diff > 0.15 else 'consider a shorter window' if diff < -0.15 else 'window looks about right'}).")
    sk = log[(log.status == "SKIPPED") & log.r.notna()] if "status" in log else pd.DataFrame()
    if len(sk) >= 3:
        parts.append(f"Signals the agents skipped would have averaged {sk.r.mean():+.2f}R ({len(sk)} trades) — "
                     + ("the skips are saving money." if sk.r.mean() < avg else "the skips are costing money."))
    text = " ".join(parts)
    if api_key:
        better = ask_claude("You are the Analyst agent reviewing a paper-trading log for intraday NSE recommendations. "
                            "Rewrite these findings as 3-4 short plain-English sentences for the human reviewer, keep every "
                            "number, end with one concrete thing to watch. No buy/sell advice.\n\n" + text, api_key, 220)
        if better:
            return better
    return text


# ============================================================================ NARRATOR
def narrate(row, mood, sent, coach_score, api_key=None):
    side = "long" if row.side == 1 else "short"
    trig = ("closed beyond the 15-minute opening range with rising volume while price was on the right side of VWAP"
            if row.setup == "ORB" else
            "pulled back to VWAP in an established trend and held")
    parts = [f"{row.symbol} {trig}, so the engine took a {side} at ₹{row.entry:,.2f} "
             f"with the stop at ₹{row.stop:,.2f} and a {abs(row.target - row.entry) / abs(row.entry - row.stop):.0f}R target."]
    if mood:
        aligned = (mood["bias"] == row.side)
        parts.append(f"Market mood is {mood['regime'].lower()}" +
                     (", which supports this direction." if aligned else
                      ", which works against this direction." if mood["bias"] == -row.side else "."))
    if sent and sent["level"] != "CLEAR":
        parts.append(f"News check: {sent['reasons'][0].rstrip('.')}.")
    if coach_score and coach_score.get("conf") is not None:
        c = coach_score
        if c["drivers"]:
            worst = c["drivers"][0]
            if worst[0] < 0:
                parts.append(f"Biggest risk from past trades: {worst[1].lower()} (won {worst[3] * 100:.0f}% of {worst[2]}).")
            best = c["drivers"][-1]
            if best[0] > 0:
                parts.append(f"In its favour: {best[1].lower()} (won {best[3] * 100:.0f}% of {best[2]}).")
    text = " ".join(parts)
    if api_key:
        better = ask_claude("Rewrite this for a retail intraday trader in 2-3 short sentences, plain English, "
                            "keep every number, no advice to buy or sell:\n\n" + text, api_key, 180)
        if better:
            return better
    return text


def cost_burden(rows, cfg):
    """How much of each trade's risk goes to costs: cost% ÷ stop distance%. Returns a dict or None."""
    if not rows:
        return None
    t = pd.DataFrame([dict(entry=r["entry"], stop=r["stop"], r=r["r"]) for r in rows])
    t["stop_pct"] = (t.entry - t.stop).abs() / t.entry * 100
    t = t[t.stop_pct > 0]
    if t.empty:
        return None
    t["cost_r"] = float(cfg["cost_pct"]) / t.stop_pct
    tight, wide = t[t.stop_pct < 0.8], t[t.stop_pct >= 0.8]
    return dict(n=len(t), median_cost_r=float(t.cost_r.median()), mean_cost_r=float(t.cost_r.mean()),
                share_tight=float(len(tight) / len(t)), tight_n=len(tight), wide_n=len(wide),
                tight_r=float(tight.r.mean()) if len(tight) else np.nan, wide_r=float(wide.r.mean()) if len(wide) else np.nan,
                median_stop=float(t.stop_pct.median()))


def cost_proposal(rows, cfg, level=0.8):
    """Propose a minimum stop distance when costs eat a large share of the risk on tight-stop signals."""
    c = merged(cfg)
    if float(c.get("min_stop_pct", 0) or 0) >= level or c.get("panel"):
        return None
    b = cost_burden(rows, c)
    if not b or b["median_cost_r"] < 0.15 or b["tight_n"] < 15:
        return None
    worse = pd.notna(b["wide_r"]) and b["wide_n"] >= 10 and b["tight_r"] < b["wide_r"]
    if not (worse or b["median_cost_r"] >= 0.25):
        return None
    why = (f"Costs are {c['cost_pct']:g}% of the trade value, so a tight stop makes them a big share of the risk: on "
           f"{b['n']} recent signals the median stop was {b['median_stop']:.2f}% away and costs took a median "
           f"{b['median_cost_r']:.2f}R per trade before the market moved at all. {b['share_tight'] * 100:.0f}% of signals had "
           f"stops under {level:g}% and averaged {b['tight_r']:+.2f}R"
           + (f", vs {b['wide_r']:+.2f}R for the {b['wide_n']} with wider stops" if b["wide_n"] else "")
           + f". Skipping stops under {level:g}% keeps costs at about {c['cost_pct'] / level:.2f}R per trade or less.")
    return dict(id=f"Costs:min_stop:{level}", agent="Risk", title=f"Skip signals with stops closer than {level:g}%",
                why=why, change={"min_stop_pct": level})

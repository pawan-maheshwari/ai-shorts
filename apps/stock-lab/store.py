"""
Storage for Stock Lab agents: audit events (kept 7 days) and the trade journal (kept 90 days).

Backends
  • Supabase (persistent) — used when Streamlit secrets contain SUPABASE_URL and SUPABASE_KEY.
  • Local SQLite (fallback) — works immediately but is wiped whenever the hosted app restarts or redeploys.

Supabase setup (run once in the Supabase SQL editor) — see SUPABASE_SQL below.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

EVENT_DAYS = 7
TRADE_DAYS = 90

SUPABASE_SQL = """
create table if not exists stocklab_events (
  id bigserial primary key,
  ts timestamptz not null default now(),
  dkey text unique,
  agent text, kind text, symbol text, message text, detail jsonb
);
create index if not exists stocklab_events_ts on stocklab_events (ts desc);

create table if not exists stocklab_trades (
  key text primary key,
  ts timestamptz, day date, source text, symbol text, setup text, side int,
  entry float8, stop float8, target float8, exit float8, r float8,
  status text, features jsonb, reason text, updated timestamptz default now()
);
create index if not exists stocklab_trades_day on stocklab_trades (day desc);

create table if not exists stocklab_kv (
  key text primary key, value jsonb, updated timestamptz default now()
);

create table if not exists stocklab_paper (
  key text primary key, day date, symbol text, status text,
  rec_time timestamptz, logged_at timestamptz, data jsonb, human jsonb, updated timestamptz default now()
);
create index if not exists stocklab_paper_day on stocklab_paper (day desc);

create table if not exists stocklab_sentinel (
  key text primary key, day date, symbol text, level text, data jsonb, updated timestamptz default now()
);
create index if not exists stocklab_sentinel_day on stocklab_sentinel (day desc);

alter table stocklab_events enable row level security;
alter table stocklab_sentinel enable row level security;
alter table stocklab_paper enable row level security;
alter table stocklab_kv enable row level security;
alter table stocklab_trades enable row level security;
-- The app uses the service-role key from Streamlit secrets (server side only), which bypasses RLS.
"""

TRADE_COLS = ["key", "ts", "day", "source", "symbol", "setup", "side", "entry", "stop", "target", "exit", "r",
              "status", "features", "reason", "updated"]


def _now():
    return datetime.now(timezone.utc)


def _iso(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    if isinstance(x, pd.Timestamp):
        x = x.to_pydatetime()
    if isinstance(x, datetime):
        if x.tzinfo is None:
            x = x.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))  # bars are IST wall time
        return x.astimezone(timezone.utc).isoformat()
    return str(x)


def _clean(v):
    if isinstance(v, (float, int)) and not isinstance(v, bool) and pd.isna(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


class Store:
    def __init__(self, url=None, key=None, path=None):
        self.url = (url or "").rstrip("/")
        self.key = key
        self.url = (self.url or "").strip().rstrip("/")
        self.key = (self.key or "").strip()
        self.remote = bool(self.url and self.key)
        self._lock = threading.Lock()
        if not self.remote:
            path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".data", "stocklab.db")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.db = sqlite3.connect(path, check_same_thread=False)
            self.db.executescript("""
            create table if not exists stocklab_events (id integer primary key autoincrement, ts text, dkey text unique,
              agent text, kind text, symbol text, message text, detail text);
            create table if not exists stocklab_trades (key text primary key, ts text, day text, source text,
              symbol text, setup text, side integer, entry real, stop real, target real, exit real, r real,
              status text, features text, reason text, updated text);
            create table if not exists stocklab_kv (key text primary key, value text, updated text);
            create table if not exists stocklab_sentinel (key text primary key, day text, symbol text, level text,
              data text, updated text);
            create table if not exists stocklab_paper (key text primary key, day text, symbol text, status text,
              rec_time text, logged_at text, data text, human text, updated text);
            """)
        self.last_error = None

    @property
    def kind(self):
        return "Supabase (persistent)" if self.remote else "Temporary (resets when the app restarts)"

    # ------------------------------------------------------------------ REST helpers
    def _h(self, extra=None):
        h = {"apikey": self.key, "Content-Type": "application/json"}
        if self.key.startswith("eyJ"):          # legacy service_role JWT; new sb_secret_ keys go in apikey only
            h["Authorization"] = f"Bearer {self.key}"
        h.update(extra or {})
        return h

    def _post(self, table, rows, prefer, on_conflict=None):
        q = f"?on_conflict={on_conflict}" if on_conflict else ""
        r = requests.post(f"{self.url}/rest/v1/{table}{q}", headers=self._h({"Prefer": prefer}),
                          data=json.dumps(rows, default=str), timeout=15)
        if r.status_code >= 300:
            self.last_error = f"{table}: {r.status_code} {r.text[:160]}"

    def _get(self, table, params):
        r = requests.get(f"{self.url}/rest/v1/{table}", headers=self._h(), params=params, timeout=15)
        if r.status_code >= 300:
            self.last_error = f"{table}: {r.status_code} {r.text[:160]}"
            return []
        return r.json()

    # ------------------------------------------------------------------ events
    def log(self, agent, kind, message, symbol=None, detail=None, dkey=None, ts=None):
        """Write one audit event. dkey makes it idempotent (same dkey is stored once)."""
        row = dict(ts=_iso(ts) or _now().isoformat(), dkey=dkey, agent=agent, kind=kind, symbol=symbol,
                   message=message, detail=detail or {})
        try:
            if self.remote:
                self._post("stocklab_events", [row], "resolution=ignore-duplicates,return=minimal", "dkey")
            else:
                with self._lock:
                    self.db.execute("insert or ignore into stocklab_events (ts,dkey,agent,kind,symbol,message,detail)"
                                    " values (?,?,?,?,?,?,?)", (row["ts"], dkey, agent, kind, symbol, message,
                                                                 json.dumps(row["detail"], default=str)))
                    self.db.commit()
        except Exception as e:  # never break the app over logging
            self.last_error = str(e)

    def events(self, days=EVENT_DAYS):
        since = (_now() - timedelta(days=days)).isoformat()
        try:
            if self.remote:
                rows = self._get("stocklab_events", {"select": "ts,agent,kind,symbol,message,detail",
                                                     "ts": f"gte.{since}", "order": "ts.desc", "limit": "5000"})
                df = pd.DataFrame(rows)
            else:
                with self._lock:
                    df = pd.read_sql_query("select ts,agent,kind,symbol,message,detail from stocklab_events "
                                           "where ts >= ? order by ts desc limit 5000", self.db, params=(since,))
                df["detail"] = df.detail.map(lambda s: json.loads(s) if s else {})
        except Exception as e:
            self.last_error = str(e)
            df = pd.DataFrame()
        if df.empty:
            return pd.DataFrame(columns=["ts", "agent", "kind", "symbol", "message", "detail"])
        df["ts"] = pd.to_datetime(df.ts, utc=True, format="ISO8601").dt.tz_convert("Asia/Kolkata")
        return df

    # ------------------------------------------------------------------ trades
    def upsert_trades(self, rows):
        if not rows:
            return
        clean = []
        for r in rows:
            r = {k: _clean(r.get(k)) for k in TRADE_COLS}
            r["ts"] = _iso(r["ts"])
            r["day"] = str(r["day"]) if r["day"] is not None else None
            r["updated"] = _now().isoformat()
            r["features"] = r["features"] or {}
            clean.append(r)
        try:
            if self.remote:
                for i in range(0, len(clean), 500):
                    self._post("stocklab_trades", clean[i:i + 500], "resolution=merge-duplicates,return=minimal", "key")
            else:
                with self._lock:
                    self.db.executemany(
                        f"insert or replace into stocklab_trades ({','.join(TRADE_COLS)}) values ({','.join('?' * len(TRADE_COLS))})",
                        [tuple(json.dumps(r[c], default=str) if c == "features" else r[c] for c in TRADE_COLS)
                         for r in clean])
                    self.db.commit()
        except Exception as e:
            self.last_error = str(e)

    def trades(self, days=TRADE_DAYS):
        since = (_now() - timedelta(days=days)).date().isoformat()
        try:
            if self.remote:
                df = pd.DataFrame(self._get("stocklab_trades", {"select": "*", "day": f"gte.{since}",
                                                                "order": "ts.desc", "limit": "20000"}))
            else:
                with self._lock:
                    df = pd.read_sql_query("select * from stocklab_trades where day >= ? order by ts desc",
                                           self.db, params=(since,))
                df["features"] = df.features.map(lambda s: json.loads(s) if s else {})
        except Exception as e:
            self.last_error = str(e)
            df = pd.DataFrame()
        return df if not df.empty else pd.DataFrame(columns=TRADE_COLS)

    # ------------------------------------------------------------------ retention
    def purge(self):
        ev = (_now() - timedelta(days=EVENT_DAYS)).isoformat()
        tr = (_now() - timedelta(days=TRADE_DAYS)).date().isoformat()
        try:
            if self.remote:
                requests.delete(f"{self.url}/rest/v1/stocklab_events", headers=self._h(), params={"ts": f"lt.{ev}"}, timeout=15)
                requests.delete(f"{self.url}/rest/v1/stocklab_trades", headers=self._h(), params={"day": f"lt.{tr}"}, timeout=15)
                requests.delete(f"{self.url}/rest/v1/stocklab_paper", headers=self._h(), params={"day": f"lt.{tr}"}, timeout=15)
            else:
                with self._lock:
                    self.db.execute("delete from stocklab_events where ts < ?", (ev,))
                    self.db.execute("delete from stocklab_trades where day < ?", (tr,))
                    self.db.execute("delete from stocklab_paper where day < ?", (tr,))
                    self.db.commit()
        except Exception as e:
            self.last_error = str(e)

    # ------------------------------------------------------------------ key/value (agent config, proposals)
    def kv_get(self, key, default=None):
        try:
            if self.remote:
                rows = self._get("stocklab_kv", {"select": "value", "key": f"eq.{key}"})
                return rows[0]["value"] if rows else default
            with self._lock:
                row = self.db.execute("select value from stocklab_kv where key = ?", (key,)).fetchone()
            return json.loads(row[0]) if row else default
        except Exception as e:
            self.last_error = str(e)
            return default

    def kv_set(self, key, value):
        try:
            if self.remote:
                self._post("stocklab_kv", [dict(key=key, value=value, updated=_now().isoformat())],
                           "resolution=merge-duplicates,return=minimal", "key")
            else:
                with self._lock:
                    self.db.execute("insert or replace into stocklab_kv (key, value, updated) values (?,?,?)",
                                    (key, json.dumps(value, default=str), _now().isoformat()))
                    self.db.commit()
        except Exception as e:
            self.last_error = str(e)

    # ------------------------------------------------------------------ paper-trade log
    PAPER_COLS = ["key", "day", "symbol", "status", "rec_time", "logged_at", "data", "human", "updated"]

    def paper_upsert(self, rows):
        """rows: dicts with key, day, symbol, status, rec_time, logged_at, data (dict). Human reviews are untouched."""
        if not rows:
            return
        clean = [dict(key=r["key"], day=str(r["day"]), symbol=r["symbol"], status=r["status"], rec_time=_iso(r["rec_time"]),
                      logged_at=_iso(r["logged_at"]), data=json.loads(json.dumps(r["data"], default=str)),
                      updated=_now().isoformat()) for r in rows]
        try:
            if self.remote:
                self._post("stocklab_paper", clean, "resolution=merge-duplicates,return=minimal", "key")
            else:
                with self._lock:
                    for r in clean:
                        self.db.execute(
                            "insert into stocklab_paper (key,day,symbol,status,rec_time,logged_at,data,updated) "
                            "values (?,?,?,?,?,?,?,?) on conflict(key) do update set status=excluded.status, "
                            "data=excluded.data, updated=excluded.updated",
                            (r["key"], r["day"], r["symbol"], r["status"], r["rec_time"], r["logged_at"],
                             json.dumps(r["data"]), r["updated"]))
                    self.db.commit()
        except Exception as e:
            self.last_error = str(e)

    def paper_list(self, days=TRADE_DAYS):
        since = (_now() - timedelta(days=days)).date().isoformat()
        try:
            if self.remote:
                df = pd.DataFrame(self._get("stocklab_paper", {"select": "*", "day": f"gte.{since}",
                                                               "order": "rec_time.desc", "limit": "5000"}))
            else:
                with self._lock:
                    df = pd.read_sql_query("select * from stocklab_paper where day >= ? order by rec_time desc",
                                           self.db, params=(since,))
                for c in ("data", "human"):
                    df[c] = df[c].map(lambda s: json.loads(s) if s else None)
        except Exception as e:
            self.last_error = str(e)
            df = pd.DataFrame()
        if df.empty:
            return pd.DataFrame(columns=self.PAPER_COLS)
        for c in ("rec_time", "logged_at"):
            df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601").dt.tz_convert("Asia/Kolkata")
        return df

    def paper_review(self, key, human):
        try:
            if self.remote:
                r = requests.patch(f"{self.url}/rest/v1/stocklab_paper", headers=self._h({"Prefer": "return=minimal"}),
                                   params={"key": f"eq.{key}"}, data=json.dumps({"human": human}, default=str), timeout=15)
                if r.status_code >= 300:
                    self.last_error = f"stocklab_paper: {r.status_code} {r.text[:160]}"
            else:
                with self._lock:
                    self.db.execute("update stocklab_paper set human = ? where key = ?", (json.dumps(human, default=str), key))
                    self.db.commit()
        except Exception as e:
            self.last_error = str(e)

    # ------------------------------------------------------------------ sentinel archive (daily flags per stock)
    def sentinel_upsert(self, rows, overwrite=False):
        """rows: dicts with day, symbol, level, data. By default the first record for a stock-day is kept
        (the pre-open view), so later scans cannot rewrite what the Sentinel knew before the session."""
        if not rows:
            return
        clean = [dict(key=f"{r['day']}|{r['symbol']}", day=str(r["day"]), symbol=r["symbol"], level=r["level"],
                      data=json.loads(json.dumps(r.get("data") or {}, default=str)), updated=_now().isoformat()) for r in rows]
        try:
            if self.remote:
                pref = "resolution=merge-duplicates" if overwrite else "resolution=ignore-duplicates"
                for i in range(0, len(clean), 500):
                    self._post("stocklab_sentinel", clean[i:i + 500], pref + ",return=minimal", "key")
            else:
                verb = "insert or replace" if overwrite else "insert or ignore"
                with self._lock:
                    self.db.executemany(f"{verb} into stocklab_sentinel (key,day,symbol,level,data,updated) values (?,?,?,?,?,?)",
                                        [(r["key"], r["day"], r["symbol"], r["level"], json.dumps(r["data"]), r["updated"])
                                         for r in clean])
                    self.db.commit()
        except Exception as e:
            self.last_error = str(e)

    def sentinel_list(self, days=120):
        since = (_now() - timedelta(days=days)).date().isoformat()
        try:
            if self.remote:
                df = pd.DataFrame(self._get("stocklab_sentinel", {"select": "day,symbol,level,data", "day": f"gte.{since}",
                                                                  "order": "day.desc", "limit": "20000"}))
            else:
                with self._lock:
                    df = pd.read_sql_query("select day,symbol,level,data from stocklab_sentinel where day >= ? order by day desc",
                                           self.db, params=(since,))
                df["data"] = df.data.map(lambda s: json.loads(s) if s else {})
        except Exception as e:
            self.last_error = str(e)
            df = pd.DataFrame()
        if df.empty:
            return pd.DataFrame(columns=["day", "symbol", "level", "data", "types", "scanned"])
        df["day"] = df.day.astype(str).str[:10]
        df["types"] = df.data.map(lambda d: (d or {}).get("types") or [])
        df["scanned"] = df.data.map(lambda d: bool((d or {}).get("scanned", True)))
        return df

"""
Storage for Stock Lab agents.

  audit events          stocklab_events    kept 7 days   (distilled into learning memory every hour)
  trade journal         stocklab_trades    kept 90 days  (Coach training rows + live signals)
  paper-trade log       stocklab_paper     kept 90 days  (Trader / Analyst records + human reviews)
  sentinel archive      stocklab_sentinel  kept 180 days (pre-open news view per stock-day)
  settings / decisions  stocklab_kv        kept forever
  learning memory       stocklab_memory    kept 180 days (signal archive past Yahoo's ~60-day limit,
                                                          daily correlations and movement, audit digests)

Backends
  • Supabase (persistent) — used when Streamlit secrets contain SUPABASE_URL and SUPABASE_KEY.
    SUPABASE_KEY must be a server-side key: a new secret key (sb_secret_…) or the legacy service_role JWT.
    Publishable / anon keys cannot see the tables (RLS is on with no policies) and are reported by health().
  • Local SQLite (fallback) — works immediately but is wiped whenever the hosted app restarts or redeploys.

Supabase setup: run SUPABASE_SQL once in the SQL editor (re-running is safe). Existing installs only need
MIGRATION_SQL (the learning-memory table).
"""
import base64
import collections
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

EVENT_DAYS = 7
TRADE_DAYS = 90
SENTINEL_DAYS = 180
MEMORY_DAYS = 180
PAGE = 1000                 # Supabase caps every REST response at "Max rows" (1000 by default) — always page
SCHEMA_VERSION = 2

MIGRATION_SQL = """-- v2: learning memory (signal archive, daily correlations/movement, audit digests)
create table if not exists stocklab_memory (
  key text primary key, kind text not null, eng text, day date, symbol text,
  data jsonb, updated timestamptz default now()
);
create index if not exists stocklab_memory_kind on stocklab_memory (kind, eng, day);
alter table stocklab_memory enable row level security;
"""

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
-- The app uses the secret / service-role key from Streamlit secrets (server side only), which bypasses RLS.
""" + "\n" + MIGRATION_SQL

TABLES = {"stocklab_events": "id", "stocklab_trades": "key", "stocklab_kv": "key", "stocklab_paper": "key",
          "stocklab_sentinel": "key", "stocklab_memory": "key"}
TABLE_ROLE = {"stocklab_events": "Audit log (7 days)", "stocklab_trades": "Trade journal / Coach training (90 days)",
              "stocklab_kv": "Shared settings and decisions", "stocklab_paper": "Paper-trade log + your reviews (90 days)",
              "stocklab_sentinel": "Sentinel news archive (180 days)",
              "stocklab_memory": "Learning memory — signal archive and audit digests (180 days)"}

TRADE_COLS = ["key", "ts", "day", "source", "symbol", "setup", "side", "entry", "stop", "target", "exit", "r",
              "status", "features", "reason", "updated"]
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 540, 544}


class StoreError(RuntimeError):
    """A read could not be completed. Raised only to callers that ask for strict reads."""


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


def _jsonable(v):
    return json.loads(json.dumps(v, default=str))


def key_kind(key):
    """What kind of Supabase key this is, and whether it can be used server side (bypasses RLS)."""
    key = (key or "").strip()
    if key.startswith("sb_secret_"):
        return "secret key (sb_secret_…)", True
    if key.startswith("sb_publishable_"):
        return "publishable key (public — cannot read the tables)", False
    if key.startswith("eyJ") and key.count(".") == 2:
        try:
            p = key.split(".")[1]
            role = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))).get("role", "?")
        except Exception:
            role = "?"
        return f"legacy {role} JWT", role == "service_role"
    return ("not set", None) if not key else ("unrecognised key", None)


class Store:
    def __init__(self, url=None, key=None, path=None):
        self.url = (url or "").strip().rstrip("/")
        self.key = (key or "").strip()
        self.remote = bool(self.url and self.key)
        self._lock = threading.Lock()
        self._buf_lock = threading.Lock()
        self._buf = []                                   # audit events waiting to be written (remote only)
        self._seen = collections.OrderedDict()           # dkeys already written by this process
        self.errors = collections.deque(maxlen=25)       # (utc iso, message) — shown in the Audit log
        self.memory_ok = True                            # False when stocklab_memory has not been created yet
        self._health = None
        self._down_until = 0.0                           # circuit breaker: fail fast while Supabase is unreachable
        self.writes = collections.Counter()
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
            create table if not exists stocklab_memory (key text primary key, kind text, eng text, day text,
              symbol text, data text, updated text);
            create index if not exists stocklab_memory_kind on stocklab_memory (kind, eng, day);
            """)

    # ------------------------------------------------------------------ status
    @property
    def kind(self):
        return "Supabase (persistent)" if self.remote else "Temporary (resets when the app restarts)"

    @property
    def last_error(self):
        return self.errors[-1][1] if self.errors else None

    def _err(self, msg):
        self.errors.append((_now().isoformat(), str(msg)[:300]))

    # ------------------------------------------------------------------ REST helpers
    def _h(self, extra=None):
        h = {"apikey": self.key, "Content-Type": "application/json"}
        if self.key.startswith("eyJ"):          # legacy service_role JWT; new sb_secret_ keys go in apikey only
            h["Authorization"] = f"Bearer {self.key}"
        h.update(extra or {})
        return h

    def _req(self, method, table, params=None, body=None, headers=None, timeout=20):
        """One REST call with retries for timeouts, rate limits and 5xx (free projects wake up slowly).
        Returns the response, or None after recording the error."""
        if time.time() < self._down_until:
            self._err(f"{table}: skipped — Supabase unreachable, retrying after {time.strftime('%H:%M:%S', time.gmtime(self._down_until))}Z")
            return None
        url = f"{self.url}/rest/v1/{table}"
        data = json.dumps(body, default=str) if body is not None else None
        last, unreachable = None, False
        for attempt in range(3):
            try:
                r = requests.request(method, url, headers=self._h(headers), params=params, data=data, timeout=timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                last, unreachable = f"{table}: {type(e).__name__} — {str(e)[:120]}", True
            else:
                unreachable = r.status_code in RETRY_STATUS and r.status_code >= 500
                if r.status_code < 300:
                    return r
                last = f"{table}: {r.status_code} {r.text[:200]}"
                if r.status_code not in RETRY_STATUS:
                    if table == "stocklab_memory" and (r.status_code == 404 or "PGRST205" in r.text or "42P01" in r.text):
                        self.memory_ok = False
                    break
            time.sleep((0.6, 1.8, 0)[attempt])
        if unreachable:
            self._down_until = time.time() + 45
        self._err(last)
        return None

    def _post(self, table, rows, prefer, on_conflict=None):
        q = {"on_conflict": on_conflict} if on_conflict else None
        ok = True
        for i in range(0, len(rows), 500):
            r = self._req("POST", table, q, rows[i:i + 500], {"Prefer": prefer})
            ok = ok and r is not None
        if ok:
            self.writes[table] += len(rows)
        return ok

    def _get(self, table, params, order, max_rows=50_000):
        """Every row matching params, paged past the server's max-rows cap. Returns None if any page failed.
        `order` must end with a unique column so pages never overlap or skip."""
        out, off = [], 0
        while off < max_rows:
            p = dict(params, order=order, limit=str(PAGE), offset=str(off))
            r = self._req("GET", table, p)
            if r is None:
                return None
            batch = r.json()
            out += batch
            if not batch:
                break
            off += len(batch)               # advance by what came back: works whatever the server's cap is
            if len(batch) < PAGE and "/" in r.headers.get("Content-Range", "") and \
                    r.headers["Content-Range"].split("/")[-1] not in ("*", ""):
                break
        return out

    def _fail(self, strict, e=None):
        if e is not None:
            self._err(e)
        if strict:
            raise StoreError(self.last_error or "storage read failed")

    # ------------------------------------------------------------------ health
    def health(self, force=False):
        """Key type, which tables exist and how many rows they hold. Cached for 10 minutes."""
        if self._health and not force and time.time() - self._health["at"] < 600:
            return self._health
        h = dict(at=time.time(), backend=self.kind, tables={}, ok=True, missing=[], key_kind=None, key_ok=None,
                 notes=[], schema=SCHEMA_VERSION)
        if not self.remote:
            with self._lock:
                for t in TABLES:
                    try:
                        h["tables"][t] = dict(status="ok", rows=self.db.execute(f"select count(*) from {t}").fetchone()[0])
                    except Exception as e:
                        h["tables"][t] = dict(status="error", rows=None, note=str(e)[:120])
            self._health = h
            return h
        h["key_kind"], h["key_ok"] = key_kind(self.key)
        if h["key_ok"] is False:
            h["notes"].append("SUPABASE_KEY is a public key. Use the secret key (Project Settings → API Keys → "
                              "Secret keys, sb_secret_…) or the legacy service_role key.")
        if not self.url.startswith("https://"):
            h["notes"].append("SUPABASE_URL should look like https://<project-ref>.supabase.co (no /rest/v1 at the end).")
        dead = None
        for t, col in TABLES.items():
            if dead:
                h["tables"][t] = dict(status="error", rows=None, note=dead)
                continue
            try:
                r = requests.get(f"{self.url}/rest/v1/{t}", headers=self._h({"Prefer": "count=exact", "Range": "0-0"}),
                                 params={"select": col, "limit": "1"}, timeout=15)
                if r.status_code < 300 or r.status_code == 206:
                    cr = r.headers.get("Content-Range", "*/0").split("/")[-1]
                    h["tables"][t] = dict(status="ok", rows=int(cr) if cr.isdigit() else None)
                elif r.status_code == 404 or "PGRST205" in r.text or "42P01" in r.text:
                    h["tables"][t] = dict(status="missing", rows=None)
                    h["missing"].append(t)
                elif r.status_code in (401, 403):
                    h["tables"][t] = dict(status="denied", rows=None, note=r.text[:120])
                else:
                    h["tables"][t] = dict(status="error", rows=None, note=f"{r.status_code} {r.text[:120]}")
            except (requests.ConnectionError, requests.Timeout) as e:
                dead = f"unreachable: {type(e).__name__}"
                h["tables"][t] = dict(status="error", rows=None, note=dead)
            except Exception as e:
                h["tables"][t] = dict(status="error", rows=None, note=str(e)[:120])
        if dead:
            self._down_until = time.time() + 45
        self.memory_ok = h["tables"].get("stocklab_memory", {}).get("status") != "missing"
        bad = [t for t, v in h["tables"].items() if v["status"] != "ok"]
        h["ok"] = not bad and h["key_ok"] is not False
        if h["missing"]:
            only_mem = h["missing"] == ["stocklab_memory"]
            h["notes"].append("Run the migration SQL below in Supabase → SQL editor (safe to re-run)." if only_mem else
                              "Some tables are missing — run the full setup SQL below in Supabase → SQL editor.")
            h["sql"] = MIGRATION_SQL if only_mem else SUPABASE_SQL
        if any(v["status"] == "denied" for v in h["tables"].values()):
            h["notes"].append("Supabase refused the key (401/403). Check the key was copied whole and belongs to this project.")
        if all(v["status"] == "error" for v in h["tables"].values()):
            h["notes"].append("Supabase did not answer. A free project pauses after a week without use — "
                              "restore it from the Supabase dashboard.")
        self._health = h
        return h

    # ------------------------------------------------------------------ events
    def log(self, agent, kind, message, symbol=None, detail=None, dkey=None, ts=None):
        """Record one audit event. dkey makes it idempotent (same dkey is stored once). On Supabase, events are
        batched and written by flush() — agents log dozens per refresh and one request each was the slow path."""
        row = dict(ts=_iso(ts) or _now().isoformat(), dkey=dkey, agent=agent, kind=kind, symbol=symbol,
                   message=message, detail=_jsonable(detail or {}))
        try:
            if self.remote:
                with self._buf_lock:
                    if dkey is not None:
                        if dkey in self._seen:
                            return
                        self._seen[dkey] = 1
                        while len(self._seen) > 20_000:
                            self._seen.popitem(last=False)
                    self._buf.append(row)
                    n = len(self._buf)
                if n >= 40:
                    self.flush()
            else:
                with self._lock:
                    self.db.execute("insert or ignore into stocklab_events (ts,dkey,agent,kind,symbol,message,detail)"
                                    " values (?,?,?,?,?,?,?)", (row["ts"], dkey, agent, kind, symbol, message,
                                                                 json.dumps(row["detail"], default=str)))
                    self.db.commit()
        except Exception as e:  # never break the app over logging
            self._err(e)

    def flush(self):
        """Write buffered audit events in one request. Failed rows stay buffered for the next flush."""
        if not self.remote:
            return True
        with self._buf_lock:
            rows, self._buf = self._buf, []
        if not rows:
            return True
        ok = self._post("stocklab_events", rows, "resolution=ignore-duplicates,return=minimal", "dkey")
        if not ok:
            with self._buf_lock:
                self._buf = (rows + self._buf)[-2000:]
        return ok

    @property
    def pending(self):
        return len(self._buf)

    def events(self, days=EVENT_DAYS, strict=False):
        self.flush()
        since = (_now() - timedelta(days=days)).isoformat()
        try:
            if self.remote:
                rows = self._get("stocklab_events", {"select": "ts,agent,kind,symbol,message,detail", "ts": f"gte.{since}"},
                                 "ts.desc,id.desc", max_rows=20_000)
                if rows is None:
                    self._fail(strict)
                    rows = []
                df = pd.DataFrame(rows)
            else:
                with self._lock:
                    df = pd.read_sql_query("select ts,agent,kind,symbol,message,detail from stocklab_events "
                                           "where ts >= ? order by ts desc limit 20000", self.db, params=(since,))
                df["detail"] = df.detail.map(lambda s: json.loads(s) if s else {})
        except StoreError:
            raise
        except Exception as e:
            self._fail(strict, e)
            df = pd.DataFrame()
        if df.empty:
            return pd.DataFrame(columns=["ts", "agent", "kind", "symbol", "message", "detail"])
        df["ts"] = pd.to_datetime(df.ts, utc=True, format="ISO8601").dt.tz_convert("Asia/Kolkata")
        return df

    # ------------------------------------------------------------------ trades
    def upsert_trades(self, rows):
        if not rows:
            return True
        clean = []
        for r in rows:
            r = {k: _clean(r.get(k)) for k in TRADE_COLS}
            r["ts"] = _iso(r["ts"])
            r["day"] = str(r["day"]) if r["day"] is not None else None
            r["updated"] = _now().isoformat()
            r["features"] = _jsonable(r["features"] or {})
            clean.append(r)
        try:
            if self.remote:
                return self._post("stocklab_trades", clean, "resolution=merge-duplicates,return=minimal", "key")
            with self._lock:
                self.db.executemany(
                    f"insert or replace into stocklab_trades ({','.join(TRADE_COLS)}) values ({','.join('?' * len(TRADE_COLS))})",
                    [tuple(json.dumps(r[c], default=str) if c == "features" else r[c] for c in TRADE_COLS)
                     for r in clean])
                self.db.commit()
            return True
        except Exception as e:
            self._err(e)
            return False

    def trades(self, days=TRADE_DAYS, strict=False):
        since = (_now() - timedelta(days=days)).date().isoformat()
        try:
            if self.remote:
                rows = self._get("stocklab_trades", {"select": "*", "day": f"gte.{since}"}, "ts.desc,key.asc")
                if rows is None:
                    self._fail(strict)
                    rows = []
                df = pd.DataFrame(rows)
            else:
                with self._lock:
                    df = pd.read_sql_query("select * from stocklab_trades where day >= ? order by ts desc",
                                           self.db, params=(since,))
                df["features"] = df.features.map(lambda s: json.loads(s) if s else {})
        except StoreError:
            raise
        except Exception as e:
            self._fail(strict, e)
            df = pd.DataFrame()
        return df if not df.empty else pd.DataFrame(columns=TRADE_COLS)

    # ------------------------------------------------------------------ retention
    def purge(self):
        cut = dict(ev=(_now() - timedelta(days=EVENT_DAYS)).isoformat(),
                   tr=(_now() - timedelta(days=TRADE_DAYS)).date().isoformat(),
                   se=(_now() - timedelta(days=SENTINEL_DAYS)).date().isoformat(),
                   me=(_now() - timedelta(days=MEMORY_DAYS)).date().isoformat())
        jobs = [("stocklab_events", "ts", "ev"), ("stocklab_trades", "day", "tr"), ("stocklab_paper", "day", "tr"),
                ("stocklab_sentinel", "day", "se"), ("stocklab_memory", "day", "me")]
        try:
            if self.remote:
                for t, col, k in jobs:
                    if t == "stocklab_memory" and not self.memory_ok:
                        continue
                    self._req("DELETE", t, {col: f"lt.{cut[k]}"}, headers={"Prefer": "return=minimal"})
            else:
                with self._lock:
                    for t, col, k in jobs:
                        self.db.execute(f"delete from {t} where {col} < ?", (cut[k],))
                    self.db.commit()
        except Exception as e:
            self._err(e)

    # ------------------------------------------------------------------ key/value (agent config, proposals)
    def kv_get(self, key, default=None, strict=False):
        """strict=True raises StoreError when the read fails, so callers never mistake an outage for 'not set'."""
        try:
            if self.remote:
                r = self._req("GET", "stocklab_kv", {"select": "value", "key": f"eq.{key}"})
                if r is None:
                    self._fail(strict)
                    return default
                rows = r.json()
                return rows[0]["value"] if rows else default
            with self._lock:
                row = self.db.execute("select value from stocklab_kv where key = ?", (key,)).fetchone()
            return json.loads(row[0]) if row else default
        except StoreError:
            raise
        except Exception as e:
            self._fail(strict, e)
            return default

    def kv_set(self, key, value):
        try:
            if self.remote:
                return self._post("stocklab_kv", [dict(key=key, value=_jsonable(value), updated=_now().isoformat())],
                                  "resolution=merge-duplicates,return=minimal", "key")
            with self._lock:
                self.db.execute("insert or replace into stocklab_kv (key, value, updated) values (?,?,?)",
                                (key, json.dumps(value, default=str), _now().isoformat()))
                self.db.commit()
            return True
        except Exception as e:
            self._err(e)
            return False

    # ------------------------------------------------------------------ paper-trade log
    PAPER_COLS = ["key", "day", "symbol", "status", "rec_time", "logged_at", "data", "human", "updated"]

    def paper_upsert(self, rows):
        """rows: dicts with key, day, symbol, status, rec_time, logged_at, data (dict). Human reviews are untouched."""
        if not rows:
            return True
        clean = [dict(key=r["key"], day=str(r["day"]), symbol=r["symbol"], status=r["status"], rec_time=_iso(r["rec_time"]),
                      logged_at=_iso(r["logged_at"]), data=_jsonable(r["data"]), updated=_now().isoformat()) for r in rows]
        try:
            if self.remote:
                return self._post("stocklab_paper", clean, "resolution=merge-duplicates,return=minimal", "key")
            with self._lock:
                for r in clean:
                    self.db.execute(
                        "insert into stocklab_paper (key,day,symbol,status,rec_time,logged_at,data,updated) "
                        "values (?,?,?,?,?,?,?,?) on conflict(key) do update set status=excluded.status, "
                        "data=excluded.data, updated=excluded.updated",
                        (r["key"], r["day"], r["symbol"], r["status"], r["rec_time"], r["logged_at"],
                         json.dumps(r["data"]), r["updated"]))
                self.db.commit()
            return True
        except Exception as e:
            self._err(e)
            return False

    def paper_list(self, days=TRADE_DAYS, strict=False):
        since = (_now() - timedelta(days=days)).date().isoformat()
        try:
            if self.remote:
                rows = self._get("stocklab_paper", {"select": "*", "day": f"gte.{since}"}, "rec_time.desc,key.asc")
                if rows is None:
                    self._fail(strict)
                    rows = []
                df = pd.DataFrame(rows)
            else:
                with self._lock:
                    df = pd.read_sql_query("select * from stocklab_paper where day >= ? order by rec_time desc",
                                           self.db, params=(since,))
                for c in ("data", "human"):
                    df[c] = df[c].map(lambda s: json.loads(s) if s else None)
        except StoreError:
            raise
        except Exception as e:
            self._fail(strict, e)
            df = pd.DataFrame()
        if df.empty:
            return pd.DataFrame(columns=self.PAPER_COLS)
        for c in ("rec_time", "logged_at"):
            df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601").dt.tz_convert("Asia/Kolkata")
        return df

    def paper_review(self, key, human):
        try:
            if self.remote:
                r = self._req("PATCH", "stocklab_paper", {"key": f"eq.{key}"}, {"human": _jsonable(human)},
                              {"Prefer": "return=minimal"})
                return r is not None
            with self._lock:
                self.db.execute("update stocklab_paper set human = ? where key = ?", (json.dumps(human, default=str), key))
                self.db.commit()
            return True
        except Exception as e:
            self._err(e)
            return False

    # ------------------------------------------------------------------ sentinel archive (daily flags per stock)
    def sentinel_upsert(self, rows, overwrite=False):
        """rows: dicts with day, symbol, level, data. By default the first record for a stock-day is kept
        (the pre-open view), so later scans cannot rewrite what the Sentinel knew before the session."""
        if not rows:
            return True
        clean = [dict(key=f"{r['day']}|{r['symbol']}", day=str(r["day"]), symbol=r["symbol"], level=r["level"],
                      data=_jsonable(r.get("data") or {}), updated=_now().isoformat()) for r in rows]
        try:
            if self.remote:
                pref = "resolution=merge-duplicates" if overwrite else "resolution=ignore-duplicates"
                return self._post("stocklab_sentinel", clean, pref + ",return=minimal", "key")
            verb = "insert or replace" if overwrite else "insert or ignore"
            with self._lock:
                self.db.executemany(f"{verb} into stocklab_sentinel (key,day,symbol,level,data,updated) values (?,?,?,?,?,?)",
                                    [(r["key"], r["day"], r["symbol"], r["level"], json.dumps(r["data"]), r["updated"])
                                     for r in clean])
                self.db.commit()
            return True
        except Exception as e:
            self._err(e)
            return False

    def sentinel_list(self, days=120, strict=False):
        """Archive rows; `updated` is when the stock-day was first recorded (lets learning stay point-in-time)."""
        since = (_now() - timedelta(days=days)).date().isoformat()
        try:
            if self.remote:
                rows = self._get("stocklab_sentinel", {"select": "key,day,symbol,level,data,updated", "day": f"gte.{since}"},
                                 "day.desc,key.asc")
                if rows is None:
                    self._fail(strict)
                    rows = []
                df = pd.DataFrame(rows)
            else:
                with self._lock:
                    df = pd.read_sql_query("select day,symbol,level,data,updated from stocklab_sentinel where day >= ? "
                                           "order by day desc", self.db, params=(since,))
                df["data"] = df.data.map(lambda s: json.loads(s) if s else {})
        except StoreError:
            raise
        except Exception as e:
            self._fail(strict, e)
            df = pd.DataFrame()
        if df.empty:
            return pd.DataFrame(columns=["day", "symbol", "level", "data", "updated", "types", "scanned"])
        df = df.drop(columns=["key"], errors="ignore")
        df["day"] = df.day.astype(str).str[:10]
        df["updated"] = pd.to_datetime(df.updated, utc=True, format="ISO8601", errors="coerce")
        df["types"] = df.data.map(lambda d: (d or {}).get("types") or [])
        df["scanned"] = df.data.map(lambda d: bool((d or {}).get("scanned", True)))
        return df

    # ------------------------------------------------------------------ learning memory
    def memory_upsert(self, kind, eng, rows):
        """rows: dicts with key, day, symbol (optional) and data. Stored under '<kind>|<eng>|<key>' so each
        signal engine keeps its own history. Re-writing a key replaces it."""
        if not rows or not self.memory_ok:
            return False
        now = _now().isoformat()
        clean = [dict(key=f"{kind}|{eng}|{r['key']}", kind=kind, eng=eng, day=str(r["day"])[:10],
                      symbol=r.get("symbol"), data=_jsonable(r.get("data")), updated=now) for r in rows]
        try:
            if self.remote:
                return self._post("stocklab_memory", clean, "resolution=merge-duplicates,return=minimal", "key")
            with self._lock:
                self.db.executemany("insert or replace into stocklab_memory (key,kind,eng,day,symbol,data,updated) "
                                    "values (?,?,?,?,?,?,?)",
                                    [(r["key"], kind, eng, r["day"], r["symbol"], json.dumps(r["data"]), now) for r in clean])
                self.db.commit()
            return True
        except Exception as e:
            self._err(e)
            return False

    def memory_list(self, kind, eng=None, days=MEMORY_DAYS, strict=False):
        """[{key, day, symbol, data}] oldest first; key is the caller's own key (prefix removed)."""
        if not self.memory_ok:
            return []
        since = (_now() - timedelta(days=days)).date().isoformat()
        pre = f"{kind}|{eng}|"
        try:
            if self.remote:
                p = {"select": "key,day,symbol,data", "kind": f"eq.{kind}", "day": f"gte.{since}"}
                if eng is not None:
                    p["eng"] = f"eq.{eng}"
                rows = self._get("stocklab_memory", p, "day.asc,key.asc", max_rows=100_000)
                if rows is None:
                    self._fail(strict)
                    return []
            else:
                q = "select key,day,symbol,data from stocklab_memory where kind = ? and day >= ?"
                args = [kind, since]
                if eng is not None:
                    q += " and eng = ?"
                    args.append(eng)
                with self._lock:
                    rows = [dict(key=k, day=d, symbol=s, data=json.loads(x) if x else None)
                            for k, d, s, x in self.db.execute(q + " order by day, key", args).fetchall()]
        except StoreError:
            raise
        except Exception as e:
            self._fail(strict, e)
            return []
        for r in rows:
            k = r["key"]
            r["key"] = k[len(pre):] if eng is not None and k.startswith(pre) else k.split("|", 2)[-1]
            r["day"] = str(r["day"])[:10]
        return rows

    def memory_stats(self):
        """{kind: {eng: (rows, first_day, last_day)}} — what the learning memory holds."""
        out = {}
        try:
            if self.remote:
                if not self.memory_ok:
                    return out
                rows = self._get("stocklab_memory", {"select": "kind,eng,day"}, "day.asc,key.asc", max_rows=200_000) or []
            else:
                with self._lock:
                    rows = [dict(kind=k, eng=e, day=d) for k, e, d in
                            self.db.execute("select kind, eng, day from stocklab_memory").fetchall()]
            for r in rows:
                n, a, b = out.setdefault(r["kind"], {}).get(r["eng"], (0, None, None))
                d = str(r["day"])[:10]
                out[r["kind"]][r["eng"]] = (n + 1, min(a, d) if a else d, max(b, d) if b else d)
        except Exception as e:
            self._err(e)
        return out

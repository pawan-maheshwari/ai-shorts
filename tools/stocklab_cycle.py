"""
Run Stock Lab's agent cycle headlessly — no browser, no Streamlit Cloud session to keep awake.

Streamlit's own test runner (AppTest) executes apps/stock-lab/app.py exactly as a browser session would: the Intraday
tab's Selector → Sentinel → Coach → Pulse → arbiter → Risk → Paper Trader → Narrator pipeline, the Analyst desk at
09:30 and every write to the Supabase store. Each cycle is one full rerun of the script in the same session, so the
app's own refresh logic fetches fresh bars. Cycles run every STOCKLAB_CYCLE_S seconds (default 120) until
STOCKLAB_UNTIL (IST, HH:MM).

The app's secrets (SUPABASE_URL, SUPABASE_KEY, GROQ_API_KEY, …) are read from the environment. Without
SUPABASE_URL + SUPABASE_KEY the agents would write nowhere, so the script exits with code 3 and the workflow falls
back to the browser watch (tools/stocklab_watch.py).

STOCKLAB_DEMO=1 runs one cycle on demo data (for testing, no secrets needed).
"""
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "apps", "stock-lab", "app.py")
UNTIL = os.environ.get("STOCKLAB_UNTIL", "15:35")
CYCLE_S = int(os.environ.get("STOCKLAB_CYCLE_S", "120"))
RUN_TIMEOUT = int(os.environ.get("STOCKLAB_RUN_TIMEOUT", "600"))
DEMO = os.environ.get("STOCKLAB_DEMO") == "1"
SECRET_NAMES = ["SUPABASE_URL", "SUPABASE_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER",
                "LLM_MODEL_FAST", "LLM_MODEL_SMART", "NSE_MCP", "NSE_BHAVCOPY_MCP_URL", "NSE_LIVE_MCP_URL", "ADMIN_PIN"]
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.now(IST)


def deadline():
    h, m = (int(x) for x in UNTIL.split(":"))
    return now_ist().replace(hour=h, minute=m, second=0, microsecond=0)


def status(at):
    """One line from what the run rendered: Pulse, arbiter, signal count, and any exception."""
    if at.exception:
        return "EXCEPTION: " + " | ".join(str(e.message)[:200] for e in at.exception)
    txt = "\n".join(str(getattr(m, "value", "") or "") for m in list(at.markdown) + list(at.info) + list(at.warning))
    bits = []
    for pat in (r"🧭 Pulse · ([^<]+)<", r"09:30 arbiter</b> · ([^<]+)<", r"SIGNALS\s*\n\s*(\d+)", r"Session of [^.]+\.",
                r"Market closed[^.<]*"):
        m = re.search(pat, txt)
        if m:
            bits.append(m.group(1) if m.groups() else m.group(0))
    return " · ".join(b.strip() for b in bits) or "ran (no status line)"


def main():
    from streamlit.testing.v1 import AppTest

    secrets = {k: os.environ[k] for k in SECRET_NAMES if os.environ.get(k)}
    if not DEMO and not (secrets.get("SUPABASE_URL") and secrets.get("SUPABASE_KEY")):
        print("No SUPABASE_URL/SUPABASE_KEY in the environment — cannot run headless; use the browser watch.")
        sys.exit(3)
    if not DEMO and now_ist() >= deadline():
        print(f"Started after {UNTIL} IST — nothing to run.")
        return
    at = AppTest.from_file(APP, default_timeout=RUN_TIMEOUT)
    for k, v in secrets.items():
        at.secrets[k] = v
    if DEMO:
        at.query_params["demo"] = "1"
    print(f"Headless agent cycle every {CYCLE_S}s until {UNTIL} IST" + (" (demo, one cycle)" if DEMO else ""), flush=True)
    fails = 0
    while True:
        t0 = time.time()
        try:
            at.run()
            line = status(at)
            fails = fails + 1 if line.startswith("EXCEPTION") else 0
        except Exception as e:                      # timeout or a crash inside the runner itself
            line, fails = f"run failed: {type(e).__name__}: {str(e)[:200]}", fails + 1
        print(f"{now_ist():%H:%M} IST · {time.time() - t0:.0f}s · {line}", flush=True)
        if DEMO:
            sys.exit(1 if fails else 0)
        if fails >= 5:
            print("FAILED — five cycles in a row did not complete")
            sys.exit(1)
        if fails:                                   # start a fresh session after a failure
            at = AppTest.from_file(APP, default_timeout=RUN_TIMEOUT)
            for k, v in secrets.items():
                at.secrets[k] = v
        if now_ist() >= deadline():
            print("Market window over — done.")
            return
        time.sleep(max(5, CYCLE_S - (time.time() - t0)))


if __name__ == "__main__":
    main()

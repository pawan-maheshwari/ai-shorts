"""
Watch Stock Lab during NSE market hours.

Stock Lab's agents (Selector, Sentinel, Coach, Risk, Paper Trader) only run while someone has the app open:
the Intraday tab re-runs itself every couple of minutes, but only inside a live browser session. Between the
keep-alive visits nobody is watching, so signals were picked up late (or only in the evening replay).

This keeps one headless browser session open on the app until STOCKLAB_UNTIL (IST, HH:MM). The Intraday
panel's own auto-refresh then runs the agents every 2 minutes, and the page is reloaded every
STOCKLAB_RELOAD_MIN minutes in case the Streamlit connection drops. Each check prints the status line so the
Actions log shows what the agents saw.
"""
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

URL = os.environ.get("STOCKLAB_URL", "https://aishorts-stocklab.streamlit.app/")
UNTIL = os.environ.get("STOCKLAB_UNTIL", "15:35")
RELOAD_MIN = int(os.environ.get("STOCKLAB_RELOAD_MIN", "20"))
POLL_S = 60
READY_TEXT = "Auto-refresh"
WAKE_BUTTON = "Yes, get this app back up!"
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.now(IST)


def deadline():
    h, m = map(int, UNTIL.split(":"))
    return now_ist().replace(hour=h, minute=m, second=0, microsecond=0)


def app_frame(page):
    for fr in page.frames:
        try:
            if fr.get_by_text(READY_TEXT).count():
                return fr
        except Exception:
            pass
    return None


def wake_if_asleep(page):
    for fr in page.frames:
        try:
            btn = fr.get_by_role("button", name=WAKE_BUTTON)
            if btn.count():
                print("App was asleep — pressing the wake button", flush=True)
                btn.first.click()
                page.wait_for_timeout(20_000)
                return True
        except Exception:
            pass
    return False


def open_app(page, wait_s=600):
    page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        wake_if_asleep(page)
        if app_frame(page) is not None:
            return True
        page.wait_for_timeout(10_000)
    return False


def status(fr):
    """One line from the Intraday tab: refresh time, latest bar and today's signal count."""
    try:
        txt = fr.locator("body").inner_text(timeout=10_000)
    except Exception:
        return "page not readable"
    bits = []
    for pat in (r"Last refreshed [\d:]+ IST", r"Latest bar [\d:]+", r"SIGNALS\s*\n\s*(\d+)\s*\n\s*triggered today"):
        m = re.search(pat, txt)
        if m:
            bits.append(f"signals today {m.group(1)}" if m.groups() else m.group(0))
    if "First to fire:" in txt:
        m = re.search(r"First to fire: ([^\n]+?) (?:is|are) armed", txt)
        if m:
            bits.append(f"armed: {m.group(1)}")
    if "Connection error" in txt or ("Please wait" in txt and READY_TEXT not in txt):
        bits.append("CONNECTION LOST")
    return " · ".join(bits) or "no status line yet"


def main():
    end = deadline()
    if now_ist() >= end:
        print(f"Started after {UNTIL} IST — nothing to watch.")
        return 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        if not open_app(page):
            page.screenshot(path="stocklab-watch.png", full_page=True)
            print("FAILED — app did not render (screenshot saved)")
            return 1
        print(f"Watching until {UNTIL} IST", flush=True)
        last_reload, fails = time.time(), 0
        while now_ist() < end:
            page.wait_for_timeout(POLL_S * 1000)
            fr = app_frame(page)
            line = status(fr) if fr is not None else "app frame missing"
            print(f"{now_ist():%H:%M} IST · {line}", flush=True)
            lost = fr is None or "CONNECTION LOST" in line
            if lost or time.time() - last_reload >= RELOAD_MIN * 60:
                fails = fails + 1 if lost else 0
                if fails > 5:
                    page.screenshot(path="stocklab-watch.png", full_page=True)
                    print("FAILED — lost the app five times in a row (screenshot saved)")
                    return 1
                if not open_app(page):
                    print("Reload did not render — retrying next minute", flush=True)
                last_reload = time.time()
        print("Market window over — done.")
        browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())

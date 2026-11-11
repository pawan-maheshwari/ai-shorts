"""
Keep Stock Lab awake on Streamlit Community Cloud.

Streamlit puts free apps to sleep after 12 hours without visitors, and only a real browser session counts
(a plain HTTP request gets a static shell and never starts the app). This opens the app in headless
Chromium, taps "Yes, get this app back up!" if it is asleep, and waits until the app has actually rendered.
Exits non-zero if the app doesn't come up, so GitHub emails you about the failed run.
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("STOCKLAB_URL", "https://aishorts-stocklab.streamlit.app/")
READY_TEXT = "Auto-refresh"           # appears on the Intraday tab once the app has run
WAKE_BUTTON = "Yes, get this app back up!"
DEADLINE_S = int(os.environ.get("STOCKLAB_WAIT", "300"))


def find_in_frames(page, fn):
    for fr in page.frames:
        try:
            if fn(fr):
                return fr
        except Exception:
            pass
    return None


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(8_000)
        woke = False
        start = time.time()
        while time.time() - start < DEADLINE_S:
            btn = find_in_frames(page, lambda fr: fr.get_by_role("button", name=WAKE_BUTTON).count() > 0)
            if btn is not None and not woke:
                print("App was asleep — pressing the wake button")
                btn.get_by_role("button", name=WAKE_BUTTON).first.click()
                woke = True
                page.wait_for_timeout(20_000)
                continue
            ready = find_in_frames(page, lambda fr: fr.get_by_text(READY_TEXT).count() > 0)
            if ready is not None:
                # stay a little longer so data and caches warm up for the first real visitor
                page.wait_for_timeout(30_000)
                print(f"OK — app is running ({'woken up' if woke else 'was already awake'}) "
                      f"after {int(time.time() - start)}s")
                browser.close()
                return 0
            page.wait_for_timeout(10_000)
        page.screenshot(path="stocklab-keepalive.png", full_page=True)
        print(f"FAILED — app did not render within {DEADLINE_S}s (screenshot saved)")
        browser.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())

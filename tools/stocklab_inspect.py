"""Open the live Stock Lab, visit each tab and save screenshots + visible text (for diagnosis)."""
import os, sys, time
from playwright.sync_api import sync_playwright

URL = os.environ.get("STOCKLAB_URL", "https://aishorts-stocklab.streamlit.app/")
OUT = "inspect"
os.makedirs(OUT, exist_ok=True)


def app_frame(page):
    for fr in page.frames:
        try:
            if fr.get_by_role("tab").count() >= 3:
                return fr
        except Exception:
            pass
    return None


with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 1000})
    page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
    fr, t0 = None, time.time()
    while time.time() - t0 < 420:
        for f in page.frames:
            try:
                if f.get_by_role("button", name="Yes, get this app back up!").count():
                    f.get_by_role("button", name="Yes, get this app back up!").first.click()
            except Exception:
                pass
        fr = app_frame(page)
        if fr is not None and fr.get_by_text("Auto-refresh").count():
            break
        page.wait_for_timeout(10_000)
    if fr is None:
        page.screenshot(path=f"{OUT}/failed.png", full_page=True)
        sys.exit(1)
    page.wait_for_timeout(20_000)
    tabs = [fr.get_by_role("tab").nth(i).inner_text() for i in range(fr.get_by_role("tab").count())]
    open(f"{OUT}/tabs.txt", "w").write("\n".join(tabs))
    for i, name in enumerate(tabs):
        if "Agent scores" not in name and "Intraday" not in name:
            continue
        fr.get_by_role("tab").nth(i).click()
        deadline = time.time() + 600
        page.wait_for_timeout(8_000)
        while time.time() < deadline and fr.locator("[data-testid=stSpinner]").count():
            page.wait_for_timeout(5_000)
        page.wait_for_timeout(5_000)
        for ex in range(fr.locator("[data-testid=stExpander] summary").count()):
            try:
                fr.locator("[data-testid=stExpander] summary").nth(ex).click(timeout=2000)
            except Exception:
                pass
        page.wait_for_timeout(4_000)
        txt = fr.locator("[data-testid=stMain]").inner_text() if fr.locator("[data-testid=stMain]").count() else fr.locator("body").inner_text()
        open(f"{OUT}/tab{i}.txt", "w").write(txt)
        page.screenshot(path=f"{OUT}/tab{i}.png", full_page=True)
        for k, label in enumerate(["Model lab", "How selective?", "Agent scores", "Execution windows compared"]):
            loc = fr.get_by_text(label).first
            try:
                if loc.count():
                    loc.scroll_into_view_if_needed(timeout=5000)
                    page.wait_for_timeout(2500)
                    page.screenshot(path=f"{OUT}/tab{i}_s{k}.png")
            except Exception:
                pass
    b.close()

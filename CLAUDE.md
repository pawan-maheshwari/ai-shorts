# AIShorts — Claude working guide

Static AI-news site on GitHub Pages → **aishorts.biz** (CNAME), refreshed daily by
GitHub Actions bots, plus a Python Streamlit app (Stock Lab). No front-end build step.
Owner: Pawan Maheshwari (planetAI LLP).

## Token rules (read first)
- Huge files: `AI_Odyssey_BookSigning_AnimatedCover.html` 4.4 MB and `ai-odyssey.html`
  4.0 MB (base64 media) — never open unless the task is about them, and then only via
  `tools/claude/peek.sh`. `index.html` is 360 KB / 5.3k lines — use anchors, not full reads.
- Find code via `docs/CODEMAP.md`. Ignore `index_bac*.html`, `index_280626.html`
  (blocked in `.claude/settings.json`) and media (`*.mp4`, `*.mp3`, `assets/`).
- **Data is JSON, rendering is HTML.** To change content, edit the generator in `tools/`,
  not the generated JSON. To change look, edit the page; you rarely need both.

## Architecture
- `index.html` front page fetches data files with cache-busting `?v=`:
  `editions/latest.json`, `daily-wire.json`, `predictions.json`, `events.json`,
  `courses.json`, `briefs/index.json`. Contact form → formsubmit.co.
- Generators (Node ESM, no deps unless noted) in `tools/`, each run by one workflow:

| Workflow (cron UTC) | Script | Writes |
|---|---|---|
| `daily-news.yaml` 02:00 | `tools/build-edition.mjs` | `editions/*.json`, `feed.xml`, `headlines.json`, `bulletin.json/.mp3` |
| `daily-wire.yml` 02:30 | `tools/daily-wire.mjs` | `daily-wire.json` (strip art in `daily-wire/`) |
| `local-desk.yml` 00:40 | `tools/local-desk.mjs` | `events.json`, `courses.json`, `tools/geo-cities.json` |
| `ai-prediction.yml` 18:00 | `tools/predict.mjs` | `predictions.json`, `predictions-history.json` |
| `weekly-brief.yml` Sat 06:20 | `tools/weekly-brief.mjs` | `briefs/` |
| Policy Desk — Claude scheduled task (not Actions), every 2 h | `tools/policy-watch.mjs` (fed raw Digital Policy Alert answers) | `policy-watch.json`, `ops/policy-changelog.json` (internal, noindex) |
| `stocklab-*.yml` | `tools/stocklab_*.py` | keep-alive / inspection / market-hours watch (09:05–15:35 IST) / study export → `stocklab-study` branch |

- **Bots commit daily → always `git pull --rebase` before pushing**, and never hand-edit
  the files in the "Writes" column (the next run overwrites them).
- `policy-watch.html` = AI Policy Watch (iOS-style, reads `policy-watch.json`; per-section filters remembered in localStorage `aishorts-policy-filters`; Print sheet = this section or all, `@media print`); front-page bell `#pwBell` + `#pwPop` near `</body>` in `index.html`; internal log at `ops/policy-changelog.html`.
- Other pages: `ai-anchor.html` / `news-anchor.html` (video anchor, `anchor-*.mp4`),
  `stock-lab.html` (front for the Streamlit app), `vitalmetrics.html`, `ai-library.html`
  (a diverged copy also lives in AI-University — ask before syncing), `about/contact/privacy/standards`.
- `apps/stock-lab/` — Streamlit app (`app.py` UI 2.8k lines, `agents.py`, `cheetah_engine.py`,
  `research.py`, `store.py`, `learning.py`, `morning.py`, `desk.py`, `models.py`).
  Theme in `.streamlit/config.toml`. Secrets via `st.secrets`/env (`_secret()` in app.py).
- Brand: white paper `#ffffff`/`#f7f7f5`, ink `#121212`, serif type. Editorial tone,
  corrections logged in `corrections.json`, standards in `standards.html`.

## Recipes
- **New data-driven section** → new `tools/x.mjs` that writes `x.json`, a workflow copying
  the shape of `local-desk.yml`, and a render block in `index.html` that
  `fetch('x.json?v='+…)` like the existing ones (grep `fetch('events.json`).
- **Stock Lab feature** → locate with `peek.sh apps/stock-lab/app.py -f NAME`; verify with
  `python3 -m py_compile apps/stock-lab/*.py`.
- **Verify pages** → `python3 tools/claude/check_html.py index.html`; JS tools →
  `node --check tools/x.mjs`.
- After structural changes → `python3 tools/claude/codemap.py`.

Slash commands: `/feature <ask>`, `/fix <symptom>`.

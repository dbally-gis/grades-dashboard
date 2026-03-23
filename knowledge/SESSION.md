# Session Log — grades-dashboard

## 2026-03-23 — Initial Build

**Goal:** Bootstrap project from spec + reference HTML

**Status:** Complete — project scaffolded, not yet deployed

### Checkpoints

- Created full project structure (3-layer: directives, execution, knowledge)
- Seeded `data/grades_2026.json` with Q1-Q3 history from spec
- Wrote `fetch_schoology.py` — Playwright scraper with persistent auth session
- Wrote `parse_skyward.py` — PDF parser using pdfplumber
- Wrote `generate_dashboard.py` — 3 HTML editions (Dad, Mom, Julia)
- Wrote `run_dashboard.sh` — orchestration + Skyward PDF conditional
- Added to app-studio launcher: pending

## Last Known State

- Schoology auth: NOT YET DONE — run `python3 execution/fetch_schoology.py --auth`
- Skyward PDF: not yet provided — grade data uses Q3 history from config
- Vercel deploy: NOT YET DONE — needs GitHub repo + Vercel project
- Q4 grades: not yet available (quarter just started Mar 23)

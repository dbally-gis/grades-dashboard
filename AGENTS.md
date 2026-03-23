# Agent Instructions — grades-dashboard

> Mirrored in CLAUDE.md and GEMINI.md.

## Project Overview

Grades dashboard for Julia Bally (6th grade, Krimmel Intermediate, Klein ISD).
Three self-contained HTML dashboards auto-updated nightly from Schoology (scraped)
and Skyward (PDF upload). Deployed to Vercel via GitHub push.

## Outputs

| File | Audience |
|---|---|
| `index.html` | Dad — full detail, expandable cards, assignment drill-down |
| `wife.html` | Mom — 30-second scan, alert strip, all subjects |
| `julia.html` | Julia — motivational, colorful, encouraging |

## Data Sources

| Source | How | Priority |
|---|---|---|
| Skyward | Parent exports PDF → saves to `.tmp/skyward.pdf` | **1 (highest)** |
| Schoology | Playwright scraper with persistent session | 2 |
| `data/grades_2026.json` | Static config + quarter history fallback | 3 |

## Execution Scripts

| Script | Purpose |
|---|---|
| `execution/fetch_schoology.py` | Playwright scraper — saves `.tmp/schoology_grades.json` |
| `execution/parse_skyward.py` | PDF parser — saves `.tmp/skyward_grades.json` |
| `execution/generate_dashboard.py` | Merges data, writes 3 HTML files |

## First-Time Schoology Auth

```bash
# One-time: opens browser so you can log in
python3 execution/fetch_schoology.py --auth

# Verify session is active
python3 execution/fetch_schoology.py --dry-run
```

## Skyward PDF Workflow

1. Log into `skyward.kleinisd.net`
2. Navigate to gradebook → Print → Save as PDF
3. Copy PDF to `.tmp/skyward.pdf`
4. Run: `python3 execution/parse_skyward.py`
5. Re-run: `python3 execution/generate_dashboard.py`

## Merging Logic

- If Skyward has a grade → use Skyward
- Else if Schoology has a grade → use Schoology
- Else → use `quarter_history` from `data/grades_2026.json` (last known)

## 3-Layer Architecture

See global CLAUDE.md for full architecture docs.

**Layer 1 — Directives:** `directives/`
**Layer 2 — Orchestration:** Claude
**Layer 3 — Execution:** `execution/*.py`

## Safety

- Never commit `.env`, `.tmp/`, or `token.json`
- Never commit Skyward PDFs
- `token.json` / browser state stored in `.tmp/` (gitignored)

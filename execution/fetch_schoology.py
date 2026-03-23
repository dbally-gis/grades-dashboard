#!/usr/bin/env python3
"""
Script: fetch_schoology
Purpose: Scrape Julia's grades and upcoming assignments from Schoology using
         a persistent Playwright browser session (parent must be logged in once).

Usage:
    python3 execution/fetch_schoology.py            # full fetch
    python3 execution/fetch_schoology.py --auth     # open browser for manual login
    python3 execution/fetch_schoology.py --dry-run  # validate session only

First-time setup:
    1. Run with --auth flag — browser window opens
    2. Log into schoology.kleinisd.net in that window
    3. Close the window — session is saved to .tmp/browser_state/
    4. Subsequent runs reuse the saved session headlessly

Output:
    .tmp/schoology_grades.json
"""

import argparse
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TMP_DIR = PROJECT_ROOT / ".tmp"
BROWSER_STATE = TMP_DIR / "browser_state"
OUT_FILE = TMP_DIR / "schoology_grades.json"
CONFIG_FILE = PROJECT_ROOT / "data" / "grades_2026.json"

GRADES_URL = "https://schoology.kleinisd.net/grades/grades/report/2882945"
HOME_URL = "https://schoology.kleinisd.net/home"
CALENDAR_BASE = "https://schoology.kleinisd.net/calendar/84470972/2026-"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth", action="store_true", help="Open visible browser for manual login")
    parser.add_argument("--dry-run", action="store_true", help="Validate session without fetching all pages")
    return parser.parse_args()


def get_letter(pct: float | None) -> str:
    if pct is None:
        return "—"
    if pct >= 90:
        return "A"
    if pct >= 80:
        return "B"
    if pct >= 70:
        return "C"
    if pct >= 60:
        return "D"
    return "F"


def parse_percent(text: str) -> float | None:
    """Extract first percentage from a string."""
    m = re.search(r"(\d+\.?\d*)\s*%", text)
    return float(m.group(1)) if m else None


def parse_score(text: str) -> tuple[float | None, float | None]:
    """Parse 'X / Y' or 'X/Y' score format. Returns (earned, possible)."""
    m = re.search(r"(\d+\.?\d*)\s*/\s*(\d+\.?\d*)", text)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d+\.?\d*)", text)
    if m:
        return float(m.group(1)), None
    return None, None


def classify_assignment(name: str) -> str:
    name_l = name.lower()
    if any(w in name_l for w in ["cca", "common assessment", "benchmark", "staar", "district"]):
        return "test"
    if any(w in name_l for w in ["quiz", "qse", "q3", "q4"]):
        return "quiz"
    return "assignment"


def scrape_grades_page(page) -> dict:
    """Parse the Schoology grades report page text into structured data."""
    from playwright.sync_api import TimeoutError as PWTimeout

    logger.info("Navigating to grades report...")
    page.goto(GRADES_URL, wait_until="networkidle", timeout=30000)

    # Confirm we're on the right page
    title = page.title()
    if "login" in title.lower() or "sign in" in title.lower():
        raise RuntimeError("Session expired — re-run with --auth to log in again.")
    logger.info("Loaded: %s", title)

    text = page.inner_text("body")
    return parse_grades_text(text)


def parse_grades_text(text: str) -> dict:
    """
    Parse Schoology grades page body text into per-subject grade dicts.

    Schoology page structure (as plain text):
        COURSE NAME
        Course Grade  XX%
        QUARTER N: date range
        Grading Period (25%)  XX%
        Major Category (60%)  XX%
          Assignment Name  due date  SCORE / 100
        Minor Category (40%)  XX%
          Assignment Name  due date  SCORE / 100
    """
    config = json.loads(CONFIG_FILE.read_text())
    subject_ids = {s["label"].lower(): s["id"] for s in config["subjects"]}
    subject_ids.update({s["label_short"].lower(): s["id"] for s in config["subjects"]})

    subjects: dict[str, dict] = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    current_subject: str | None = None
    current_quarter: str | None = None
    current_assignments: list[dict] = []

    for i, line in enumerate(lines):
        # Detect subject headers — lines containing known subject keywords
        for label, sid in subject_ids.items():
            if label in line.lower() and len(line) < 80:
                if current_subject and current_subject != sid:
                    pass  # already recorded assignments below
                current_subject = sid
                if sid not in subjects:
                    subjects[sid] = {
                        "id": sid,
                        "current_pct": None,
                        "quarters": {},
                        "assignments": [],
                        "upcoming": [],
                        "source": "schoology",
                    }
                break

        if current_subject is None:
            continue

        # Overall course grade
        if "course grade" in line.lower():
            pct = parse_percent(line)
            if pct and subjects[current_subject]["current_pct"] is None:
                subjects[current_subject]["current_pct"] = pct

        # Quarter detection
        q_match = re.match(r"quarter\s+(\d)", line, re.IGNORECASE)
        if q_match:
            current_quarter = f"Q{q_match.group(1)}"

        # Quarter average
        if current_quarter and "grading period" in line.lower():
            pct = parse_percent(line)
            if pct:
                subjects[current_subject]["quarters"][current_quarter] = pct

        # Assignment row: name + score
        score_match = re.search(r"(\d+\.?\d*)\s*/\s*(\d+)", line)
        if score_match and current_quarter in ("Q3", "Q4"):
            earned = float(score_match.group(1))
            possible = float(score_match.group(2))
            pct = round(earned / possible * 100, 1) if possible > 0 else None
            # Assignment name is everything before the score
            asgn_name = line[:score_match.start()].strip()
            if len(asgn_name) > 3:
                subjects[current_subject]["assignments"].append({
                    "name": asgn_name,
                    "earned": earned,
                    "possible": possible,
                    "pct": pct,
                    "quarter": current_quarter,
                    "source": "schoology",
                    "type": classify_assignment(asgn_name),
                    "pending": False,
                })

        # Pending/awaiting
        if any(w in line.lower() for w in ["awaiting grade", "not yet graded", "pending"]):
            # Previous non-empty line is likely assignment name
            for j in range(i - 1, max(0, i - 4), -1):
                candidate = lines[j].strip()
                if len(candidate) > 5 and not any(c in candidate.lower() for c in ["quarter", "category", "grade"]):
                    subjects[current_subject]["assignments"].append({
                        "name": candidate,
                        "earned": None,
                        "possible": None,
                        "pct": None,
                        "quarter": current_quarter,
                        "source": "schoology",
                        "type": classify_assignment(candidate),
                        "pending": True,
                    })
                    break

    # Keep only last 10 assignments per subject (most recent first)
    for sid in subjects:
        subjects[sid]["assignments"] = subjects[sid]["assignments"][-10:][::-1]
        subjects[sid]["letter"] = get_letter(subjects[sid]["current_pct"])

    return subjects


def scrape_home_page(page) -> list[dict]:
    """Extract upcoming/overdue items from the Schoology home sidebar."""
    logger.info("Navigating to home page for upcoming items...")
    page.goto(HOME_URL, wait_until="networkidle", timeout=30000)
    text = page.inner_text("body")

    upcoming: list[dict] = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    in_upcoming = False
    for line in lines:
        if "upcoming" in line.lower() or "to do" in line.lower():
            in_upcoming = True
            continue
        if in_upcoming:
            if any(w in line.lower() for w in ["overdue", "completed", "recent activity"]):
                in_upcoming = False
                continue
            # Date pattern
            date_m = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d+", line, re.IGNORECASE)
            if date_m and len(line) > 5:
                upcoming.append({
                    "name": line[:date_m.start()].strip() or line,
                    "date_raw": date_m.group(0),
                    "type": classify_assignment(line),
                })
        if len(upcoming) >= 10:
            break

    return upcoming


def scrape_calendar(page, months: list[str]) -> list[dict]:
    """Scrape calendar events for given months (e.g. ['03', '04'])."""
    events: list[dict] = []
    for mm in months:
        url = CALENDAR_BASE + mm
        logger.info("Scraping calendar for month %s...", mm)
        page.goto(url, wait_until="networkidle", timeout=30000)
        text = page.inner_text("body")
        for line in text.splitlines():
            line = line.strip()
            if len(line) > 5 and re.search(r"\d", line):
                events.append({"month": mm, "raw": line})
    return events


def main() -> None:
    args = parse_args()
    TMP_DIR.mkdir(exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        headless = not args.auth
        browser = pw.chromium.launch_persistent_context(
            str(BROWSER_STATE),
            headless=headless,
            args=["--no-sandbox"],
        )
        page = browser.new_page() if args.auth else browser.pages[0] if browser.pages else browser.new_page()

        if args.auth:
            logger.info("Opening browser for manual login...")
            page.goto("https://schoology.kleinisd.net")
            logger.info("Please log in, then close this browser window.")
            input("Press Enter here after you've logged in and the page has loaded... ")
            browser.storage_state(path=str(BROWSER_STATE / "state.json"))
            logger.info("Session saved. Re-run without --auth for headless mode.")
            browser.close()
            return

        if args.dry_run:
            page.goto(GRADES_URL, timeout=15000)
            title = page.title()
            logger.info("Session check — page title: %s", title)
            if "login" in title.lower():
                logger.error("Session expired. Re-run with --auth.")
            else:
                logger.info("Session valid.")
            browser.close()
            return

        # Full fetch
        subjects = scrape_grades_page(page)
        upcoming = scrape_home_page(page)

        today = date.today()
        months = [f"{today.month:02d}"]
        if today.month < 12:
            months.append(f"{today.month + 1:02d}")
        calendar_events = scrape_calendar(page, months)

        browser.close()

    out = {
        "generated_at": datetime.now().isoformat(),
        "fetched_date": date.today().isoformat(),
        "subjects": subjects,
        "upcoming": upcoming,
        "calendar_events": calendar_events,
    }

    OUT_FILE.write_text(json.dumps(out, indent=2))
    logger.info("Wrote Schoology data for %d subjects to %s", len(subjects), OUT_FILE)


if __name__ == "__main__":
    main()

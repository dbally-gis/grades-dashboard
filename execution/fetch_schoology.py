#!/usr/bin/env python3
"""
Script: fetch_schoology
Purpose: Log into Schoology via Klein ISD ClassLink SSO and scrape Julia's
         grades and upcoming assignments using BeautifulSoup HTML parsing.

Usage:
    python3 execution/fetch_schoology.py            # full fetch
    python3 execution/fetch_schoology.py --dry-run  # login check only
    python3 execution/fetch_schoology.py --headed   # show browser window

Environment (.env):
    KLEINISD_USER  — Klein ISD username (e.g. S736263)
    KLEINISD_PASS  — Klein ISD password

Output:
    .tmp/schoology_grades.json
"""

import argparse
import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TMP_DIR = PROJECT_ROOT / ".tmp"
OUT_FILE = TMP_DIR / "schoology_grades.json"
CONFIG_FILE = PROJECT_ROOT / "data" / "grades_2026.json"

CLASSLINK_URL = "https://login.classlink.com/my/kleinisd"
GRADES_URL = "https://schoology.kleinisd.net/grades/grades"
HOME_URL = "https://schoology.kleinisd.net/home"
CALENDAR_BASE = "https://schoology.kleinisd.net/calendar/84470972/2026-"

# Course name fragments → config subject id
SUBJECT_MAP = {
    "ela reading":   "ela",
    "math 6 adv":    "math",
    "math advanced": "math",
    "pe girls":      "pe",
    "science 6":     "science",
    "soc stud":      "social",
    "theatre arts":  "theatre",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    return parser.parse_args()


def get_letter(pct: float | None) -> str:
    if pct is None: return "—"
    if pct >= 90: return "A"
    if pct >= 80: return "B"
    if pct >= 70: return "C"
    if pct >= 60: return "D"
    return "F"


def classify_assignment(name: str) -> str:
    n = name.lower()
    if any(w in n for w in ["cca", "common assessment", "benchmark", "staar", "district"]):
        return "test"
    if any(w in n for w in ["quiz", "qse", "test"]):
        return "quiz"
    return "assignment"


def match_subject(course_title: str) -> str | None:
    t = course_title.lower()
    for fragment, sid in SUBJECT_MAP.items():
        if fragment in t:
            return sid
    return None


def parse_score(text: str) -> tuple[float | None, float | None]:
    """Parse 'X / Y' → (earned, possible). Returns (None, None) if not a real grade."""
    m = re.search(r"(\d+\.?\d*)\s*/\s*(\d+\.?\d*)", text)
    if not m:
        return None, None
    earned, possible = float(m.group(1)), float(m.group(2))
    # Skip zero-possible items (extra credit / info columns)
    if possible == 0:
        return None, None
    return earned, possible


def parse_grades_html(html: str, config: dict) -> dict:
    """
    Parse Schoology gradebook HTML using BeautifulSoup.

    Structure:
      div#s-js-gradebook-course-XXXXXXX
        → tr.course-row  → td: "92.5%"  (overall grade)
        → tr (quarter)   → th: "QUARTER N: ..."  → td: "87.2%"
        → tr (category)  → th: "Major/Minor Category(XX%)"  → td: "90%"
        → tr (assignment) → th: assignment name  → td: "85 / 100"
    """
    soup = BeautifulSoup(html, "html.parser")
    course_divs = soup.find_all("div", id=lambda x: x and "s-js-gradebook-course-" in x)
    logger.info("Found %d course sections in HTML.", len(course_divs))

    subjects: dict[str, dict] = {}

    for course_div in course_divs:
        # Course title
        title_el = course_div.find("a", class_="sExtlink-processed")
        if not title_el:
            continue
        title = title_el.get_text(separator=" ", strip=True)

        sid = match_subject(title)
        if sid is None:
            continue  # skip non-subject courses (campus, library, etc.)

        subjects[sid] = {
            "id": sid,
            "current_pct": None,
            "quarters": {},
            "assignments": [],
            "source": "schoology",
        }

        # Process all rows in the table
        rows = course_div.find_all("tr")
        current_quarter: str | None = None
        in_current_quarter = False

        for row in rows:
            cls = row.get("class", [])
            header_cell = row.find("th")
            grade_cell = row.find("td")

            header_text = header_cell.get_text(separator=" ", strip=True) if header_cell else ""
            grade_text = grade_cell.get_text(separator=" ", strip=True) if grade_cell else ""

            # Overall course grade row
            if "course-row" in cls:
                m = re.search(r"(\d+\.?\d*)\s*%", grade_text)
                if m:
                    subjects[sid]["current_pct"] = float(m.group(1))
                continue

            # Quarter row: "QUARTER N: ..."
            q_match = re.match(r"QUARTER\s+(\d)", header_text, re.IGNORECASE)
            if q_match:
                current_quarter = f"Q{q_match.group(1)}"
                in_current_quarter = current_quarter in ("Q3", "Q4")
                m = re.search(r"(\d+\.?\d*)\s*%", grade_text)
                if m:
                    subjects[sid]["quarters"][current_quarter] = float(m.group(1))
                continue

            # Category row: "Major/Minor Category(XX%)"
            if "category" in header_text.lower() and "%" in header_text:
                continue

            # Assignment row: anything else with a score
            if not in_current_quarter:
                continue

            name = re.sub(r"\s*(assignment|test-quiz|assessment|grade_column)\s*", " ", header_text, flags=re.IGNORECASE).strip()
            # Remove "Due MM/DD/YY" suffixes and "Note: ..." suffixes
            name = re.sub(r"\s*Due\s+\d+/\d+/\d+.*$", "", name).strip()
            name = re.sub(r"\s*Note:.*$", "", name).strip()
            name = re.sub(r"\s*Click to launch.*$", "", name).strip()

            if not name or len(name) < 3:
                continue

            # Pending (no score or "—")
            if not grade_text or grade_text == "—" or "awaiting" in grade_text.lower():
                subjects[sid]["assignments"].append({
                    "name": name, "earned": None, "possible": None, "pct": None,
                    "quarter": current_quarter, "source": "schoology",
                    "type": classify_assignment(name), "pending": True,
                })
                continue

            earned, possible = parse_score(grade_text)
            if earned is not None and possible is not None:
                pct = round(earned / possible * 100, 1)
                subjects[sid]["assignments"].append({
                    "name": name, "earned": earned, "possible": possible, "pct": pct,
                    "quarter": current_quarter, "source": "schoology",
                    "type": classify_assignment(name), "pending": False,
                })

        # Most recent first, cap at 15
        subjects[sid]["assignments"] = subjects[sid]["assignments"][::-1][:15]
        subjects[sid]["letter"] = get_letter(subjects[sid]["current_pct"])

    return subjects


def parse_upcoming(soup: BeautifulSoup) -> list[dict]:
    upcoming: list[dict] = []
    # Schoology home sidebar: look for elements with upcoming/todo context
    for el in soup.find_all(string=re.compile(r"upcoming|to.do|due", re.IGNORECASE)):
        parent = el.parent
        if parent:
            items = parent.find_all_next("li", limit=10)
            for item in items:
                txt = item.get_text(strip=True)
                date_m = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+\d+", txt, re.IGNORECASE)
                if date_m and len(txt) > 5:
                    upcoming.append({
                        "name": txt[:date_m.start()].strip() or txt[:60],
                        "date_raw": date_m.group(0),
                        "type": classify_assignment(txt),
                    })
            if upcoming:
                break
    return upcoming[:10]


def main() -> None:
    args = parse_args()
    user = os.getenv("KLEINISD_USER")
    password = os.getenv("KLEINISD_PASS")
    if not user or not password:
        raise EnvironmentError("KLEINISD_USER and KLEINISD_PASS must be set in .env")

    config = json.loads(CONFIG_FILE.read_text())
    TMP_DIR.mkdir(exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        page = browser.new_page()

        # Login via ClassLink
        logger.info("Logging in via ClassLink...")
        page.goto(CLASSLINK_URL, wait_until="networkidle", timeout=20000)
        page.fill("#username", user)
        page.fill("#password", password)
        page.click("button:has-text('Sign In')")
        page.wait_for_url(lambda u: "myapps.classlink.com" in u or "schoology" in u, timeout=30000)
        logger.info("Authenticated — at: %s", page.url)

        if args.dry_run:
            logger.info("Dry run complete. Login OK.")
            browser.close()
            return

        # Grades page
        logger.info("Loading grades page...")
        page.goto(GRADES_URL, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(2000)
        grades_html = page.content()
        (TMP_DIR / "schoology_page.html").write_text(grades_html)
        logger.info("Grades HTML: %d bytes", len(grades_html))

        subjects = parse_grades_html(grades_html, config)

        # Home page for upcoming
        logger.info("Loading home page for upcoming items...")
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        home_html = page.content()
        home_soup = BeautifulSoup(home_html, "html.parser")
        upcoming = parse_upcoming(home_soup)
        logger.info("Found %d upcoming items.", len(upcoming))

        # Calendar
        today = date.today()
        months = [f"{today.month:02d}"]
        if today.month < 12:
            months.append(f"{today.month + 1:02d}")
        calendar_events: list[dict] = []
        for mm in months:
            logger.info("Scraping calendar month %s...", mm)
            page.goto(CALENDAR_BASE + mm, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
            cal_text = page.inner_text("body")
            for line in cal_text.splitlines():
                line = line.strip()
                if len(line) > 5 and re.search(r"\d", line):
                    calendar_events.append({"month": mm, "raw": line})

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

    for sid, s in subjects.items():
        asgn_count = len([a for a in s["assignments"] if not a["pending"]])
        pending_count = len([a for a in s["assignments"] if a["pending"]])
        logger.info("  %-10s  %s  (%d graded, %d pending)",
                    sid, f"{s['current_pct']}%" if s['current_pct'] else "N/A",
                    asgn_count, pending_count)


if __name__ == "__main__":
    main()

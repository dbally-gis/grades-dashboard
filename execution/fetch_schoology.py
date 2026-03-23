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
from datetime import date, datetime, timedelta
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
# Calendar AJAX base — month suffix (YYYY-MM) + timestamps appended at runtime
CALENDAR_AJAX_BASE = "https://schoology.kleinisd.net/calendar/84470972/{month}?ajax=1&start={start}&end={end}"

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
            # Capture "Due MM/DD/YY" before stripping it
            due_date_str: str | None = None
            due_m = re.search(r"Due\s+(\d+)/(\d+)/(\d+)", name, re.IGNORECASE)
            if due_m:
                mo, dy, yr = int(due_m.group(1)), int(due_m.group(2)), int(due_m.group(3))
                full_yr = 2000 + yr if yr < 100 else yr
                try:
                    due_date_str = date(full_yr, mo, dy).isoformat()
                except ValueError:
                    pass
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
                    "due_date": due_date_str,
                })
                continue

            earned, possible = parse_score(grade_text)
            if earned is not None and possible is not None:
                pct = round(earned / possible * 100, 1)
                subjects[sid]["assignments"].append({
                    "name": name, "earned": earned, "possible": possible, "pct": pct,
                    "quarter": current_quarter, "source": "schoology",
                    "type": classify_assignment(name), "pending": False,
                    "due_date": due_date_str,
                })

        # Most recent first, cap at 15
        subjects[sid]["assignments"] = subjects[sid]["assignments"][::-1][:15]
        subjects[sid]["letter"] = get_letter(subjects[sid]["current_pct"])

    return subjects


def fetch_calendar_events(page, weeks_ahead: int = 4) -> list[dict]:
    """
    Fetch upcoming assignment events via Schoology's calendar AJAX API.
    Calls each month's endpoint (current + next) since the API is month-scoped.
    Returns list of {title, course, subject_id, due_date, due_time, type, all_day}.
    Only includes events on or after today, up to `weeks_ahead` weeks out.
    """
    today = date.today()
    end_date = today + timedelta(weeks=weeks_ahead)
    today_str = today.isoformat()

    # Collect unique months to query
    months_to_fetch: list[str] = []
    cursor = today.replace(day=1)
    while cursor <= end_date:
        months_to_fetch.append(cursor.strftime("%Y-%m"))
        # Advance to next month
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)

    # FullCalendar shows ~5 weeks per month; use wide window so Schoology returns all
    start_ts = int(datetime(today.year, today.month, 1).timestamp()) - 7 * 86400
    end_ts = int(datetime(end_date.year, end_date.month, end_date.day, 23, 59).timestamp()) + 7 * 86400

    raw_seen: set[str] = set()
    all_raw: list[dict] = []
    for month in months_to_fetch:
        url = CALENDAR_AJAX_BASE.format(month=month, start=start_ts, end=end_ts)
        logger.info("Fetching calendar %s...", month)
        try:
            resp = page.request.get(url, timeout=15000)
            if resp.status == 200:
                for evt in resp.json():
                    uid = evt.get("id", "") or evt.get("content_id", "")
                    if uid and uid not in raw_seen:
                        raw_seen.add(uid)
                        all_raw.append(evt)
        except Exception as exc:
            logger.warning("Calendar fetch error for %s: %s", month, exc)

    logger.info("Unique raw calendar events: %d", len(all_raw))

    events: list[dict] = []
    for evt in all_raw:
        start_str = evt.get("start", "")
        if not start_str or start_str[:10] < today_str:
            continue

        title = re.sub(r"<[^>]+>", "", evt.get("title", "")).strip()
        if not title:
            continue

        content_title = evt.get("content_title", "")
        course_name = content_title.split(":")[0].strip()
        subject_id = match_subject(course_name)

        e_type = evt.get("e_type", "assignment")
        atype = classify_assignment(f"{title} {e_type}")

        events.append({
            "title": title,
            "course": course_name,
            "subject_id": subject_id,
            "due_date": start_str[:10],
            "due_time": start_str[11:16] if len(start_str) > 10 else "",
            "type": atype,
            "all_day": bool(evt.get("allDay", False)),
        })

    events.sort(key=lambda e: (e["due_date"], e["due_time"]))
    logger.info("Upcoming events (≥ today, next %d weeks): %d", weeks_ahead, len(events))
    return events


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

        # Calendar AJAX — upcoming events for next 4 weeks
        upcoming_events = fetch_calendar_events(page, weeks_ahead=4)

        browser.close()

    out = {
        "generated_at": datetime.now().isoformat(),
        "fetched_date": date.today().isoformat(),
        "subjects": subjects,
        "upcoming_events": upcoming_events,
    }
    OUT_FILE.write_text(json.dumps(out, indent=2))
    logger.info("Wrote Schoology data for %d subjects to %s", len(subjects), OUT_FILE)

    for sid, s in subjects.items():
        asgn_count = len([a for a in s["assignments"] if not a["pending"]])
        pending_count = len([a for a in s["assignments"] if a["pending"]])
        logger.info("  %-10s  %s  (%d graded, %d pending)",
                    sid, f"{s['current_pct']}%" if s['current_pct'] else "N/A",
                    asgn_count, pending_count)

    # Print upcoming events summary
    from itertools import groupby
    for due_date, evts in groupby(upcoming_events, key=lambda e: e["due_date"]):
        evts_list = list(evts)
        logger.info("  %s: %d event(s) — %s",
                    due_date, len(evts_list),
                    ", ".join(e["title"][:30] for e in evts_list[:3]))


if __name__ == "__main__":
    main()

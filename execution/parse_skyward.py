#!/usr/bin/env python3
"""
Script: parse_skyward
Purpose: Fetch Julia's grades from Skyward via Playwright login (primary) or
         parse a manually-exported PDF (fallback).

Usage:
    python3 execution/parse_skyward.py            # Playwright login (default)
    python3 execution/parse_skyward.py --dry-run  # login only, no parse
    python3 execution/parse_skyward.py --headed   # show browser window
    python3 execution/parse_skyward.py --pdf /path/to/file.pdf  # PDF fallback

Environment (.env):
    KLEINISD_USER  — Klein ISD username (e.g. S736263)
    KLEINISD_PASS  — Klein ISD password

Output:
    .tmp/skyward_grades.json

Skyward table column order:
    PC1 PR1 PC2 PR2 QC1 Q1 PC3 PR3 PC4 PR4 QC2 Q2 S1
    PC5 PR5 PC6 PR6 QC3 Q3 PC7 PR7 PC8 PR8 QC4 Q4 S2 YR
    QCn = citizenship, Qn = quarter average
"""

import argparse
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TMP_DIR = PROJECT_ROOT / ".tmp"
OUT_FILE = TMP_DIR / "skyward_grades.json"
CONFIG_FILE = PROJECT_ROOT / "data" / "grades_2026.json"

SKYWARD_LOGIN = "https://skyward.kleinisd.net/scripts/wsisa.dll/WService=wsEAplus/seplog01.w"
SKYWARD_GRADEBOOK = "https://skyward.kleinisd.net/scripts/wsisa.dll/WService=wsEAplus/sfgradebook001.w"

# Skyward course name fragments → config subject id
SUBJECT_MAP = {
    "ela reading":   "ela",
    "math 6 adv":    "math",
    "math advanced": "math",
    "pe girls":      "pe",
    "science 6":     "science",
    "soc stud":      "social",
    "theatre arts":  "theatre",
}

# Skyward column order
COLS = ["PC1","PR1","PC2","PR2","QC1","Q1","PC3","PR3","PC4","PR4","QC2","Q2","S1",
        "PC5","PR5","PC6","PR6","QC3","Q3","PC7","PR7","PC8","PR8","QC4","Q4","S2","YR"]
Q3_IDX = COLS.index("Q3")   # 18
Q4_IDX = COLS.index("Q4")   # 24


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    parser.add_argument("--pdf", type=Path, default=None,
                        help="Parse a manually-exported PDF instead of logging in")
    return parser.parse_args()


def get_letter(pct: float | None) -> str:
    if pct is None:
        return "—"
    if pct >= 90: return "A"
    if pct >= 80: return "B"
    if pct >= 70: return "C"
    if pct >= 60: return "D"
    return "F"


def match_subject(name: str) -> str | None:
    name_l = name.lower()
    for key, sid in SUBJECT_MAP.items():
        if key in name_l:
            return sid
    return None


def parse_gradebook_html(html: str, config: dict) -> dict[str, dict]:
    """
    Parse Skyward gradebook HTML into per-subject grade dicts.

    Structure:
      - Course names in <span class="classDesc"><a>COURSE NAME</a></span>
      - Each course name is followed (within ~50KB) by its section ID:
        <tr group-parent="436677_SECTION_0_N" ...>  (one row per quarter group)
      - The first 6 unique group-parent rows correspond to the 6 courses.
      - Each row has 27 TD cells matching COLS order; Q3 at index 18, Q4 at 24.
    """
    current_quarter = f"Q{config['meta']['current_quarter']}"

    # --- Map section IDs to course names ---
    names_and_pos = [(m.start(), m.group(1))
                     for m in re.finditer(r'classDesc[^>]*><a[^>]*>([^<]+)</a>', html)]

    section_map: dict[str, str] = {}  # section_id -> course_name
    seen_sids: list[str] = []
    for pos, name in names_and_pos:
        chunk = html[pos:pos + 50_000]
        m = re.search(r'436677_(\d+)_0_\d+', chunk)
        if m:
            sid = m.group(1)
            if sid not in section_map:
                section_map[sid] = name
                seen_sids.append(sid)

    logger.info("Section → course mapping: %s", section_map)

    # --- Parse grade rows ---
    soup = BeautifulSoup(html, "html.parser")
    parent_rows = soup.find_all("tr", attrs={"group-parent": True})

    results: dict[str, dict] = {}
    for row in parent_rows:
        gp = row.get("group-parent", "")
        m = re.match(r"436677_(\d+)_", gp)
        if not m:
            continue
        sid = m.group(1)
        course_name = section_map.get(sid, "")
        subject_id = match_subject(course_name)
        if subject_id is None:
            continue

        # Only update if we haven't found this subject yet (take the first / current-quarter row)
        if subject_id in results:
            continue

        cells = row.find_all("td")
        vals = [c.get_text(strip=True) for c in cells]

        def col_val(idx: int) -> float | None:
            if idx >= len(vals):
                return None
            v = vals[idx]
            if not v or not re.search(r"\d", v):
                return None
            try:
                return float(v)
            except ValueError:
                return None

        q3 = col_val(Q3_IDX)
        q4 = col_val(Q4_IDX)

        # Use Q4 if available (current quarter), otherwise Q3
        pct = q4 if q4 is not None else q3
        quarter = "Q4" if q4 is not None else "Q3"

        results[subject_id] = {
            "id": subject_id,
            "pct": pct,
            "letter": get_letter(pct),
            "quarter": quarter,
            "quarters": {"Q3": q3, "Q4": q4},
            "assignments": [],
            "source": "skyward",
        }
        logger.info("  %-10s  %s  (%s)", subject_id, f"{pct}%" if pct else "N/A", quarter)

    return results


def fetch_skyward(user: str, password: str, args: argparse.Namespace) -> dict[str, dict]:
    """Login to Skyward and scrape the gradebook."""
    from playwright.sync_api import sync_playwright

    config = json.loads(CONFIG_FILE.read_text())

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context()
        page = context.new_page()

        # Collect popup pages
        popup_pages: list = []
        context.on("page", lambda p: popup_pages.append(p))

        # --- Login ---
        logger.info("Loading Skyward login page...")
        page.goto(SKYWARD_LOGIN, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1000)

        page.select_option("#cUserRole", "family/student")
        page.fill("#login", user)
        page.fill("#password", password)

        # Capture login response to verify success
        login_ok = [False]
        def on_response(resp):
            if "skyporthttp" in resp.url and resp.status == 200:
                try:
                    body = resp.text()
                    if "^" in body and "sfhome01" in body:
                        login_ok[0] = True
                except Exception:
                    pass
        page.on("response", on_response)

        page.click("#bLogin")
        page.wait_for_timeout(5000)

        if not login_ok[0]:
            raise RuntimeError("Skyward login failed — check credentials")

        if not popup_pages:
            raise RuntimeError("Skyward home popup did not open — login may have failed")

        home = popup_pages[0]
        home.wait_for_load_state("domcontentloaded", timeout=15000)
        home.wait_for_timeout(2000)
        logger.info("Logged in as: %s", home.title())

        if args.dry_run:
            logger.info("Dry run complete. Login OK.")
            browser.close()
            return {}

        # --- Navigate to Gradebook ---
        logger.info("Navigating to gradebook...")
        home.click("a:has-text('Gradebook')", timeout=10000)
        home.wait_for_timeout(4000)
        logger.info("Gradebook URL: %s", home.url)

        html = home.content()
        (TMP_DIR / "skyward_gradebook.html").write_text(html)
        logger.info("Gradebook HTML: %d bytes", len(html))

        browser.close()

    return parse_gradebook_html(html, config)


def parse_pdf(pdf_path: Path, config: dict) -> dict[str, dict]:
    """Parse a manually-exported Skyward PDF."""
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber not installed. Run: pip3 install pdfplumber")

    current_quarter = f"Q{config['meta']['current_quarter']}"
    results: dict[str, dict] = {}

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            logger.info("Processing PDF page %d...", page_num)
            tables = page.extract_tables()
            for table in tables:
                _parse_pdf_table(table, results, current_quarter, config)
            text = page.extract_text() or ""
            _parse_pdf_text(text, results, current_quarter)

    logger.info("Parsed %d subjects from PDF.", len(results))
    return results


def _parse_pdf_table(table: list[list], results: dict, current_quarter: str, config: dict) -> None:
    if not table or not table[0]:
        return
    header_row = None
    header_idx = 0
    for i, row in enumerate(table):
        row_text = " ".join(str(c or "") for c in row)
        if "QC3" in row_text or "Q3" in row_text or "Assignment" in row_text:
            header_row = row
            header_idx = i
            break

    current_subject_id: str | None = None
    for row in table[header_idx:]:
        if not row:
            continue
        first_cell = str(row[0] or "").strip()
        sid = match_subject(first_cell)
        if sid:
            current_subject_id = sid
            if sid not in results:
                results[sid] = {"id": sid, "quarter": current_quarter, "pct": None,
                                "letter": "—", "quarters": {}, "assignments": [], "source": "skyward"}
        if current_subject_id is None:
            continue
        if header_row:
            for col_i, hdr_cell in enumerate(header_row):
                hdr = str(hdr_cell or "").strip().upper()
                if hdr in (current_quarter, f"QC{current_quarter[1]}", "QC3", "Q3"):
                    if col_i < len(row):
                        cell = str(row[col_i] or "").strip()
                        m = re.search(r"(\d+\.?\d*)", cell)
                        if m:
                            pct = float(m.group(1))
                            if 0 < pct <= 100:
                                results[current_subject_id]["pct"] = pct
                                results[current_subject_id]["letter"] = get_letter(pct)


def _parse_pdf_text(text: str, results: dict, current_quarter: str) -> None:
    current_subject_id: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sid = match_subject(line)
        if sid:
            current_subject_id = sid
            if sid not in results:
                results[sid] = {"id": sid, "quarter": current_quarter, "pct": None,
                                "letter": "—", "quarters": {}, "assignments": [], "source": "skyward"}
        if current_subject_id is None:
            continue
        q_match = re.search(r"(?:QC?[34]|Q[34])\s+(\d+\.?\d*)\s*%?", line, re.IGNORECASE)
        if q_match and results[current_subject_id]["pct"] is None:
            pct = float(q_match.group(1))
            if 0 < pct <= 100:
                results[current_subject_id]["pct"] = pct
                results[current_subject_id]["letter"] = get_letter(pct)


def main() -> None:
    args = parse_args()
    TMP_DIR.mkdir(exist_ok=True)
    config = json.loads(CONFIG_FILE.read_text())

    if args.pdf:
        if not args.pdf.exists():
            logger.error("PDF not found: %s", args.pdf)
            return
        logger.info("Parsing Skyward PDF: %s", args.pdf)
        subjects = parse_pdf(args.pdf, config)
    else:
        user = os.getenv("KLEINISD_USER")
        password = os.getenv("KLEINISD_PASS")
        if not user or not password:
            raise EnvironmentError("KLEINISD_USER and KLEINISD_PASS must be set in .env")
        subjects = fetch_skyward(user, password, args)

    if not subjects:
        logger.warning("No Skyward grades extracted.")
        return

    out = {
        "generated_at": datetime.now().isoformat(),
        "source": "skyward_fetch" if not args.pdf else "skyward_pdf",
        "subjects": subjects,
    }
    OUT_FILE.write_text(json.dumps(out, indent=2))
    logger.info("Wrote Skyward data for %d subjects to %s", len(subjects), OUT_FILE)

    for sid, s in subjects.items():
        logger.info("  %-10s  %s  (%s)", sid, f"{s['pct']}%" if s['pct'] else "N/A", s['quarter'])


if __name__ == "__main__":
    main()

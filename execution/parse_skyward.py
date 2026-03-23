#!/usr/bin/env python3
"""
Script: parse_skyward
Purpose: Parse a Skyward gradebook PDF exported by the parent and extract
         per-subject Q3/Q4 grades. Skyward grades are authoritative.

Usage:
    python3 execution/parse_skyward.py [--pdf /path/to/file.pdf]
    python3 execution/parse_skyward.py  # reads .tmp/skyward.pdf by default

Setup:
    1. Log into skyward.kleinisd.net
    2. Go to the gradebook view
    3. Print / Export as PDF → save to .tmp/skyward.pdf
    4. Run this script

Output:
    .tmp/skyward_grades.json — { subject_id: { pct, letter, quarter, assignments } }

PDF column structure (from spec):
    Assignment | PC1 | PR1 | PC2 | PR2 | QC1 | Q1 | PC3 | PR3 | PC4 | PR4 | QC2 | Q2 | S1 |
    PC5 | PR5 | PC6 | PR6 | QC3 | Q3 | ...
    QC1=Q1avg, QC2=Q2avg, QC3=Q3avg, Q4 avg when available
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
DEFAULT_PDF = TMP_DIR / "skyward.pdf"
OUT_FILE = TMP_DIR / "skyward_grades.json"
CONFIG_FILE = PROJECT_ROOT / "data" / "grades_2026.json"

# Quarter column markers in Skyward PDF text
QUARTER_COLS = ["QC1", "Q1", "QC2", "Q2", "S1", "QC3", "Q3", "QC4", "Q4"]

# Skyward subject name → config subject id (fuzzy match keys)
SUBJECT_MAP = {
    "math": "math",
    "ela": "ela",
    "reading": "ela",
    "science": "science",
    "soc stud": "social",
    "social studies": "social",
    "pe": "pe",
    "physical ed": "pe",
    "theatre": "theatre",
    "theater": "theatre",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF,
                        help=f"Path to Skyward PDF (default: {DEFAULT_PDF})")
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


def match_subject(name: str) -> str | None:
    name_l = name.lower()
    for key, sid in SUBJECT_MAP.items():
        if key in name_l:
            return sid
    return None


def parse_pdf(pdf_path: Path) -> dict[str, dict]:
    """
    Extract per-subject quarter grades from Skyward PDF.

    Skyward PDF structure (varies slightly by export settings):
    - Each subject appears as a section header followed by assignment rows
    - Quarter averages appear in QC1/QC2/QC3/QC4 columns or in summary rows
    - We primarily look for the summary row with "Q3" or current quarter average
    """
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber not installed. Run: pip3 install pdfplumber")

    results: dict[str, dict] = {}
    config = json.loads(CONFIG_FILE.read_text())
    current_quarter = f"Q{config['meta']['current_quarter']}"

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            logger.info("Processing page %d...", page_num)

            # Try structured table extraction first
            tables = page.extract_tables()
            for table in tables:
                _parse_table(table, results, current_quarter, config)

            # Fallback: raw text extraction
            text = page.extract_text() or ""
            _parse_text(text, results, current_quarter)

    logger.info("Parsed %d subjects from PDF.", len(results))
    return results


def _parse_table(table: list[list], results: dict, current_quarter: str, config: dict) -> None:
    """Parse a pdfplumber table (list of rows, each row a list of cells)."""
    if not table or not table[0]:
        return

    # Detect header row to find quarter column index
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

        # Subject header row
        sid = match_subject(first_cell)
        if sid:
            current_subject_id = sid
            if sid not in results:
                results[sid] = {"id": sid, "quarter": current_quarter, "pct": None,
                                "letter": "—", "assignments": [], "source": "skyward"}

        if current_subject_id is None:
            continue

        # Try to find quarter average in this row
        row_text = " ".join(str(c or "") for c in row)
        # Look for pattern: a cell that's a percentage near a Q3/QC3 marker
        if header_row:
            for col_i, header_cell in enumerate(header_row):
                hdr = str(header_cell or "").strip().upper()
                if hdr in (current_quarter, f"QC{current_quarter[1]}", "QC3", "Q3"):
                    if col_i < len(row):
                        cell = str(row[col_i] or "").strip()
                        m = re.search(r"(\d+\.?\d*)", cell)
                        if m:
                            pct = float(m.group(1))
                            if 0 < pct <= 100:
                                results[current_subject_id]["pct"] = pct
                                results[current_subject_id]["letter"] = get_letter(pct)

        # Parse individual assignment rows: first cell = name, look for score cells
        if first_cell and len(first_cell) > 3 and not match_subject(first_cell):
            scores = []
            for cell in row[1:]:
                cell_s = str(cell or "").strip()
                m = re.search(r"(\d+\.?\d*)", cell_s)
                if m and "/" not in cell_s:
                    scores.append(float(m.group(1)))
            if scores:
                earned = scores[0]
                results[current_subject_id]["assignments"].append({
                    "name": first_cell,
                    "earned": earned,
                    "possible": 100.0,
                    "pct": earned,
                    "source": "skyward",
                    "pending": False,
                })


def _parse_text(text: str, results: dict, current_quarter: str) -> None:
    """Fallback: parse raw text for subject averages when table parsing fails."""
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
                                "letter": "—", "assignments": [], "source": "skyward"}

        if current_subject_id is None:
            continue

        # Look for "Q3  89%" or "QC3  89%" pattern
        q_match = re.search(r"(?:QC?[34]|Q[34])\s+(\d+\.?\d*)\s*%?", line, re.IGNORECASE)
        if q_match and results[current_subject_id]["pct"] is None:
            pct = float(q_match.group(1))
            if 0 < pct <= 100:
                results[current_subject_id]["pct"] = pct
                results[current_subject_id]["letter"] = get_letter(pct)


def main() -> None:
    args = parse_args()
    TMP_DIR.mkdir(exist_ok=True)

    if not args.pdf.exists():
        logger.warning("No Skyward PDF found at %s", args.pdf)
        logger.warning("Export from skyward.kleinisd.net → gradebook → Print/PDF → save to .tmp/skyward.pdf")
        return

    logger.info("Parsing Skyward PDF: %s", args.pdf)
    results = parse_pdf(args.pdf)

    out = {
        "generated_at": datetime.now().isoformat(),
        "pdf_path": str(args.pdf),
        "subjects": results,
    }

    OUT_FILE.write_text(json.dumps(out, indent=2))
    logger.info("Wrote Skyward data for %d subjects to %s", len(results), OUT_FILE)


if __name__ == "__main__":
    main()

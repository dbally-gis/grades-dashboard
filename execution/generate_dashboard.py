#!/usr/bin/env python3
"""
Script: generate_dashboard
Purpose: Merge Schoology + Skyward data (Skyward priority) and produce three
         self-contained HTML dashboard editions:
           index.html        — Dad's detailed view (expandable cards)
           wife.html         — Mom's streamlined 30-second scan
           julia.html        — Julia's colorful motivational brief

Usage:
    python3 execution/generate_dashboard.py
    python3 execution/generate_dashboard.py --dry-run

Inputs:
    data/grades_2026.json          — static config (subjects, colors, meta)
    .tmp/schoology_grades.json     — live Schoology data (optional)
    .tmp/skyward_grades.json       — Skyward PDF data (optional; priority)

Outputs:
    index.html, wife.html, julia.html
"""

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TMP_DIR = PROJECT_ROOT / ".tmp"
CONFIG_FILE = PROJECT_ROOT / "data" / "grades_2026.json"
SCHOOLOGY_FILE = TMP_DIR / "schoology_grades.json"
SKYWARD_FILE = TMP_DIR / "skyward_grades.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
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


def score_class(pct: float | None) -> str:
    if pct is None:
        return ""
    if pct >= 85:
        return "score-hi"
    if pct >= 70:
        return "score-md"
    return "score-lo"


def s_class(pct: float | None) -> str:
    """Small score class variant."""
    if pct is None:
        return ""
    if pct >= 85:
        return "s-hi"
    if pct >= 70:
        return "s-md"
    return "s-lo"


def type_tag_html(atype: str) -> str:
    cls = {"quiz": "tt-quiz", "test": "tt-test", "assignment": "tt-asgn"}.get(atype, "tt-asgn")
    label = {"quiz": "Quiz", "test": "Test/CCA", "assignment": "Assignment"}.get(atype, "Assignment")
    return f'<span class="type-tag {cls}">{label}</span>'


def load_data() -> tuple[dict, dict, dict]:
    """Load config + live data files. Returns (config, schoology, skyward)."""
    config = json.loads(CONFIG_FILE.read_text())

    schoology: dict = {}
    if SCHOOLOGY_FILE.exists():
        raw = json.loads(SCHOOLOGY_FILE.read_text())
        schoology = raw.get("subjects", {})
        logger.info("Loaded Schoology data: %d subjects, fetched %s",
                    len(schoology), raw.get("fetched_date", "unknown"))
    else:
        logger.warning("No Schoology data found at %s — using fallback grades.", SCHOOLOGY_FILE)

    skyward: dict = {}
    if SKYWARD_FILE.exists():
        raw = json.loads(SKYWARD_FILE.read_text())
        skyward = raw.get("subjects", {})
        logger.info("Loaded Skyward data: %d subjects", len(skyward))
    else:
        logger.info("No Skyward PDF data found — Schoology will be used where available.")

    return config, schoology, skyward


def merge_grades(config: dict, schoology: dict, skyward: dict) -> list[dict]:
    """
    Merge per-subject grade data. Skyward priority.
    Returns list of merged subject dicts ready for template rendering.
    """
    today = date.today()
    quarter = f"Q{config['meta']['current_quarter']}"

    merged: list[dict] = []
    for subj in config["subjects"]:
        sid = subj["id"]
        sky = skyward.get(sid, {})
        sch = schoology.get(sid, {})

        # Grade: Skyward > Schoology > quarter_history fallback
        if sky.get("pct") is not None:
            pct = sky["pct"]
            source = "Skyward" if not sch.get("current_pct") else "Both"
        elif sch.get("current_pct") is not None:
            pct = sch["current_pct"]
            source = "Schoology"
        else:
            # Fall back to last known quarter history
            pct = subj["quarter_history"].get(quarter)
            source = "Cached"

        letter = get_letter(pct)

        # Quarter history: prefer config history, update current quarter with live data
        q_history = dict(subj["quarter_history"])
        if pct is not None:
            q_history[quarter] = round(pct, 1) if pct else None

        # Assignments: merge Schoology + Skyward (Skyward takes priority for duplicates)
        assignments = list(sch.get("assignments", []))
        sky_assignments = sky.get("assignments", [])
        sky_names = {a["name"].lower() for a in sky_assignments}
        for a in sky_assignments:
            # Add Skyward-only assignments at front
            assignments = [a] + [x for x in assignments if x["name"].lower() not in sky_names]

        merged.append({
            **subj,
            "pct": pct,
            "pct_display": f"{pct:.1f}%" if pct is not None else "N/A",
            "letter": letter,
            "source": source,
            "quarter": quarter,
            "q_history": q_history,
            "assignments": assignments[:12],
            "pending": [a for a in assignments if a.get("pending")],
        })

    return merged


def compute_gpa(subjects: list[dict]) -> dict:
    valid = [s["pct"] for s in subjects if s["pct"] is not None]
    core_ids = {"math", "ela", "science", "social"}
    core = [s["pct"] for s in subjects if s["id"] in core_ids and s["pct"] is not None]

    return {
        "all_avg": round(sum(valid) / len(valid), 1) if valid else None,
        "core_avg": round(sum(core) / len(core), 1) if core else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HTML GENERATORS
# ─────────────────────────────────────────────────────────────────────────────

COMMON_CSS = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
  .score-hi, .s-hi { color: #276749; }
  .score-md, .s-md { color: #d69e2e; }
  .score-lo, .s-lo { color: #e53e3e; }
  .src-tag { font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 6px; text-transform: uppercase; }
  .sky-tag { background: #e0f2fe; color: #0369a1; }
  .sch-tag { background: #f3f4f6; color: #9ca3af; }
  .type-tag { font-size: 9px; padding: 1px 5px; border-radius: 5px; font-weight: 600; }
  .tt-quiz { background: #fef3c7; color: #92400e; }
  .tt-asgn { background: #e0f2fe; color: #0c4a6e; }
  .tt-test { background: #fce7f3; color: #831843; }
"""


def _q_pill(q_label: str, val, color: str, is_current: bool) -> str:
    star = " ★" if is_current else ""
    bg = f"background:{color}18;border-color:{color}88;" if is_current else ""
    val_str = str(int(val)) if val is not None and isinstance(val, (int, float)) else "—"
    val_color = color if is_current else "#cbd5e0"
    return (f'<div class="q-pill" style="{bg}">'
            f'<span class="q-label">{q_label}{star}</span>'
            f'<span class="q-val" style="color:{val_color};">{val_str}</span></div>')


def _source_badge(source: str) -> str:
    cls = {"Skyward": "src-sky", "Schoology": "src-sch", "Both": "src-both", "Cached": "src-sch"}
    return f'<span class="card-source {cls.get(source, "src-sch")}">{source}</span>'


def _assignment_rows(assignments: list[dict]) -> str:
    if not assignments:
        return '<div style="font-size:11px;color:#a0aec0;padding:8px 0;">No assignment data yet.</div>'
    rows = []
    for a in assignments:
        src_tag = '<span class="src-tag sky-tag">Sky</span>' if a.get("source") == "skyward" else '<span class="src-tag sch-tag">Sch</span>'
        type_html = type_tag_html(a.get("type", "assignment"))
        if a.get("pending"):
            score_html = '<span class="asgn-pending">Awaiting grade</span>'
        else:
            pct = a.get("pct")
            sc = score_class(pct)
            earned = a.get("earned")
            possible = a.get("possible")
            if earned is not None and possible is not None:
                score_html = f'<span class="asgn-score {sc}">{earned:.0f}/{possible:.0f}</span>'
            elif pct is not None:
                score_html = f'<span class="asgn-score {sc}">{pct:.0f}</span>'
            else:
                score_html = '<span class="asgn-pending">—</span>'
        rows.append(f'''
        <div class="asgn-row">
          <div class="asgn-left">
            <div class="asgn-name">{a["name"]}</div>
            <div class="asgn-date">{src_tag} {type_html}</div>
          </div>
          {score_html}
        </div>''')
    return "".join(rows)


def build_dad_html(subjects: list[dict], gpa: dict, config: dict,
                   schoology_raw: dict, skyward_raw: dict, today: date) -> str:
    quarter = f"Q{config['meta']['current_quarter']}"
    q_dates = config["meta"]["quarters"].get(quarter, {})
    q_end = q_dates.get("end", "")
    updated_str = today.strftime("%B %-d, %Y")

    # Source callout: differences between Skyward and Schoology
    callout_items = []
    for s in subjects:
        if s["source"] == "Both":
            sch_pct = schoology_raw.get(s["id"], {}).get("current_pct")
            sky_pct = skyward_raw.get(s["id"], {}).get("pct")
            if sch_pct and sky_pct and abs(sch_pct - sky_pct) > 1:
                callout_items.append(
                    f'{s["label_short"]} → <strong>{sky_pct:.0f}%</strong> '
                    f'<span style="color:#64748b;">(Schoology shows {sch_pct:.0f}%)</span>')
    callout_html = ""
    if callout_items:
        callout_html = f'''<div class="sky-callout">
          🔵 <strong>Skyward vs Schoology differences:</strong> {" &nbsp;·&nbsp; ".join(callout_items)}
        </div>'''

    # Grade cards
    cards_html = ""
    for s in subjects:
        q_pills = ""
        for q in ["Q1", "Q2", "Q3", "Q4"]:
            val = s["q_history"].get(q)
            q_pills += _q_pill(q, val, s["color"], q == quarter)

        cards_html += f'''
      <div class="grade-card {s['id']}" onclick="toggle(this)">
        {_source_badge(s['source'])}
        <div class="card-header">
          <div class="subject" style="color:{s['color']}">{s['label_short'].upper()}</div>
          <div class="teacher">{s['teacher']} · Period {s['period']}</div>
          <div class="grade-row">
            <div class="grade-pct" style="color:{s['color']}">{s['pct_display']}</div>
            <div class="letter-grade" style="background:{s['color_badge_bg']};color:{s['color']};">{s['letter']}</div>
          </div>
          <div class="grade-bar-bg"><div class="grade-bar" style="width:{min(s['pct'] or 0,100):.0f}%;background:{s['color_bar']};"></div></div>
          <div class="q-history">{q_pills}</div>
          <div class="expand-hint">▾ <span class="expand-arrow"></span> Show recent assignments</div>
        </div>
        <div class="assignments-panel">
          <div class="assignments-panel-inner">
            <div class="panel-title">Recent Assignments</div>
            {_assignment_rows(s['assignments'])}
          </div>
        </div>
      </div>'''

    # Pending/awaiting panel
    awaiting_rows = ""
    for s in subjects:
        for a in s.get("pending", []):
            awaiting_rows += f'''
        <div class="recent-item">
          <div class="recent-left">
            <div class="assignment-name">{a['name']}</div>
            <div class="assignment-meta"><span style="color:{s['color']}">{s['label_short']}</span></div>
          </div>
          <span style="font-size:11px;color:#f59e0b;font-weight:600;">Pending</span>
        </div>'''
    if not awaiting_rows:
        awaiting_rows = '<div style="font-size:11px;color:#a0aec0;padding:4px 0;">All caught up!</div>'

    all_avg_str = f"{gpa['all_avg']:.1f}%" if gpa['all_avg'] else "N/A"
    core_avg_str = f"{gpa['core_avg']:.1f}%" if gpa['core_avg'] else "N/A"
    skyward_present = SKYWARD_FILE.exists()
    sky_note = "Skyward PDF loaded (priority)" if skyward_present else "No Skyward PDF — upload to .tmp/skyward.pdf"

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Julia Bally – Grade Dashboard (Dad)</title>
<style>
{COMMON_CSS}
  body {{ background: #f0f4f8; color: #2d3748; min-height: 100vh; }}
  .header {{ background: linear-gradient(135deg, #1a6eb5 0%, #0e4d8a 100%); color: white; padding: 24px 40px; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 4px 12px rgba(0,0,0,0.15); }}
  .header-left {{ display: flex; align-items: center; gap: 16px; }}
  .avatar {{ width: 50px; height: 50px; border-radius: 50%; background: rgba(255,255,255,0.25); display: flex; align-items: center; justify-content: center; font-size: 20px; font-weight: 700; }}
  .header h1 {{ font-size: 21px; font-weight: 700; }}
  .header .subtitle {{ font-size: 12px; opacity: 0.8; margin-top: 3px; }}
  .klein-badge {{ display: inline-block; background: rgba(255,255,255,0.2); border-radius: 20px; padding: 3px 10px; font-size: 11px; margin-left: 6px; }}
  .source-bar {{ background: #fff; border-bottom: 1px solid #e2e8f0; padding: 8px 40px; display: flex; gap: 16px; align-items: center; font-size: 12px; }}
  .source-pill {{ display: inline-flex; align-items: center; gap: 5px; padding: 3px 10px; border-radius: 20px; font-weight: 600; font-size: 11px; }}
  .source-sky {{ background: #e0f2fe; color: #0369a1; }}
  .source-sch {{ background: #f3f4f6; color: #6b7280; }}
  .main {{ padding: 24px 40px; display: grid; grid-template-columns: 1fr 340px; gap: 24px; max-width: 1400px; margin: 0 auto; }}
  .section-title {{ font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #718096; margin-bottom: 12px; }}
  .gpa-row {{ background: linear-gradient(135deg, #1a6eb5, #0e4d8a); border-radius: 12px; padding: 16px 24px; color: white; display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(26,110,181,0.3); }}
  .gpa-row .label {{ font-size: 11px; opacity: 0.8; margin-bottom: 3px; }}
  .gpa-row .val {{ font-size: 26px; font-weight: 800; }}
  .gpa-row .val-sm {{ font-size: 14px; font-weight: 700; }}
  .gpa-divider {{ width: 1px; background: rgba(255,255,255,0.2); height: 36px; }}
  .q3-badge {{ background: rgba(255,255,255,0.15); border-radius: 8px; padding: 6px 14px; font-size: 11px; }}
  .q-indicator {{ background: #ebf8ff; border: 1px solid #bee3f8; border-radius: 8px; padding: 7px 14px; font-size: 12px; color: #2b6cb0; margin-bottom: 12px; font-weight: 600; }}
  .sky-callout {{ background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; font-size: 12px; color: #0c4a6e; }}
  .sky-callout strong {{ color: #0369a1; }}
  .grades-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 20px; }}
  .grade-card {{ background: #fff; border-radius: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); border: 1px solid #e8edf3; overflow: hidden; transition: box-shadow 0.15s; position: relative; }}
  .grade-card:hover {{ box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
  .card-header {{ padding: 16px 18px 14px; cursor: pointer; user-select: none; }}
  .subject {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; margin-bottom: 2px; }}
  .teacher {{ font-size: 11px; color: #a0aec0; margin-bottom: 10px; }}
  .grade-row {{ display: flex; align-items: flex-end; justify-content: space-between; }}
  .grade-pct {{ font-size: 34px; font-weight: 800; line-height: 1; }}
  .letter-grade {{ font-size: 18px; font-weight: 700; width: 40px; height: 40px; border-radius: 10px; display: flex; align-items: center; justify-content: center; }}
  .grade-bar-bg {{ height: 5px; background: #e8edf3; border-radius: 3px; margin-top: 10px; }}
  .grade-bar {{ height: 5px; border-radius: 3px; }}
  .q-history {{ display: flex; gap: 6px; margin-top: 10px; }}
  .q-pill {{ flex: 1; text-align: center; padding: 5px 3px; border-radius: 6px; font-size: 10px; background: #f7fafc; border: 1px solid #e2e8f0; }}
  .q-pill .q-label {{ color: #a0aec0; font-weight: 600; display: block; margin-bottom: 2px; font-size: 9px; }}
  .q-pill .q-val {{ font-weight: 700; font-size: 11px; }}
  .expand-hint {{ font-size: 10px; color: #a0aec0; margin-top: 10px; cursor: pointer; }}
  .expand-arrow {{ display: inline-block; transition: transform 0.2s; }}
  .grade-card.open .expand-arrow {{ transform: rotate(180deg); }}
  .card-source {{ position: absolute; top: 10px; right: 10px; font-size: 9px; font-weight: 700; padding: 2px 6px; border-radius: 8px; text-transform: uppercase; letter-spacing: 0.04em; }}
  .src-sky {{ background: #e0f2fe; color: #0369a1; }}
  .src-sch {{ background: #f3f4f6; color: #9ca3af; }}
  .src-both {{ background: #f0fdf4; color: #166534; }}
  .assignments-panel {{ max-height: 0; overflow: hidden; transition: max-height 0.3s ease; background: #f8fafc; border-top: 1px solid #e8edf3; }}
  .grade-card.open .assignments-panel {{ max-height: 600px; }}
  .assignments-panel-inner {{ padding: 12px 18px; }}
  .panel-title {{ font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #718096; margin-bottom: 8px; }}
  .asgn-row {{ display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid #edf2f7; }}
  .asgn-row:last-child {{ border-bottom: none; }}
  .asgn-left .asgn-name {{ font-size: 12px; color: #2d3748; font-weight: 500; line-height: 1.3; }}
  .asgn-left .asgn-date {{ font-size: 10px; color: #a0aec0; margin-top: 2px; display: flex; gap: 5px; align-items: center; }}
  .asgn-score {{ font-size: 13px; font-weight: 700; }}
  .asgn-pending {{ font-size: 10px; color: #a0aec0; font-weight: 600; font-style: italic; }}
  .side-panel {{ display: flex; flex-direction: column; gap: 18px; }}
  .panel-card {{ background: #fff; border-radius: 12px; padding: 16px 18px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); border: 1px solid #e8edf3; }}
  .recent-item {{ display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #f0f4f8; }}
  .recent-item:last-child {{ border-bottom: none; }}
  .recent-left .assignment-name {{ font-size: 12px; font-weight: 600; color: #2d3748; line-height: 1.3; }}
  .recent-left .assignment-meta {{ font-size: 10px; color: #a0aec0; margin-top: 2px; }}
  .footer {{ text-align: center; padding: 24px; font-size: 11px; color: #a0aec0; }}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <div class="avatar">JB</div>
    <div>
      <h1>Julia Bally <span class="klein-badge">Klein ISD</span></h1>
      <div class="subtitle">Krimmel Intermediate · Grade 6 · 2025–2026</div>
    </div>
  </div>
  <div style="text-align:right;">
    <div style="font-size:16px;font-weight:600;">{updated_str}</div>
    <div style="font-size:12px;opacity:0.75;margin-top:4px;">{quarter} · ends {q_end}</div>
  </div>
</div>

<div class="source-bar">
  <span style="color:#9ca3af;font-size:11px;">Sources:</span>
  <span class="source-pill source-sky">🔵 Skyward (priority)</span>
  <span class="source-pill source-sch">⚪ Schoology (fallback)</span>
  <span style="color:#9ca3af;font-size:11px;">· Click any subject card to expand assignments</span>
  <span style="margin-left:auto;color:{'#0369a1' if skyward_present else '#f59e0b'};font-size:11px;">{'✓ ' + sky_note if skyward_present else '⚠ ' + sky_note}</span>
</div>

<div class="main">
  <div>
    <div class="gpa-row">
      <div><div class="label">All Subjects Avg</div><div class="val">{all_avg_str}</div></div>
      <div class="gpa-divider"></div>
      <div><div class="label">Core Subjects Avg</div><div class="val">{core_avg_str}</div></div>
      <div class="gpa-divider"></div>
      <div class="q3-badge"><div class="label">Quarter ends</div><div class="val-sm">{q_end}</div></div>
    </div>

    <div class="q-indicator">📅 {quarter} · Last updated {updated_str}</div>
    {callout_html}

    <div class="section-title">Course Grades — click a card to expand assignments</div>
    <div class="grades-grid">
{cards_html}
    </div>
  </div>

  <div class="side-panel">
    <div class="panel-card">
      <div class="section-title">Awaiting Grade</div>
      {awaiting_rows}
    </div>
    <div class="panel-card">
      <div class="section-title">Quarter Summary</div>
      {''.join(f"""<div class="recent-item">
        <div class="recent-left">
          <div class="assignment-name" style="color:{s['color']}">{s['label_short']}</div>
          <div class="assignment-meta">{s['teacher']}</div>
        </div>
        <div style="font-size:16px;font-weight:800;color:{s['color']}">{s['pct_display']}</div>
      </div>""" for s in subjects)}
    </div>
  </div>
</div>

<div class="footer">Last updated {updated_str} · grades-dashboard · Skyward (priority) + Schoology</div>

<script>
function toggle(card) {{
  card.classList.toggle('open');
  var arrow = card.querySelector('.expand-arrow');
  if (arrow) arrow.textContent = card.classList.contains('open') ? '▴' : '▾';
  var hint = card.querySelector('.expand-hint');
  if (hint) hint.firstChild.textContent = card.classList.contains('open') ? '▴ ' : '▾ ';
}}
</script>
</body>
</html>'''


def build_wife_html(subjects: list[dict], gpa: dict, config: dict, today: date) -> str:
    quarter = f"Q{config['meta']['current_quarter']}"
    updated_str = today.strftime("%B %-d, %Y")
    all_avg_str = f"{gpa['all_avg']:.1f}%" if gpa['all_avg'] else "N/A"

    # Alert strip — flag anything below 88 or pending many items
    alerts = []
    for s in subjects:
        if s["pct"] is not None and s["pct"] < 88:
            alerts.append(f"⚠ {s['label_short']} is at {s['pct_display']} — watch closely")
        if len(s.get("pending", [])) > 1:
            alerts.append(f"📋 {s['label_short']} has {len(s['pending'])} assignments awaiting grades")
    alert_html = ""
    if alerts:
        items = "".join(f"<li>{a}</li>" for a in alerts[:4])
        alert_html = f'<div class="alert-strip"><ul>{items}</ul></div>'

    # Subject rows
    rows_html = ""
    for s in subjects:
        pct = s["pct"] or 0
        flag = ""
        if s["pct"] is not None and s["pct"] < 88:
            flag = " 👀"
        if s["pct"] is not None and s["pct"] < 80:
            flag = " ⚠️"
        sc = s_class(s["pct"])
        rows_html += f'''
    <div class="subj-row">
      <div style="display:flex;align-items:center;gap:10px;flex:1;min-width:0;">
        <span class="dot" style="background:{s['color']};"></span>
        <div style="min-width:0;">
          <div style="font-size:13px;font-weight:600;color:#1e293b;">{s['label_short']}{flag}</div>
          <div style="font-size:11px;color:#64748b;">{s['teacher']}</div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:10px;flex-shrink:0;">
        <div style="width:70px;background:#e2e8f0;border-radius:3px;height:6px;overflow:hidden;">
          <div style="width:{min(pct,100):.0f}%;height:6px;background:{s['color']};border-radius:3px;"></div>
        </div>
        <div style="font-size:15px;font-weight:700;width:44px;text-align:right;" class="{sc}">{s['pct_display']}</div>
        <div style="font-size:13px;font-weight:700;width:22px;text-align:center;color:{s['color']};">{s['letter']}</div>
      </div>
    </div>'''

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Julia – Grades (Quick View)</title>
<style>
{COMMON_CSS}
  body {{ background: #f8fafc; color: #1e293b; }}
  .header {{ background: #1e293b; color: white; padding: 18px 24px; }}
  .header h1 {{ font-size: 18px; font-weight: 700; }}
  .header .sub {{ font-size: 12px; opacity: 0.6; margin-top: 3px; }}
  .wrap {{ max-width: 680px; margin: 0 auto; padding: 20px 16px; }}
  .alert-strip {{ background: #fef2f2; border: 1px solid #fca5a5; border-radius: 10px; padding: 12px 16px; margin-bottom: 18px; color: #7f1d1d; font-size: 13px; font-weight: 600; }}
  .alert-strip ul {{ padding-left: 18px; }}
  .alert-strip li {{ margin-bottom: 4px; }}
  .card {{ background: #fff; border-radius: 12px; border: 1px solid #e2e8f0; padding: 16px; margin-bottom: 14px; }}
  .card-title {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #64748b; margin-bottom: 12px; }}
  .subj-row {{ display: flex; align-items: center; padding: 9px 0; border-bottom: 1px solid #f0f4f8; gap: 12px; }}
  .subj-row:last-child {{ border-bottom: none; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
  .gpa-strip {{ background: #1e293b; color: white; border-radius: 10px; padding: 12px 18px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }}
  .footer {{ text-align: center; font-size: 11px; color: #94a3b8; padding: 16px; }}
</style>
</head>
<body>
<div class="header">
  <h1>Julia Bally · Grades</h1>
  <div class="sub">Krimmel Intermediate · {updated_str} · {quarter}</div>
</div>
<div class="wrap">
  {alert_html}
  <div class="gpa-strip">
    <div><div style="font-size:10px;opacity:0.6;">All Subjects</div><div style="font-size:22px;font-weight:800;">{all_avg_str}</div></div>
    <div style="text-align:right;"><div style="font-size:10px;opacity:0.6;">Core Avg</div><div style="font-size:18px;font-weight:700;">{f"{gpa['core_avg']:.1f}%" if gpa['core_avg'] else "N/A"}</div></div>
  </div>
  <div class="card">
    <div class="card-title">Grades · {quarter}</div>
    {rows_html}
  </div>
</div>
<div class="footer">Updated {updated_str} · Skyward priority</div>
</body>
</html>'''


def build_julia_html(subjects: list[dict], gpa: dict, config: dict, today: date) -> str:
    quarter = f"Q{config['meta']['current_quarter']}"
    q_dates = config["meta"]["quarters"].get(quarter, {})
    q_end_str = q_dates.get("end", "")
    updated_str = today.strftime("%A, %B %-d")

    # Grade tiles
    tiles_html = ""
    for s in subjects:
        pct = s["pct"] or 0
        g1, g2 = s["gradient"]
        tiles_html += f'''
    <div style="background:linear-gradient(135deg,{g1},{g2});border-radius:14px;padding:14px;box-shadow:0 2px 8px rgba(0,0,0,0.08);color:white;">
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;opacity:0.85;">{s['emoji']} {s['label_short']}</div>
      <div style="font-size:30px;font-weight:800;line-height:1.1;margin:6px 0 2px;">{s['pct_display']}</div>
      <div style="font-size:11px;opacity:0.8;">{s['letter']} · {s['teacher'].split()[0]}</div>
      <div style="background:rgba(255,255,255,0.25);border-radius:3px;height:4px;margin-top:8px;">
        <div style="width:{min(pct,100):.0f}%;background:white;height:4px;border-radius:3px;"></div>
      </div>
    </div>'''

    # Wins / encouragement
    wins = [s for s in subjects if s["pct"] is not None and s["pct"] >= 90]
    win_items = "".join(f'<div style="padding:4px 0;font-size:13px;">⭐ <strong>{s["label_short"]}</strong> — {s["pct_display"]} · {s["letter"]}</div>' for s in wins)
    if not win_items:
        win_items = '<div style="font-size:13px;">Keep it up — every assignment counts! 💪</div>'

    # Something to work on
    watch = [s for s in subjects if s["pct"] is not None and s["pct"] < 90]
    work_items = ""
    for s in watch:
        work_items += f'<div style="padding:4px 0;font-size:13px;">📌 <strong>{s["label_short"]}</strong> is at {s["pct_display"]} — a couple more good grades will bring it up!</div>'
    if not work_items:
        work_items = '<div style="font-size:13px;">Everything looks great — stay consistent! 🌟</div>'

    all_avg_str = f"{gpa['all_avg']:.1f}%" if gpa['all_avg'] else "N/A"

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Julia's Daily Grade Brief</title>
<style>
{COMMON_CSS}
  body {{ background: #f5f3ff; color: #1e1b4b; min-height: 100vh; }}
  .header {{ background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #a855f7 100%); color: white; padding: 28px 24px; text-align: center; position: relative; overflow: hidden; }}
  .header-watermark {{ position: absolute; top: -10px; right: -10px; font-size: 120px; opacity: 0.08; }}
  .header h1 {{ font-size: 28px; font-weight: 900; margin-bottom: 4px; }}
  .header .sub {{ font-size: 13px; opacity: 0.85; }}
  .header .q-tag {{ display:inline-block;background:rgba(255,255,255,0.2);border-radius:20px;padding:3px 12px;font-size:11px;margin-top:8px; }}
  .wrap {{ max-width: 480px; margin: 0 auto; padding: 20px 16px 32px; }}
  .card {{ border-radius: 16px; padding: 18px; margin-bottom: 16px; }}
  .card-title {{ font-size: 13px; font-weight: 800; margin-bottom: 10px; }}
  .tiles {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }}
  .footer {{ text-align: center; padding: 16px; font-size: 13px; color: #7c3aed; font-weight: 700; }}
</style>
</head>
<body>
<div class="header">
  <div class="header-watermark">⭐</div>
  <div style="font-size:16px;margin-bottom:6px;">Good morning! 🌟</div>
  <h1>Julia's Grade Brief</h1>
  <div class="sub">{updated_str}</div>
  <div class="q-tag">{quarter} · ends {q_end_str}</div>
</div>

<div class="wrap">

  <div style="background:linear-gradient(135deg,#1e293b,#334155);border-radius:14px;padding:16px;margin-bottom:16px;color:white;text-align:center;">
    <div style="font-size:11px;opacity:0.6;margin-bottom:4px;">Overall Average</div>
    <div style="font-size:36px;font-weight:900;">{all_avg_str}</div>
    <div style="font-size:12px;opacity:0.7;margin-top:4px;">You're doing amazing! 🎉</div>
  </div>

  <div class="card" style="background:linear-gradient(135deg,#ecfdf5,#d1fae5);border:1px solid #6ee7b7;">
    <div class="card-title">🌟 Great Grades Right Now</div>
    {win_items}
  </div>

  <div class="card" style="background:#fff7ed;border:1px solid #fed7aa;">
    <div class="card-title">📚 Something to Work On</div>
    {work_items}
  </div>

  <div class="card-title" style="margin-bottom:12px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#7c3aed;">My Grades — {quarter}</div>
  <div class="tiles">
    {tiles_html}
  </div>

</div>

<div class="footer">Go Julia! 🌟</div>
</body>
</html>'''


def main() -> None:
    args = parse_args()
    config, schoology, skyward = load_data()

    if args.dry_run:
        logger.info("[DRY RUN] Would generate 3 HTML files with %d subjects.", len(config["subjects"]))
        return

    today = date.today()
    subjects = merge_grades(config, schoology, skyward)
    gpa = compute_gpa(subjects)

    logger.info("Merged %d subjects. Avg: %s", len(subjects),
                f"{gpa['all_avg']:.1f}%" if gpa['all_avg'] else "N/A")

    dad_html = build_dad_html(subjects, gpa, config, schoology, skyward, today)
    wife_html = build_wife_html(subjects, gpa, config, today)
    julia_html = build_julia_html(subjects, gpa, config, today)

    (PROJECT_ROOT / "index.html").write_text(dad_html)
    (PROJECT_ROOT / "wife.html").write_text(wife_html)
    (PROJECT_ROOT / "julia.html").write_text(julia_html)

    logger.info("Wrote index.html (Dad), wife.html, julia.html")


if __name__ == "__main__":
    main()

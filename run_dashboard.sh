#!/bin/bash
# Grades dashboard runner — called by launchd daily (8 PM)
# Fetches Schoology data, optionally parses Skyward PDF, generates HTML, pushes to GitHub

set -euo pipefail

PROJ="/Users/danielbally/Git/grades-dashboard"
LOG="$PROJ/.tmp/launchd.log"

mkdir -p "$PROJ/.tmp"
echo "--- $(date) ---" >> "$LOG"

cd "$PROJ"
source "$PROJ/.venv/bin/activate"

echo "Fetching Schoology grades..." >> "$LOG"
python3 execution/fetch_schoology.py >> "$LOG" 2>&1

if [ -f "$PROJ/.tmp/skyward.pdf" ]; then
  echo "Parsing Skyward PDF..." >> "$LOG"
  python3 execution/parse_skyward.py >> "$LOG" 2>&1
else
  echo "No Skyward PDF found — skipping (using Schoology only)." >> "$LOG"
fi

echo "Generating dashboard HTML..." >> "$LOG"
python3 execution/generate_dashboard.py >> "$LOG" 2>&1

echo "Pushing to GitHub..." >> "$LOG"
git add index.html wife.html julia.html
git commit -m "chore: update grades dashboard $(date +%Y-%m-%d)" || echo "No changes to commit." >> "$LOG"
git pull --rebase origin main >> "$LOG" 2>&1
git push origin main >> "$LOG" 2>&1

echo "Done." >> "$LOG"

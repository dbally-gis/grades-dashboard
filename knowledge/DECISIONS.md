# Architecture Decisions — grades-dashboard

## 2026-03-23 — Dual-source data strategy (Skyward priority over Schoology)

**Decision:** Use Skyward as the authoritative grade source; Schoology as fallback and for assignment detail.

**Why:** Skyward is the official Klein ISD gradebook. Schoology reflects teacher-entered grades but can lag or show rounding differences. Skyward quarter grades are the ones that appear on report cards.

**How applied:** `merge_grades()` in `generate_dashboard.py` checks `skyward.pct` first; falls back to `schoology.current_pct`; finally falls back to `quarter_history` from config. Source is flagged per subject as "Skyward", "Schoology", "Both", or "Cached".

---

## 2026-03-23 — BeautifulSoup HTML parsing (not inner_text or DOM selectors)

**Decision:** Parse Schoology gradebook HTML with BeautifulSoup, targeting `div#s-js-gradebook-course-*` containers.

**Why:** Schoology's grades page is 1.85MB of server-rendered HTML. Playwright `inner_text()` on the calendar page returned unstructured flat text due to absolute-positioned FullCalendar elements. CSS selectors alone were fragile. BS4 gives reliable traversal of the nested TR/TH/TD structure.

---

## 2026-03-23 — Calendar data via AJAX endpoint, not page scrape

**Decision:** Fetch upcoming assignments from `https://schoology.kleinisd.net/calendar/84470972/{month}?ajax=1&start=UNIX&end=UNIX` rather than scraping the rendered calendar page.

**Why:** FullCalendar renders events as absolute-positioned divs — `inner_text` gives unstructured garbage. The AJAX endpoint returns clean JSON with ISO dates, assignment types, and course names.

**Constraint:** The API is month-scoped. Must fetch current + next month(s) and deduplicate by event `id`.

---

## 2026-03-23 — Skyward popup window login pattern

**Decision:** Capture Skyward's home page as a Playwright popup (via `context.on("page", ...)`) rather than navigating in the same window.

**Why:** After `#bLogin` click, Skyward's JS calls `openNewWindow(vSplit[7])` — the desktop path opens a new popup window, not a same-window redirect. Attempting to POST session tokens directly via `page.request.post()` returned "session expired" because server-side sessions require the popup's cookie context.

---

## 2026-03-23 — Three dashboard editions (Dad / Wife / Julia)

**Decision:** Generate three separate HTML files from the same merged data rather than a single view with tabs or a user selector.

**Why:** Each audience has a different information need and screen context. Dad wants full detail with expandable assignment rows. Mom wants a 30-second scan with alerts. Julia wants colorful motivation with encouragement. Separate static files are simpler to deploy (Vercel, GitHub Pages) and faster to load.

---

## 2026-03-23 — launchd plist at 20:00 daily

**Decision:** Schedule the pipeline via `com.danielbally.grades-dashboard.plist` at 8 PM rather than cron.

**Why:** Consistent with daily-briefing and tx-lottery-dashboard patterns in this workspace. launchd survives sleep/wake cycles; cron may miss if the machine is asleep.

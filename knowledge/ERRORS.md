# Error Log — grades-dashboard

## 2026-03-23 — ClassLink SSO timeout (#username not found within 30s)

**Error:** `TimeoutError: Locator.fill: Timeout 30000ms exceeded waiting for #username`

**Cause:** Rate limiting from repeated logins during development session. ClassLink throttles rapid successive logins from the same IP.

**Fix:** Wait and retry. Subsequent runs succeeded. No code change needed.

**Pattern:** Infrastructure/rate-limit — log only.

---

## 2026-03-23 — Skyward home page opens as popup, not same-window navigation

**Error:** After clicking `#bLogin`, page URL did not change. Wait-for-URL timed out. `page.content()` still showed the login form.

**Cause:** Skyward's `customExtraInfo("tryLogin", ...)` processes the AJAX login response and calls `openNewWindow(vSplit[7])` (the desktop path). The home session opens in a **popup window**, not the existing page.

**Fix:** Register `context.on("page", lambda p: popup_pages.append(p))` before the login click. Access `popup_pages[0]` as the home page after login.

**Graduated to:** `knowledge/DECISIONS.md` — Skyward popup window login pattern.

---

## 2026-03-23 — Skyward `page.request.post()` returns "session expired"

**Error:** Direct POST to `sfhome01.w` / `skyporthttp.w` with session token fields returned a "session expired" error page.

**Cause:** Skyward binds server-side sessions to the browser cookie context. Replaying tokens via a raw HTTP request from Playwright's request context does not carry the session cookie.

**Fix:** Use the popup page object directly for all subsequent navigation. Do not attempt to reconstruct the session via raw HTTP.

---

## 2026-03-23 — Calendar inner_text returned unstructured flat text

**Error:** `page.inner_text("body")` on the calendar page returned a flat string with no date/event structure — FullCalendar positions events via absolute CSS, not DOM order.

**Fix:** Discovered AJAX endpoint `?ajax=1&start=UNIX&end=UNIX`. Returns clean JSON array per month.

**Graduated to:** `knowledge/DECISIONS.md` — Calendar data via AJAX endpoint.

---

## 2026-03-23 — Calendar AJAX only returned 4 events (all today)

**Error:** First AJAX fetch returned only 4 events, all dated today. Expected several weeks of assignments.

**Cause:** The AJAX endpoint is month-scoped — March endpoint only returned March events, and most March events were already past. No April events were fetched.

**Fix:** Fetch both current and next month's endpoints, deduplicate by event `id` field.

---

## 2026-03-23 — `from pathlib import Path, re` syntax error

**Error:** `ImportError: cannot import name 're' from 'pathlib'`

**Cause:** Typo in ad-hoc test script — `re` is a stdlib module, not a pathlib export.

**Fix:** Use separate `import re` statement. Trivial — not graduated.

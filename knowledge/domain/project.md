# Project: grades-dashboard

**Student:** Julia Bally
**School:** Krimmel Intermediate, Klein ISD, Texas
**Grade:** 6th — 2025-2026 school year
**Current Quarter:** Q4 (started March 23, 2026)

## Subjects & Teachers

| Subject | Teacher | Period | Color |
|---|---|---|---|
| Math 6 Advanced | Melody Boyd | 1 | Blue #2b6cb0 |
| ELA Reading 6 KP | Libby Klempnauer | 2 | Purple #6b46c1 |
| Science 6 KP | Jillian Mundy | 7 | Green #276749 |
| SOC STUD 6 KP | Donnie Lancelin | 4 | Orange #c05621 |
| PE Girls 6 | Jessica Fanucchi | 5 | Teal #0f766e |
| Theatre Arts Beg 6 | Katherine Mehrens | 6 | Pink #9d174d |

## Data Sources

- **Schoology:** `https://schoology.kleinisd.net` — LMS, grades may lag 1-5 days
- **Skyward:** `https://skyward.kleinisd.net` — official gradebook, authoritative
- **Schoology auth:** Playwright persistent context saved to `.tmp/browser_state/`
- **Skyward auth:** PDF export only (server sessions not shareable)

## Known Quirks

- PE and Theatre Arts never post to Schoology — Skyward PDF is only source
- Math Schoology/Skyward often diverge (Skyward lower = more assignments counted)
- Social Studies dropped to 89% in Q3 due to India CCA (70) in Skyward only
- Schoology sometimes redirects to home — check page title before parsing
- Quarter history stored in `data/grades_2026.json` as fallback

## Q3 Final Grades (locked Mar 13 2026)

| Subject | Q3 Grade | Source |
|---|---|---|
| Math | 95% | Skyward |
| ELA | 86% | Both |
| Science | 91% | Both |
| Social Studies | 89% | Skyward |
| PE | 100% | Skyward |
| Theatre Arts | 100% | Skyward |

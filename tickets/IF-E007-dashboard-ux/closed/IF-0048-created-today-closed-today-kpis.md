---
id: IF-0048
title: Created today / closed today KPIs
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 1h
created: 2026-07-13
closed: 2026-07-13
updated: 2026-07-13
index_note: two tiles head the KPI row; /filter created=today closed=today shorthands
---

# IF-0048 · Created today / closed today KPIs

## Context

The board showed totals and velocity, but not the day's momentum — the two
numbers you actually want at a glance: what got filed today, what shipped
today, and a click through to those lists.

## Approach

Two tiles at the head of the KPI row, using the existing tile component:
counts by exact `created:`/`closed:` date with a 7-day sub-stat, clicking
through to `/filter?created=today` / `?closed=today` — new server-side
shorthands (static tile hrefs can't compute dates; the server can).

## Acceptance criteria

- [x] Tiles render first in the KPI row with live counts and 7-day subs.
- [x] Click-through lists show exactly the tickets created/closed today.
- [x] Shorthands compose with the existing filter params.

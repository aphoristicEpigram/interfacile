---
id: IF-0048
title: Created today / closed today, in a header pocket
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 1h
created: 2026-07-13
closed: 2026-07-13
updated: 2026-07-13
index_note: compact today pocket in the header (eyebrow <-> buttons); /filter created=today closed=today shorthands
---

# IF-0048 · Created today / closed today, in a header pocket

## Context

The board showed totals and velocity, but not the day's momentum — the two
numbers you actually want at a glance: what got filed today, what shipped
today, and a click through to those lists.

## Approach

A compact "today" pocket at the very top of the page, sitting in the header
row between the eyebrow and the header buttons (first cut put these in the
KPI row; moved on feedback — the ask was TOP top, and small). Clicking the
label cycles the window subtly: today → 7 days → 30 days (rolling, remembered
in localStorage), counts and click-throughs following. The lists come from
`/filter?created=today|week|month` / `?closed=…` — new server-side shorthands
(static hrefs can't compute dates; the server can).

## Acceptance criteria

- [x] Pocket renders in the header row, small, with live counts; hidden
      until data loads.
- [x] Label click cycles today / 7 days / 30 days; choice sticks across
      reloads.
- [x] Click-through lists match each window exactly (verified: today's
      closes vs the week's over HTTP).
- [x] Shorthands compose with the existing filter params.

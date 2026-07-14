---
id: IF-0064
title: Sortable TAGS column on the filtered grid, with a code-review checkbox
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0064 · Sortable TAGS column on the filtered grid, with a code-review checkbox

## Context

The filtered grid's trailing cell held loose flag chips (blocked, WIP, pin) with
no heading and no way to sort on them, and there was no way to record that a
ticket's code has been reviewed — a checklist the review process needs.

## Approach

Name the trailing column TAGS and make its header a sort control like the rest:
a server-computed `data-tagrank` (blocked 8 · decision 4 · wip 2 · pinned 1,
summed) gives the shared list script a single number to order by, heaviest
first. Surface DECISION/CONCEPT titles as a chip (same test the dashboard runs).
The review tick mirrors the pin end to end: `review.json` beside `pins.json`
(id → timestamp), a `reviewed` flag from scan(), a `/api/review` POST, and an
in-place checkbox chip in the TAGS cell.

## Acceptance criteria

- [x] The grid's last column is headed TAGS and sorts by tag weight, blocked
      and decision tickets first.
- [x] DECISION/CONCEPT tickets show a DEC chip on the grid.
- [x] Every row carries a review tick; toggling it persists to
      `.interfacile/review.json` and survives a reload.
- [x] `/api/review` rejects unknown ticket ids.

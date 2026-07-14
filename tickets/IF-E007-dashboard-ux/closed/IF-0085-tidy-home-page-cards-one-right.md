---
id: IF-0085
title: Tidy home page cards: one right edge for chips, tick-sized dep icon, per-panel sort
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0085 · Tidy home page cards: one right edge for chips, tick-sized dep icon, per-panel sort

## Context

Dashboard card rows reserved a 46px right column for the absolutely-positioned
review tick, so every other chip (tags, dates, dep icon) stopped at an
invisible vertical line left of the tick — dead space on every row, and the
tag cluster looked misaligned. The dependency chip rendered at .6rem, visibly
smaller than the tick beside it. The home page panels (Pinned, WIP, Up next,
Recently closed/created) had no sort control, unlike the backlog.

## Approach

- Let the review tick flow in the row (it was already the last element in the
  markup) instead of pinning it absolute; drop the 46px `padding-right`
  reserve so all chips share one right edge. Keep the tick visible on ≤640px
  where `.b-right` hides.
- Size the dep chip like the tick (`.78rem`).
- A touch more row padding in `.b-list` / `.recent` for breathing room.
- View-only sort select in `_LIST_TOOLS`, opted into per panel via
  `data-sort`: priority / risk / effort / id, keyless rows sink, "sort"
  restores served order; a MutationObserver re-applies the sort after
  re-renders.

## Acceptance criteria

- [x] Tag chips, dates, dep icon and review tick end on the same right edge
      in backlog cards and all home page panels.
- [x] Dep chip is visually the same size as the review tick.
- [x] Each home page panel (Pinned, WIP, Up next, Recently closed, Recently
      created) offers a sort control; sorting is display-only and survives a
      panel re-render.
- [x] No dead reserved column on rows; tests pass and all inline scripts
      parse (`node --check`).

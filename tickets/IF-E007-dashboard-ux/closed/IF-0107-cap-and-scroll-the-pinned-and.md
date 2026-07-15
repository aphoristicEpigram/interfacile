---
id: IF-0107
title: Cap and scroll the pinned and WIP panels so they can't balloon
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 3h
created: 2026-07-15
closed: 2026-07-15
updated: 2026-07-15
---

# IF-0107 · Cap and scroll the pinned and WIP panels so they can't balloon

## Context

The pinned panel renders every pin, and once IF-0106 makes pinning a one-click
act you will pin more freely. A pinned list with thirty items pushes the whole
board down the page and turns the two-column top strip into a wall of rows — the
panels stop being a glance and start being the page.

The WIP panel already noticed this and hard-caps at 8 with a "+N more" link. That
keeps the height down but hides the rest behind a navigation away from the board,
which is a different behaviour from the pinned panel beside it. Two panels that
sit side by side and do the same job should behave the same way.

## Approach

Both panels grow with their contents up to about a dozen tickets, then stop
growing and scroll their own contents. A capped, internally-scrollable list keeps
the whole list reachable — no "+N more" trip away from the board — while the panel
itself never gets tall enough to shove the rest of the page around.

Concretely: a max-height on the pinned and WIP list bodies (not the panels, so
their headers and tools stay put), with `overflow-y: auto` below it, sized to
land near twelve rows. WIP drops its 8-item slice and "+N more" link and renders
its full list into the same capped, scrolling body, so the two panels match. The
"solo" single-column layout when there is no WIP is unchanged.

## Acceptance criteria

- [x] With a handful of pins the pinned panel is its natural height — no scrollbar, no empty capped box.
- [x] Past roughly a dozen pins the pinned list stops growing and scrolls within itself; the rest of the page does not move.
- [x] The WIP panel behaves identically: it grows to the same cap, then scrolls, and no longer shows a "+N more" link.
- [x] Only the list body scrolls — each panel's header and its copy/pin/CSV tools stay visible above the scroll area.
- [x] Every pinned or WIP ticket remains reachable by scrolling; nothing is hidden behind a cap.
- [x] Drag-to-reorder of pinned rows still works, including dragging to a row currently below the fold.
- [x] The single-column "solo" layout when there is no WIP is unchanged.

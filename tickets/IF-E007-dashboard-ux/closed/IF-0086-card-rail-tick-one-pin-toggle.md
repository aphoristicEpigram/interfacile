---
id: IF-0086
title: Card rail (tick + one pin toggle), designed filter dropdowns, multi-select tag pills
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 4h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0086 · Card rail (tick + one pin toggle), designed filter dropdowns, multi-select tag pills

## Context

Round two of the home-page card tidy-up (follows IF-0085). The review tick sat
at the end of the chip line with dead space above it; pinned rows carried two
separate unpin controls (the 🔖 chip and a "✕ unpin" button); the dependency
icon rendered as a squished rectangle; the Open/Closed flag was vertically
centred against tall titles, leaving an arbitrary-looking gap; and the /filter
page used bare browser selects with a single-choice tags dropdown that ignored
the state the URL arrived with (?pinned=1 showed tags: all).

## Approach

- Card rows restructured: a body (title line, then a right-aligned chip line
  with 7px of air) plus a rail on the right edge — review tick top, ONE pin
  toggle (pins or unpins) directly beneath. 🔖 meta chip and ✕ unpin removed.
- Dep icon and rail chips share a 22px square footprint.
- Epic chip dropped from home-page card rows (kept in flat search results).
- Status flag baseline-aligns with the title's first line (top-left).
- /filter + /epic selects get a designed face: appearance:none, custom SVG
  caret, hover/focus accents; same treatment for the dashboard backlog selects.
- Tags become multi-select toggle pills with icons; every pressed tag must
  hold; contradictions auto-clear (untagged vs bit tags, reviewed vs not).
  The bar prefills from the URL (?pinned=1, ?wip=1, ?quick=1, ?blocked,
  ?risk, ?priority, single ?status) so canned links arrive honest.

## Acceptance criteria

- [x] Every card row shows the tick top-right with a single pin/unpin toggle
      directly beneath it; no ✕ unpin button, no 🔖 meta chip.
- [x] Dep icon is square, same footprint as the tick.
- [x] Open/Closed flag sits top-left, aligned with the title's first line.
- [x] Visible padding between title text and the chip line.
- [x] /filter dropdowns carry the designed face with a rendered caret.
- [x] Tags on /filter are multi-select pills; /filter?pinned=1 arrives with
      the pinned pill already pressed.
- [x] Tests pass; all inline scripts on /, /filter, /epic parse clean.

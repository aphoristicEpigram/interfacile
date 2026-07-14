---
id: IF-0068
title: Louder review tick, toggleable from the ticket page
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0068 · Louder review tick, toggleable from the ticket page

## Context

The review tick shipped quiet (dim, 45% opacity) and only on the grid -
invisible enough to miss, and not toggleable while reading the ticket itself.

## Approach

Grid chip gets a real checkbox face: bordered, bigger, 75% opacity, green fill
when on. The ticket page toolbar gains a 'Reviewed' button sharing the same
.revchip[data-review] contract; the one click handler moves to the _LIST_TOOLS
script, which both page kinds already load - one binding, no double-POST.

## Acceptance criteria

- [x] The grid tick is plainly visible unchecked and green-filled when checked.
- [x] A Reviewed toolbar button on every ticket page toggles the same state.
- [x] Exactly one handler serves both (no double toggles).

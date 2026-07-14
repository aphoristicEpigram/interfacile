---
id: IF-0074
title: Separator line between backlog list items
epic: IF-E007
status: CLOSED
risk: LOW
priority: 4
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0074 · Separator line between backlog list items

## Context

Backlog items lost their separating line somewhere along the way and the
cards read as a run-on block.

## Approach

.b-list li gets a 1px var(--line-2) bottom border, last child exempt -
theme-driven, no new tokens.

## Acceptance criteria

- [x] Backlog (and epic-card) list items are separated by a hairline again.

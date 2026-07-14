---
id: IF-0088
title: Dependency chip leads the card's bottom line from the far left
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0088 · Dependency chip leads the card's bottom line from the far left

## Context

The dependency-trace chip sat inside the right-aligned tag cluster, reading as
just another tag. It is a navigation control, not a property of the ticket —
it deserves its own side of the card.

## Approach

Pull the chip out of metaBadges into its own depChip() and render it first on
the chip line; `.b-right .b-meta.dep{margin-right:auto}` anchors it to the
card's far left while the tags stay right-aligned by the pin.

## Acceptance criteria

- [x] On every home-page card, the dependency chip sits on the far left of
      the bottom line, apart from the tags; rows without dependencies show
      nothing there.
- [x] Chip still routes to /deps focused on the ticket; tags and date stay
      right-aligned; tests pass.

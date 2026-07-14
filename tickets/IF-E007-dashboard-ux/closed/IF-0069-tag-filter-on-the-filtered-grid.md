---
id: IF-0069
title: Tag filter on the filtered grid bar
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0069 · Tag filter on the filtered grid bar

## Context

Tags became sortable but not filterable - no way to see only blocked,
decision, wip, pinned, reviewed or unreviewed tickets in the grid.

## Approach

A tags select in the shared filter bar (grid pages only), decoding the same
data-tagrank bitmask the TAGS column sorts by, plus data-reviewed for the two
review states and 'untagged' for rank 0.

## Acceptance criteria

- [x] The /filter bar filters by blocked/decision/wip/pinned/untagged and reviewed/not-reviewed, composing with the other selects and clear.

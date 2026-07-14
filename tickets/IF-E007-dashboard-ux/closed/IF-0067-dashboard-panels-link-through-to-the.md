---
id: IF-0067
title: Dashboard panels link through to the sortable filter grid
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0067 · Dashboard panels link through to the sortable filter grid

## Context

The Pinned, WIP and Up-next panels showed their tickets but their headings
went nowhere - the sortable filtered grid the pocket links to had no
equivalent entry point for these lists.

## Approach

Each panel heading becomes a .ph-link into /filter with the matching query
(pinned=1 / wip=1 / status=open&blocked=n), same face as before, an arrow on
hover saying it goes somewhere.

## Acceptance criteria

- [x] Pinned, WIP and Up-next panel headings open the filtered grid scoped to that list, sortable like any grid.

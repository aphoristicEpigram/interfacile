---
id: IF-0081
title: Review tick gets its own top-right slot; fixed vs interactive tags
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0081 · Review tick gets its own top-right slot; fixed vs interactive tags

## Context

Every tag rode in one squished cluster per row, the interactive ones
indistinguishable from the informational ones, and the review tick was lost
among them.

## Approach

The review tick renders as its own element anchored top-right of each row
(backlog, panels, recents), with row padding reserving the corner. Informational
tags lie flat on a quiet fill; interactive ones (epic link, pin, dep trace,
review) carry class act - a real border, a pointer and an accent hover. Chip
gap widened 5px to 7px.

## Acceptance criteria

- [x] The review tick sits alone at each row's top-right corner.
- [x] Pressable tags visibly differ from informational ones.
- [x] Tags have more air between them.

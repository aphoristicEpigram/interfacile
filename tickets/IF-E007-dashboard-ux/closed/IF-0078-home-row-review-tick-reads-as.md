---
id: IF-0078
title: Home-row review tick reads as a checkbox
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0078 · Home-row review tick reads as a checkbox

## Context

The review tick rendered on home rows but at .6rem chip size and 75%
opacity it read as decoration, not a control - invisible in practice.

## Approach

The b-meta.revchip gets the checkbox face the grid got: larger type, wider
padding, 1.5px border, green fill when on.

## Acceptance criteria

- [x] The tick is plainly visible on panel, backlog and recents rows, unchecked or checked.

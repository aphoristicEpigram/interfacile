---
id: IF-0091
title: Header splits at GitHub; utilities right-align (single interface)
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0091 · Header splits at GitHub; utilities right-align (single interface)

## Context

On a single-interface serve the header buttons sat in one undifferentiated
cluster. The today-pocket and GitHub button are status; the links, notes and
Regenerate are controls — they read better anchored to opposite sides.

## Approach

`.head-btns` flexes to fill the row and packs right; the GitHub button takes
`margin-right:auto`, splitting the cluster after it. In hub mode the bar
relocates the left group and whatever remains still packs right.

## Acceptance criteria

- [x] Single interface: pocket + GitHub sit left; links, notes and Regenerate
      right-align on the same row.
- [x] Hub dashboards unaffected (Regenerate stays top-right).

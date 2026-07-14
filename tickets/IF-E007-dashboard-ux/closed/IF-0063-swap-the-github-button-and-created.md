---
id: IF-0063
title: Swap the GitHub button and created/closed pocket across the site
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0063 · Swap the GitHub button and created/closed pocket across the site

## Context

In the sticky hub bar, the GitHub button sits dead-centre and the created/closed
pocket rides on the right with the ticket controls. The pocket is the number you
actually glance at; GitHub is a jump-off link. They should trade places, and the
order should hold on every page (dashboard and sub-pages alike).

## Approach

Swap the two slots in the three places that position the pair: the
`_persist_controls_html` (centre, right) tuple for sub-pages, the dashboard
`relocate()` JS that moves `ghBtn`/`todayPocket` into the bar, and the stale CSS
comment describing the centre slot. The dashboard's own header row already reads
pocket-then-GitHub, so it needs no change.

## Acceptance criteria

- [x] On sub-pages the bar renders the pocket centred and GitHub leading the
      right-hand controls (before project links and notes).
- [x] On the multi-interface dashboard the relocated controls land the same way.
- [x] Relative order pocket-before-GitHub now matches the single-interface
      header row.

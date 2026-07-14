---
id: IF-0073
title: Review tick on every dashboard ticket row
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0073 · Review tick on every dashboard ticket row

## Context

The green review tick lived only on the grid and ticket page; the dashboard
panels and backlog rows - where tickets actually get scanned - had no way to
tick one off.

## Approach

metaBadges() renders the .revchip on every ticket row it decorates (panels,
backlog, up next, recents); the shared _LIST_TOOLS handler already listens
document-wide, so no new wiring.

## Acceptance criteria

- [x] Every dashboard ticket row shows the tick, dim until checked, green when checked, toggleable in place.

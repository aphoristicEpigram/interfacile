---
id: IF-0083
title: Review toggle flips in place - no full-page reload
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0083 · Review toggle flips in place - no full-page reload

## Context

Ticking the review box reloaded the whole page - a visible jank for a
one-bit change.

## Approach

The shared handler now POSTs and flips every chip for that ticket in place
(class, aria-checked, the row's data-reviewed), then emits an ifc-review event
the dashboard uses to patch its in-memory board, so tag filters and re-renders
agree without a reload.

## Acceptance criteria

- [x] Toggling the tick updates instantly with no page reload on dashboard, grid and ticket pages.
- [x] Tag filters and later re-renders reflect the new state.

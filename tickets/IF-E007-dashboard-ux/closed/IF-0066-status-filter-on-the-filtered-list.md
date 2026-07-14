---
id: IF-0066
title: Status filter on the filtered list page
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0066 · Status filter on the filtered list page

## Context

The filtered grid can mix open, closed and won't-fix tickets, but the filter
bar had no status control — narrowing by status meant editing the URL.

## Approach

A status select in the shared filter bar, rendered only where one list mixes
statuses (the filtered grid — epic pages are one column per status). The shared
script filters on the `data-status` every row already carries, null-safe so
pages without the select are untouched.

## Acceptance criteria

- [x] The /filter page's bar has a status select (all / open / closed /
      won't fix) that narrows rows client-side and resets with clear.
- [x] Epic pages render no status select and their filter bar keeps working.

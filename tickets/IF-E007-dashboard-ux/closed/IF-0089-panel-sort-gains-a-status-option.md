---
id: IF-0089
title: Panel sort gains a status option (open before closed)
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0089 · Panel sort gains a status option (open before closed)

## Context

The pinned panel mixes open and closed tickets, and its view-only sort
(IF-0085) had no way to group them by status.

## Approach

Add a `status` key to the shared panel sort: open → closed → won't-fix,
case-insensitive on data-status, statusless rows (doc pins, "+N more") sink
last. Available on all five home-page panels.

## Acceptance criteria

- [x] The panel sort dropdown offers status; picking it lists open tickets
      before closed before won't-fix, ties keeping served order.
- [x] Doc pins and filler rows sink to the bottom under status sort.

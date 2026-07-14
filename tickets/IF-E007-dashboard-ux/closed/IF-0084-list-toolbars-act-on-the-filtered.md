---
id: IF-0084
title: List toolbars act on the filtered rows, not the page load
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0084 · List toolbars act on the filtered rows, not the page load

## Context

On a filtered list (e.g. /filter?pinned=1 with a search applied), 'copy
ids' copied the full page-load set instead of what the filter showed.

## Approach

rowsOf() - the collector behind copy ids, CSV and pin all - now skips rows
hidden by the client-side filter, so every toolbar action operates on exactly
the rows the reader can see.

## Acceptance criteria

- [x] With a filter applied, copy ids / CSV / pin all act on the visible rows only.

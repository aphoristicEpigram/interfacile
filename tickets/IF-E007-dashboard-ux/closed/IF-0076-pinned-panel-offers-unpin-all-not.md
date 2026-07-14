---
id: IF-0076
title: Pinned panel offers unpin all, not pin all
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0076 · Pinned panel offers unpin all, not pin all

## Context

The pinned panel's toolbar said 'pin all' - a no-op on a list whose every
row is already pinned. The useful bulk action there is the opposite one.

## Approach

The pinned panel's toolbar button becomes lt-unpin ('unpin all'); the shared
handler collects data-pin-key rows (which include doc pins that the
ticket-links-only rowsOf would miss) and POSTs pinned:false for each. Every
other list keeps pin all.

## Acceptance criteria

- [x] The pinned panel offers unpin all and it clears tickets and doc pins alike.
- [x] All other list toolbars still offer pin all.

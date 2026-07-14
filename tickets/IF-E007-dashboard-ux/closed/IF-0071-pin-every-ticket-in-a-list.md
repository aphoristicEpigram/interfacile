---
id: IF-0071
title: Pin every ticket in a list from its toolbar
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0071 · Pin every ticket in a list from its toolbar

## Context

Pinning a parent's sub-tickets (or any list) meant opening each ticket -
there was no list-level pin action anywhere.

## Approach

A 'pin all' button in the shared list-tools toolbar (grid, dashboard panels,
sub-tickets table): POST /api/pin per row id, then reload showing the chips
agreeing with the server.

## Acceptance criteria

- [x] Every list-tools toolbar carries pin all; clicking pins each ticket in that list and the pinned panel shows them.

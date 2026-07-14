---
id: IF-0065
title: Per-row copy-id button on the filtered grid
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0065 · Per-row copy-id button on the filtered grid

## Context

The list-tools menu copies every id in a list; grabbing a single ticket's id
meant opening the ticket page or selecting text by hand.

## Approach

A small copy button in each grid row's ID cell, visible on row hover. It reuses
the existing `.copy-one[data-copy]` delegated handler that ticket and epic pages
already ship, so there is no new clipboard code — just the markup and two CSS
rules.

## Acceptance criteria

- [x] Every grid row's ID cell carries a copy button on hover.
- [x] Clicking it copies that one id and does not navigate to the ticket.

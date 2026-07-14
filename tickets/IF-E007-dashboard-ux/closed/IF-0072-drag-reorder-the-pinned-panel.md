---
id: IF-0072
title: Drag-reorder the pinned panel
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0072 · Drag-reorder the pinned panel

## Context

Pins land in the panel in the order they were made; rearranging them
meant unpinning and re-pinning in sequence.

## Approach

Pinned rows are draggable (same pattern as the switcher-menu reorder); on
drop the panel posts the DOM order to /api/pin-order, which re-stamps the named
pins newest-first so the stored order is the displayed order. A 400ms guard
keeps the drop from reading as a click on the link.

## Acceptance criteria

- [x] Dragging a pinned row re-orders the panel and the order survives reload.
- [x] Unknown pin keys in the payload are ignored, not invented.

---
id: IF-0051
title: Theme picker at the bottom of the board
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-13
closed: 2026-07-13
updated: 2026-07-13
index_note: footer swatch row writes theme into config.json; custom palettes shown as a chip
---

# IF-0051 · Theme picker at the bottom of the board

## Context

Changing a project's theme meant editing `.interfacile/config.json` by hand.
The board can already write its own config (the header link editor does), so
the theme deserved the same treatment — from the project itself, quietly.

## Approach

A row of theme dots at the very bottom of the dashboard footer, one per
built-in theme (accent-coloured, ground ring, current one outlined; dimmed
until hovered). Clicking POSTs `/api/theme`, which writes `theme` into the
repo's config.json — the same file, the same key you'd edit by hand — keeping
every other key, then reloads wearing the new palette. Unknown names are
rejected; a custom palette shows as a "custom" chip until a dot replaces it.

## Acceptance criteria

- [x] All 14 built-ins render as dots with the current one marked; a custom
      palette shows the "custom" chip (verified on this repo's acid theme).
- [x] Clicking a dot updates config.json (other keys intact) and the reload
      wears the theme (verified rose end-to-end over HTTP).
- [x] Unknown theme names get a 400; config-less repos get a config created.

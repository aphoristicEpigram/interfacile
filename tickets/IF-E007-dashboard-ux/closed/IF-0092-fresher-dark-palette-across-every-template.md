---
id: IF-0092
title: Fresher dark palette across every template
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0092 · Fresher dark palette across every template

## Context

Dark mode read grimy next to light — muddy blue-gray surfaces, low line
contrast, gray muted ink — across the dashboard, epic, filter, flow and
ticket templates, which all share the canonical dark hex literals.

## Approach

Refresh the twelve canonical dark neutrals in place (cooler navy surfaces,
crisper bluer lines, brighter cool muted ink, cleaner soft tints), replacing
each literal file-wide so every template, the violet-neon remap KEYS and the
_palette_css default all track the same values. Accent literals untouched —
they anchor the theme remaps. Preset palettes derive their own darks and are
unaffected.

## Acceptance criteria

- [x] Dark dashboard/epic/filter/flow pages render the fresh neutrals.
- [x] violet-neon remap still resolves (keys updated with the literals).
- [x] Light mode untouched; tests pass.

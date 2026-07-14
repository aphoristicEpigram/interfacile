---
id: IF-0095
title: Footer settings panel: feature toggles persisted to config.json
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0095 · Footer settings panel: feature toggles persisted to config.json

## Context

Feature flags (starting with the header quote) lived only as hand-edited
config.json keys. The dashboard should offer a check/uncheck surface — and
future features should get theirs for free.

## Approach

A quiet ⚙ settings button right-aligned across from the footer's
launch/stop commands opens a drop-up of feature checkboxes. Rows come from
feature_toggles() — (key, label, note, live value) — so a future feature is
one entry. A toggle POSTs /api/settings (allowlisted keys only), which
persists the top-level config.json key via the shared _config_set (also now
backing save_theme) and applies it live before the panel reloads the page.

## Acceptance criteria

- [x] ⚙ settings sits on the commands line, right-aligned; the panel opens
      as a drop-up, closes on outside click / Escape.
- [x] Toggling "Header quote" hides/shows the quote and writes `quotes`
      into the repo's config.json (verified round-trip on the hub).
- [x] Unknown keys are rejected (400); config edits preserve other keys.
- [x] Suite green (63).

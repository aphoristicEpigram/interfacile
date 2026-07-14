---
id: IF-0094
title: Header quote: rotating, editable, right-aligned on the project-name line
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 2h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0094 · Header quote: rotating, editable, right-aligned on the project-name line

## Context

A little soul on the dashboard: a quiet inspirational quote riding the
project-name line — shipped with the package, editable per repo, and
switchable off.

## Approach

`templates/quotes.txt` ships ~150 `quote --- author` lines (blank lines and
#-comments skipped; author optional); `.interfacile/quotes.txt` in a repo
overrides the packaged set wholesale. `quote_html()` picks one at random per
page load and renders it into the h1 via a `__QUOTE__` token: italic quote,
plain author, right-aligned, small and muted, hidden under 860px. Config
`"quotes": false` turns it off (default on).

## Acceptance criteria

- [x] Dashboard h1 shows “quote” — author, right-aligned, smaller, subtle,
      rotating per load; long quotes wrap with the attribution beneath.
- [x] Repo-local quotes.txt replaces the packaged list; format tolerant.
- [x] `"quotes": false` in config.json hides it.
- [x] quotes.txt ships in package-data; README documents the key.
- [x] Unit tests cover parsing, override, escaping, toggle; suite green (63).

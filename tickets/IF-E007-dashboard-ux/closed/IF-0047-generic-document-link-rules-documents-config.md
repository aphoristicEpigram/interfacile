---
id: IF-0047
title: Document series (documents config) — the full ADR treatment
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-13
closed: 2026-07-13
updated: 2026-07-13
index_note: full ADR treatment per series: linked mentions, /docs/<PREFIX> index, jump box, header button
---

# IF-0047 · Document series (documents config) — the full ADR treatment

## Context

`ADR-###` docs are first-class: mentions autolink, `/adrs` lists them with
status and date, and the jump box finds them. Other projects number other
documents (`PR-001`, `RFC-###`) and wanted the same — declare a prefix and
the system picks it up. (First cut of this ticket only autolinked mentions;
reopened to deliver the whole treatment.)

## Approach

A `documents` list in config: `{"prefix": "PR", "dir": "docs/product",
"title": "Product requirements"}`. ADR becomes just the built-in series and
everything flows through one mechanism (`series_index` / `doc_series`):

- Mentions in rendered tickets/docs autolink to the matching file (recursive,
  scanned live). Unknown numbers stay plain text; collisions link to the
  series index so the reader can choose.
- Each series gets an index page at `/docs/<PREFIX>` — newest first, title /
  `**Status:**` / `**Date:**` parsed like ADRs; `/adrs` stays as the ADR
  alias. A dashboard header button per series.
- Series docs appear in the dashboard jump box next to tickets and epics.
- Rules sanitized on load (malformed prefixes, path traversal, clashes with
  the ticket prefix or ADR are dropped).

## Acceptance criteria

- [x] PR-001 mention links to its doc; collision numbers link to `/docs/PR`;
      unknown numbers untouched; `<pre>` skipped (unit-tested).
- [x] `/docs/PR` renders the index with parsed title/status/date; `/adrs`
      unchanged; unknown series 404 (verified over HTTP).
- [x] Header shows one button per series; jump-box data carries every
      series' records with their kind.
- [x] Rule sanitization and series parsing unit-tested; config reference
      documents the section.

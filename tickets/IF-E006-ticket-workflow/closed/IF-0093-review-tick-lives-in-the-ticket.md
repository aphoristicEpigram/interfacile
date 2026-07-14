---
id: IF-0093
title: Review tick lives in the ticket: Reviewed section replaces review.json
epic: IF-E006
status: CLOSED
risk: MEDIUM
priority: 2
effort: 2h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0093 · Review tick lives in the ticket: Reviewed section replaces review.json

## Context

Review state lived in `.interfacile/review.json` — invisible when reading a
ticket file, unversioned alongside it, and a second source of truth. The tick
should live in the ticket.

## Approach

A `## Reviewed ✅` body section is now the one source of truth: `is_reviewed`
reads it during the scan and on ticket pages, `set_reviewed` appends/removes
it atomically (tail of file; removal stops at the next heading; tolerates a
bare `## Reviewed` and the VS16 emoji variant), and `/api/review` edits the
file instead of the JSON. review.json is no longer read; a one-off sweep
migrated existing state into the files (900 tickets in clean_paste_lite,
using each entry's recorded date) and parked the old stores as
`review.json.migrated`.

## Acceptance criteria

- [x] Checking a tick appends `## Reviewed ✅` + date to the ticket file;
      unchecking removes it and leaves the file byte-identical.
- [x] Dashboard/filter/epic reviewed states derive from the section.
- [x] Marking twice keeps one section; removal spares neighbouring sections.
- [x] Existing review.json state migrated; counts match pre-migration (900).
- [x] Unit tests cover the round-trip and edge cases; suite green (60).

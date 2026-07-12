---
id: IF-0050
title: Pin docs alongside tickets
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-13
closed: 2026-07-13
updated: 2026-07-13
index_note: pin keys with a slash are doc paths; same panel, doc chip, unpin in place
---

# IF-0050 · Pin docs alongside tickets

## Context

Pins were ticket-only, but the things you're watching are often docs — a
blog draft, a spec, an ADR under discussion. Those had no way onto the
dashboard's pinned panel.

## Approach

A pin key containing a slash is a repo-relative markdown path. `/doc/` pages
get the same 🔖 button as tickets (posting their path), `/api/pin` validates
doc paths stay inside the repo, and the pinned panel renders doc entries with
a `doc` chip, the doc's own H1 as title, linking to its `/doc/` page — unpin
works in place, ticket pins unchanged.

## Acceptance criteria

- [x] Pin/unpin round-trips from a doc page; the doc appears in the pinned
      panel with title and chip (verified over HTTP).
- [x] Paths outside the repo or non-markdown are rejected.
- [x] Ticket pins behave exactly as before.

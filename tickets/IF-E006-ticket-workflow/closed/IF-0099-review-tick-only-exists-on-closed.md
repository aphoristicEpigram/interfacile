---
id: IF-0099
title: Review tick only exists on closed tickets
epic: IF-E006
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0099 · Review tick only exists on closed tickets

## Context

The review tick showed on every ticket — open, won't-fix, standing. Review is
something you do to finished work: offering it on an open ticket is an
invitation to tick something that isn't done, and it adds a control to every
row that can't mean anything yet.

## Approach

One predicate, applied on each surface that renders the tick: `is_closed(t)`
(tolerant of the uppercase frontmatter status and the filter page's lowercase
buckets). `_review_chip()` returns "" unless closed; the ticket page's
toolbar button is only emitted for a CLOSED ticket; the dashboard's revChip()
returns "" unless closed. The scan now carries `status` on every ticket record
so the dashboard can see it (which also fixes the panel status sort, whose
data-status was empty for backlog rows).

An open row keeps its pin, still anchored to the row's bottom edge.

## Acceptance criteria

- [x] No review tick on open / won't-fix / standing tickets, on the dashboard,
      the filtered grid, or the ticket page.
- [x] Closed tickets still show the tick and toggle as before.
- [x] Verified live: filter?status=open renders 0 ticks, status=closed renders
      1063; an OPEN ticket page has no Reviewed button, a CLOSED one does.
- [x] Suite green (71).

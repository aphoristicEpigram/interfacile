---
id: IF-0058
title: One filtered view: a single aligned grid, not cards or columns
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 4h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
index_note: /filter is one aligned grid — rows really are grid rows now, measured in the browser
---

# IF-0058 · One filtered view: a single aligned grid, not cards or columns

## Context

`/filter` renders itself three different ways depending on how you arrived: one
status gives you a roomy card grid, several give you status columns (side by
side since IF-0055), and either way you are reading *around* a ticket rather
than down a list. The original complaint — "just a mess of tickets" — was never
really about order. It was about not being able to scan.

Every arrival at this page is the same question: *which tickets, and what are
they?* That deserves one answer, not three layouts.

## Approach

**One list, one look.** `/filter` renders a single aligned grid — a row per
ticket, cells in real columns: id, title, epic, status, priority, risk, effort,
date, pin. You read down a column. Cards and status columns both go; `created`
and `closed` become what they always should have been — two views of the same
grid, one filtered by created date, the other by closed date, each with its own
URL.

**The date column is the one you asked about.** A closed-window query shows the
closed date; everything else shows created. Newest first, ticket number breaking
same-day ties (IF-0055).

**Reuse the machinery, don't rebuild it.** The rows keep the `.col > ul >
li[data-id]` shape and the `data-*` contract the shared toolbar already reads, so
search, the risk/priority/effort filters, the sort dropdown, copy-ids and CSV all
keep working untouched. Alignment comes from a CSS variable holding the column
template, inherited by the header row and every row, so they can't drift apart.

Status only earns a column when the result actually mixes statuses — on a
closed-only view, "CLOSED" on every row is noise.

`_ticket_card` and `_card_groups` existed only for this page's card mode; they go
with it.

## Acceptance criteria

- [x] `/filter` renders one grid, whatever the query — no cards, no status
      columns.
- [x] Columns line up across every row; the header names them. **Reopened for
      this:** they didn't. The epic page's `.col li a` rule (one class, two
      elements) out-specifies a bare `.g-row`, and it declares `display:flex` —
      so every row laid out as a wrapping flex line and the `--gcols` template
      was ignored. It *looked* like a grid because the cells happened to fall in
      a plausible order. Row rules are now scoped to `.col-grid`, which wins the
      cascade, and the child-row indent is undone (a shifted row is a row whose
      cells don't line up). Measured in Chrome: every column's header and value
      share an x-coordinate to the pixel.
- [x] Created views show the created date; closed views show the closed date.
- [x] Newest first, number breaking same-day ties.
- [x] The status column appears only when the result mixes statuses.
- [x] Search, risk/priority/effort filters, sort, copy-ids and CSV still work.
- [x] A ticket can be pinned from any row.
- [x] Dead card code is gone.
- [x] Verified against a running server.

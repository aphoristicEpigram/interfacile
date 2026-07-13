---
id: IF-0055
title: Make the filtered view navigable: newest first, pin in place, columns
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 4h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
index_note: filter view: newest first (number breaks same-day ties), columns, pin from any row
---

# IF-0055 · Make the filtered view navigable: newest first, pin in place, columns

## Context

Captured as: *"Filtered view for closed should be easier to navigate.. Currently
just a mess of tickets."*

`/filter` is where every metric on the dashboard lands — click "created this
week" and you get this page. But it's built like the epic page, which is a page
you *browse*, and it inherits two things that make no sense for a click-through:

- **Oldest first.** The epic page leads with the oldest open ticket on purpose —
  that's the one to work next. But arriving from "created — 7 days", the ticket
  you want is the one you just made, and it's at the bottom.
- **One column, stacked.** Open, then closed, then won't-fix, each full-width,
  one after another. Two short lists become one long scroll, and the answer to
  "what did I open vs close this week" is never on screen at once.

And a row can be *un*pinned here (the 🔖 chip) but never pinned — the one action
you actually want while scanning a list is missing.

## Approach

**Newest first, where "new" means what the column is about.** On `/filter` only,
each column orders by its own date descending: open and won't-fix by `created`,
closed by `closed`. The epic page keeps oldest-first for open — there, the
oldest ticket really is the next one to work.

**Sort you can change.** The shared list toolbar gains `newest` / `oldest` next
to the existing risk/priority/effort sorts, so the default is a starting point,
not a decision. Rows already carry `data-*` for the client-side sorter; they gain
`data-date` (the column's own date) for this.

**Pin in place.** `_pin_chip` learns the unpinned state: a muted 📌 that appears
on row hover and pins on click, alongside the 🔖 that unpins. Both list and card
renderers already call it, so this lands on the epic page too — same gesture
wherever a ticket is listed. It reuses the existing `POST /api/pin` and reload.

**Side by side.** `/filter` stops forcing `grid-template-columns:1fr` and gives
each rendered status column a column, capped at 3; the existing `<900px` rule
already collapses them to one on narrow screens. A "created this week" view is
then *open | closed*, both visible at once.

## Acceptance criteria

- [x] On `/filter`, the newest ticket in each column is at the top (open/wf by
      created, closed by closed); the epic page's open column still leads with
      the oldest.
- [x] Same-day tickets order by ticket number, highest first. **Reopened for
      this:** "newest first" sorted on date alone, and a date is coarse — a busy
      day gives a dozen tickets the same one, so ties fell back to id *ascending*
      and IF-2206 sat above IF-2221. The number is the only thing that says which
      came last, so it is now the tie-break, in whichever direction the date is
      already going (server-side ordering *and* the client-side sort dropdown).
- [x] The sort dropdown offers newest / oldest and re-orders rows client-side,
      keeping parent + sub-ticket families together.
- [x] Every listed row can be pinned *and* unpinned in place, on both `/filter`
      and the epic page, in list and card layouts; the pinned panel reflects it.
- [x] `/filter` shows its status columns side by side (max 3), collapsing to one
      column under 900px.
- [x] Headings say what the order actually is.
- [x] Verified against a running server, not just by reading the HTML: the
      created-this-week view renders `repeat(2,minmax(0,1fr))` with today's
      ticket at the top of the open column, and `POST /api/pin` flips a row's
      chip from `data-pin` to `data-unpin` and back.

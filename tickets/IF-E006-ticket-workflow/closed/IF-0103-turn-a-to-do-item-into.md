---
id: IF-0103
title: Turn a to-do item into a ticket, and tick it off
epic: IF-E006
status: CLOSED
risk: LOW
priority: 3
effort: 3h
created: 2026-07-15
closed: 2026-07-15
updated: 2026-07-15
---

# IF-0103 · Turn a to-do item into a ticket, and tick it off

## Context

The to-do pop-out is where a thought lands when it is too small to stop for. Some
of those thoughts turn out to be real work, and the `capture-ticket` skill exists
precisely to promote one into a ticket and point the note at what it became.

But that promotion is only available by asking an agent. In the pop-out itself,
the item just sits there, and turning it into a ticket means retyping it into a
form and then remembering to come back and tick it off. That is exactly the
friction IF-0100 removed for a thought that arrives while you are reading the
board, and the same fix applies here.

## Approach

Each to-do row gains a quiet control that opens the New-ticket modal with the
item's text already in the title. Fill in whatever else you want and save.

On success the item is linked to the ticket it became and ticked off, so the list
records the outcome instead of losing it. The linking rule is not reimplemented in
the browser: `ticket.link_todo_text` already knows how to name a ticket at the end
of an item and how to extend `(IF-0042)` into `(IF-0042, IF-0043)` rather than
starting a second group. So the promotion happens server-side in one step —
`POST /api/newticket` takes an optional to-do index, creates the ticket, links and
ticks that item, and returns the rewritten to-do content for the pop-out to adopt.

One round trip, one writer, and no chance of the browser's idea of the list and
the file's idea of it drifting apart.

## Acceptance criteria

- [x] Each open to-do item has a control that opens the New-ticket modal with the item's text pre-filled as the title.
- [x] Saving from that modal creates the ticket and, in the same request, ticks the item off and names the ticket on it — `Fix the thing` becomes `Fix the thing (IF-0104)`, done.
- [x] An item that already names a ticket gains the new one alongside it — `(IF-0042)` becomes `(IF-0042, IF-0104)`, not a second group.
- [x] The to-do pop-out shows the ticked, linked item without a page reload, and the file on disk agrees with what is on screen.
- [x] Cancelling the modal leaves the to-do item untouched — not ticked, not linked.
- [x] If the ticket cannot be created, the item is left untouched and the error is shown in the form.
- [x] Creating a ticket from the board (not from a to-do) still works and touches no to-do item.

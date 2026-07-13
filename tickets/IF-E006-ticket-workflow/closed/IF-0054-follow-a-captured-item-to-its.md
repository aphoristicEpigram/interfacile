---
id: IF-0054
title: Follow a captured item to its ticket's status
epic: IF-E006
status: CLOSED
risk: LOW
priority: 3
effort: 3h
created: 2026-07-14
closed: 2026-07-14
depends_on: [IF-0053]
updated: 2026-07-14
index_note: captured ids resolve live: status in the CLI, status-chipped links in the pop-out, /api/ids
---

# IF-0054 · Follow a captured item to its ticket's status

## Context

Capture writes the ticket id back into the item — `(IF-0053)` on a to-do,
`→ IF-0053` on a scratchpad block — but the id is inert. It is text in a
`textContent` span in the pop-out and text in a CLI listing, so the one question
you actually have when you look at a ticketed item ("did that ever get done?")
still costs a trip to the board.

The reference is already there. It just doesn't point anywhere.

## Approach

**Resolve, don't duplicate.** The item stores an id and nothing else; status is
read live from the board every time it's shown, so a to-do can never disagree
with the ticket it names.

**CLI.** `todo` and `scratch` resolve the ids they print and append the ticket's
status, e.g. `IF-0052 CLOSED · IF-0053 OPEN`. An id that no longer exists is
reported as unknown rather than silently dropped. `--json` carries the same in a
`tickets` array (`id` / `status` / `title`), so an agent can see at a glance
what it already captured.

**Dashboard.** A small `/api/ids` endpoint returns `{id: {status, title}}` from
the scan the server already runs. The pop-out linkifies any ticket id it finds
in a to-do item or a scratchpad block — `/ticket/<ID>`, which the hub already
redirects across projects — and chips it with its live status, muted once
closed. Clicking a captured item takes you to what it became.

## Acceptance criteria

- [x] `interfacile todo` / `scratch` print the status of every referenced ticket,
      and say so when an id is unknown.
- [x] `--json` includes a `tickets` array (`id`, `status`, `title`) per item.
- [x] `/api/ids` returns id → status/title for every ticket, and is covered by a
      test.
- [x] In the pop-out, a ticket id in a to-do item or scratchpad block is a link
      to `/ticket/<ID>` carrying a status chip; closed reads as muted/done.
      (Only ids the board knows are linked — a captured link never 404s. The
      scratchpad's preview goes through the server's autolinker, which already
      had this rule.)
- [x] Status is never stored in the note file — always resolved at display time.

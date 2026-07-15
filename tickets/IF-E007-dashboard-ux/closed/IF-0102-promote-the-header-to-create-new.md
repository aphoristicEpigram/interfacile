---
id: IF-0102
title: Promote the header + to Create new ticket; move add-link into settings
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-15
closed: 2026-07-15
updated: 2026-07-15
---

# IF-0102 · Promote the header + to Create new ticket; move add-link into settings

## Context

The top row's `+` currently adds a header link — a website shortcut. That was the
right call when it was the only thing there was to add, but it is not what the
button's prime position is worth: over the life of a project you will create
hundreds of tickets and add a handful of links. The prominent affordance is
pointing at the rare action.

IF-0100 then made it worse by adding a *second* `+` for New ticket, so the header
now has two plus signs sitting next to each other meaning different things. One
of them has to go.

## Approach

There is one `+` in the top row and it creates a ticket. The button IF-0100 added
is deleted; the existing `+` — its dashed pill already designed and in place — is
repurposed to open the New-ticket modal. Nothing new is drawn.

Adding a link moves down into the ⚙ settings drop-up in the footer, next to the
other things you configure once and forget. Its editor popover comes with it, and
re-anchors to the bottom of the screen where it is now opened from.

The link *buttons* themselves stay in the header — only the affordance for adding
one moves. Rearranging what you reach for daily is not the same as hiding what you
reach for yearly.

## Acceptance criteria

- [x] Exactly one `+` in the header, and clicking it opens the New-ticket modal.
- [x] The extra `+` button added by IF-0100 is gone from the header, the bar, and every sub-page.
- [x] Adding a header link is reachable from the ⚙ settings panel and still works: add a link, remove a link, and the header link buttons update.
- [x] The link editor popover appears near the settings panel it was opened from, not at the top of the page.
- [x] Existing header link buttons still render in the header, and still open their sites.
- [x] `n` still opens the New-ticket modal on every page.

---
id: IF-0106
title: Pin a ticket straight from the search dropdown
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 3h
created: 2026-07-15
closed: 2026-07-15
updated: 2026-07-15
---

# IF-0106 · Pin a ticket straight from the search dropdown

## Context

The jump box is the fastest way to find a ticket — type `2221`, it's there. But
the only thing you can do from a result is open it. To pin a ticket you have
found by searching, you first have to navigate into it (or into the board and
hunt for its row), pin it there, and come back. The moment you know which ticket
you want to watch is the moment the search result is in front of you, and that is
the moment you can't act on it.

## Approach

Each ticket result in the dropdown gains a small pin toggle at the right of the
row, showing whether it is already pinned. Clicking it pins or unpins through the
existing `/api/pin`, and does not open the ticket — the row's own link still does
that, as before.

The whole row is an `<a>`, so the toggle cannot itself be a nested interactive
element that navigates; it reuses the same `role`/delegated-click pattern the
board's rail pin chip already uses, catching the click before the row's link
fires.

Pinning from here has to leave the page consistent without a reload: the result's
own pinned state flips in place so the icon updates, and the board's pinned panel
is refreshed from the same fetch the rest of the dashboard uses, so what you just
pinned shows up in the watching list immediately. Only tickets get the toggle —
epics and documents are not pinnable this way.

## Acceptance criteria

- [x] Every ticket result in the search dropdown shows a pin toggle; epic and document results do not.
- [x] The toggle shows the current pin state — a ticket that is already pinned reads as pinned.
- [x] Clicking the toggle pins or unpins the ticket and does not navigate to it.
- [x] Clicking anywhere else on the row still opens the ticket, as it does today.
- [x] After pinning, the icon reflects the new state without a page reload, and the ticket appears in (or leaves) the pinned panel.
- [x] Keyboard: the result is still reachable and openable by keyboard, and the toggle is operable without a mouse.

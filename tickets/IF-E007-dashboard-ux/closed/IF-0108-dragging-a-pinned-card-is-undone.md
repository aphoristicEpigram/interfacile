---
id: IF-0108
title: Dragging a pinned card is undone when the panel is sorted
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-15
closed: 2026-07-15
updated: 2026-07-15
---

# IF-0108 · Dragging a pinned card is undone when the panel is sorted

## Context

The pinned panel can be reordered by dragging a card, and it can be sorted by the
display-only "sort" dropdown in its header. With the sort left on "default" the
drag works. With any other sort chosen, dragging does nothing — the card snaps
straight back and the order never changes.

The two features fight. The sort attaches a `MutationObserver` to `#pinnedList`
that re-applies the sort whenever the list's children change. A drag reorders by
`insertBefore` on every `dragover` — which is exactly the mutation the observer is
watching for, so it re-sorts the row back to its sorted position the instant you
move it. Even if you manage to drop, the order stamped to the server is the
sorted order, not the one you dragged.

## Approach

Dragging is manual ordering, and manual ordering should win — that is the whole
point of being able to drag. So starting a drag resets the panel's sort to
"default": the display sort was a way to look at the list, and the moment you
take hold of a card you are declaring the order yourself.

Concretely, on `dragstart` in the pinned list, set the header's sort select back
to "default". Setting it programmatically does not fire a change event, and the
observer already skips re-sorting while the select reads "default" — so it stops
fighting the drag without any new coordination between the two scripts. The rows
keep the on-screen order they had (no jarring jump back to served order at the
moment you grab one); the drag then edits that order, and `dragend` stamps the
result to `/api/pin-order` as it does today.

Only the pinned panel is draggable, so only it needs this; the WIP panel's sort
is unaffected.

## Acceptance criteria

- [x] With the pinned panel sorted by priority (or any non-default sort), dragging a card to a new position moves it and it stays there.
- [x] Starting a drag resets the panel's sort control to "default"/"sort".
- [x] The cards do not jump to a different order at the moment the drag starts — they keep their on-screen order, and only the dragged card moves.
- [x] The new order after a drag is stamped to the server (survives a reload), whatever sort was active before.
- [x] Dragging with the sort already on "default" works exactly as before.
- [x] The WIP panel's display sort is unchanged.

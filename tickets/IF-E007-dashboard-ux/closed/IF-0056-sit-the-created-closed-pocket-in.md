---
id: IF-0056
title: Sit the created/closed pocket in the header's button row
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
index_note: pocket moved into .head-btns beside GitHub, matching pill height
---

# IF-0056 · Sit the created/closed pocket in the header's button row

## Context

The `181 created · 169 closed` pocket sat as the middle child of a
`justify-content:space-between` header row — eyebrow on the left, buttons on the
right, pocket wherever the leftover space happened to put it. It wasn't centred
on anything, and it moved with the length of the eyebrow and the width of the
button cluster, which is why it read as "off".

## Approach

Put it in the cluster it belongs to: the pocket becomes the first child of
`.head-btns`, immediately left of the GitHub button, and takes the same pill
geometry (`padding:7px 14px`, `border-radius:100px`, `surface-2`) plus a
`min-height` matching GitHub's icon so the two boxes are the same height rather
than merely centred against each other. The header row is then two things —
eyebrow, controls — and there is no floating middle element to drift.

## Acceptance criteria

- [x] The pocket renders inside `.head-btns`, directly before `#ghBtn`.
- [x] Pocket and GitHub button share a centre line and a height.
- [x] The pocket's window toggle (today / week / month) and its two links still
      work; it stays hidden until the scan populates it.
- [x] Verified against a running server.

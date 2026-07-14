---
id: IF-0087
title: Anchor the rail pin to the card's bottom edge
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0087 · Anchor the rail pin to the card's bottom edge

## Context

Follow-up to IF-0086. The rail pin sat a fixed 6px under the review tick, so
on cards with long titles it floated mid-row while the chip line sat lower —
the bottom edge read ragged across cards.

## Approach

Stretch `.b-rail` to the row's height (`align-self:stretch`) and give the pin
`margin-top:auto`: the tick keeps riding the title line, the pin anchors to
the row's bottom edge, and doc rows (pin-only rail) anchor the same way.

## Acceptance criteria

- [x] The pin sits on the card's bottom line regardless of title length; the
      bottom row (chips + pin) is uniform card to card.
- [x] Review tick unchanged, top-right on the title line.

---
id: IF-0096
title: Remove healing quotes; quote turns over on every ticket interaction
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0096 · Remove healing quotes; quote turns over on every ticket interaction

## Context

The shipped quote list carried a strand of pain-and-healing material — "your
pain is the portal to your power", "the wound is the place where the light
enters you" — that doesn't belong on a dashboard. The quotes are meant to be
motivational. Quantum / esoteric material stays; pain and healing go.

Separately, the quote only turned over on a full page load, so Regenerate and
ticket interactions left it stale.

## Approach

Remove the pain/woundedness/healing lines (7 in total across two passes:
Bernstein, Maté ×2, Teal Swan, Rumi's wound, Brené Brown's "wired for
struggle", Mastin Kipp), leaving 141. Then make the quote turn over with the
board: `pick_quote()` rides on /api/data (so Regenerate deals a new one), `GET
/api/quote` serves one on its own, and the dashboard repaints `#hQuote` from
either. Pin and reorder already reload the board; the review tick — which
flips in place by design (IF-0083) — calls newQuote() so it turns over too.

## Acceptance criteria

- [x] No pain / wound / trauma / healing quotes remain; 141 left, in order.
- [x] Quantum and esoteric quotes are untouched.
- [x] Regenerate deals a new quote (rides /api/data).
- [x] Pin, unpin, review, unreview and reorder each turn the quote over.
- [x] Quotes-off still renders nothing; suite green.

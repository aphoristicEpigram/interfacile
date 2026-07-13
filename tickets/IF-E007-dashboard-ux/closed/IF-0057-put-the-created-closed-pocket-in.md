---
id: IF-0057
title: Put the created/closed pocket in the top bar, on every page
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
index_note: pocket lives in the top bar next to GitHub on every page; counts computed server-side
---

# IF-0057 · Put the created/closed pocket in the top bar, on every page

## Context

IF-0056 moved the pocket next to GitHub *in the dashboard's header card*. But
GitHub doesn't live there — on a hub, `relocate()` lifts it into the sticky top
bar the moment the page loads, and the bar is the thing that follows you from
page to page. The pocket stayed behind in the header, so it went to the wrong
place and only existed on the board.

Where it belongs is the bar: up top, beside GitHub, on every page.

## Approach

**Count once, on the server.** The dashboard currently tallies the windows in JS
by walking every epic's tickets — which the bar on a ticket page can't do, since
it never loads that payload. So `scan()` grows a `pocket` key: created/closed for
today / 7 days / 30 days, using the same rolling windows `/filter` already uses,
so the counts and the page they link to can't disagree (they could before — the
JS counted in the browser's timezone, the filter page in the server's).

A small `/api/pocket` serves just that, for pages that don't fetch the board.

**One pocket, one behaviour.** A shared `_POCKET_JS` owns filling the counts,
cycling the window (today → 7d → 30d, remembered in `localStorage`), and pointing
the two links at their views. The dashboard hands it `d.pocket` from the payload
it already has; the bar on other pages fetches `/api/pocket`. Same module, so the
pocket behaves identically wherever it is.

**Getting it up there.** `relocate()` moves `#todayPocket` into `#ifc-center`
alongside GitHub — the same trick that already moves GitHub, links and notes —
and `_persist_controls_html()` renders one for non-dashboard pages.

Single-interface installs have no bar at all (`_switcher_html` returns "" below
two interfaces), so the pocket stays in the header card there. Nothing is lost;
it simply doesn't fly up when there's nowhere to fly to.

## Acceptance criteria

- [x] On a hub, the pocket renders in the top bar next to GitHub on the
      dashboard *and* on ticket / epic / filter / doc pages.
- [x] Counts come from the server (`scan()["pocket"]` / `/api/pocket`) and match
      what the linked filter view actually lists.
- [x] The window toggle cycles today → 7 days → 30 days, persists across pages,
      and re-points both links.
- [x] With one interface (no bar), the pocket still works in the header card.
- [x] The client-side counting loop is gone — one implementation, in Python.
- [x] Verified against a running hub, on more than one page.

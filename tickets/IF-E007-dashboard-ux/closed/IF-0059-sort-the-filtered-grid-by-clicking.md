---
id: IF-0059
title: Sort the filtered grid by clicking its column headers
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
index_note: grid column headers sort: click to order, click again to reverse; comparator handles text, numbers and missing values
---

# IF-0059 · Sort the filtered grid by clicking its column headers

## Context

The grid (IF-0058) has column headers, and column headers are a promise: *this
is what the column is, and you can order by it.* Ours only kept half of that.
Sorting lived in a dropdown a few inches away, offering four of the nine columns
— so the headings were decoration, and the one control that did work didn't look
like it belonged to the thing it acted on.

Either the headers sort, or they shouldn't look like headers.

## Approach

**The engine already exists.** `LIST_FILTER_SCRIPT` sorts groups of `li` (parent
+ sub-tickets move together) by a key read off `data-*`. Clicking a header just
has to set that key. What it *can't* do today is compare text: every key is
coerced to a number and subtracted, which works for priority and effort and is
nonsense for a title or an epic.

So the comparator grows up. A key becomes `{value, missing}`; comparison is
numeric when both values are numbers and a string compare otherwise; and a
missing value always sinks to the bottom, in either direction — an unestimated
ticket shouldn't lead the list just because you sorted descending.

**Columns need the data to sort on.** Rows carry `data-num`, `data-date`,
`data-prio`, `data-risk`, `data-effh` already; they gain `data-title`,
`data-epic` and `data-status` so every visible column can be a sort key.

**The header becomes the control.** Each heading is a button: click to sort,
click again to reverse. First click picks the direction the column is usually
read in — dates and ids newest/highest first, text A→Z, priority/risk/effort
best-first. An arrow marks the active column, so the grid always says out loud
how it is ordered. The page still arrives sorted newest-first, with that arrow
already on the date column.

**One control, not two.** The sort dropdown comes off the filtered view — the
headers do that job now, and two controls for one action is how you get them
disagreeing. Epic pages keep the dropdown (they're lists, not grids), so
`LIST_FILTER_BAR` becomes a small function rather than a constant, and the script
tolerates the select being absent.

## Acceptance criteria

- [x] Every grid column sorts from its header; a second click reverses it.
- [x] Text columns (title, epic) sort alphabetically; id, priority, risk, effort
      and date sort in their natural order — not as strings.
- [x] Rows with no value for the sorted column sink to the bottom, both ways.
- [x] The active column shows its direction; the page loads with the date column
      marked, matching the server's newest-first order.
- [x] Sub-tickets stay attached to their parent through a sort.
- [x] Headers are keyboard-reachable and operable (Enter / Space).
- [x] The sort dropdown is gone from the filtered view and still works on epic
      pages; search, the filters, copy-ids, CSV and pinning all still work.
- [x] Verified against a running server.

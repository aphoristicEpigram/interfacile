---
id: IF-0105
title: Copy id and dependency link, in the same place on every ticket card
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 4h
created: 2026-07-15
closed: 2026-07-15
updated: 2026-07-15
---

# IF-0105 · Copy id and dependency link, in the same place on every ticket card

## Context

Copying a ticket id is the single most common thing you do to a card — it is how
a ticket gets into a commit message, a branch name, or a prompt. Right now the
button that does it moves depending on which list you happen to be looking at:

- on a **grid row** it sits at the left, beside the id
- on a **dashboard list card** it leads the right-hand chip group, on the far side
  of the title
- on an **epic list item** it leads the meta chips, also on the right
- on the **sub-ticket table** of a ticket page there is no copy button at all, and
  no dependency link either

So the control you reach for most has no fixed home, and you hunt for it every
time. The dependency link has the same problem, and the two belong together: they
are the two things you want *about* a ticket without opening it.

There is also no way to copy a **parent's** id from a sub-ticket's page. The
breadcrumb names the parent but gives you nothing to grab.

## Approach

One rule: **copy-id and the dependency link live together, and the pair is the
leftmost thing on the row's chip line.** Pressable controls on one side, readable
chips on the other.

The real defect on the homepage cards was never that the controls were on the
right — it was that `margin-right:auto` was hung on *whichever chip happened to
come first*. So on a ticket with dependencies the dep chip took the left slot and
copy drifted right with the tags; on a ticket without them, copy took the left
slot. The control you reach for most moved depending on the ticket. Wrapping the
pair in one element and putting the `margin-right:auto` on **that** fixes it at the
left of the tag line, permanently, in both cases.

The epic lists and grid rows are single-line layouts with no separate chip line,
so there the pair sits immediately after the id — the same left-hand position.

The sub-ticket table on a ticket page is a table, not a card: its id is already the
first column, so the controls go at the **right** end of each row, aligned in a
column, where a table's actions belong. The parent breadcrumb gets the same pair,
so the parent's id is copyable from the child without navigating to it.

Cards are wrapped in an `<a>`, so the dependency control cannot itself be an
anchor — nested anchors are invalid. It reuses the existing `role="link"` +
delegated-click pattern that the epic chips already use for exactly this reason.

## Acceptance criteria

- [x] On every homepage card the copy-id and dependency controls are the leftmost items on the chip/tag line, and the informational chips stay at the right of that same line.
- [x] Copy sits in exactly the same place whether or not the ticket has dependencies — its position never depends on the presence of the dep chip.
- [x] On the epic lists and grid rows (single-line layouts) the pair sits immediately after the ticket id.
- [x] The controls are never loose among the informational chips — they are wrapped as one unit, so nothing can reorder them relative to each other or to the tags.
- [x] The informational chips (risk, priority, effort, blocked, unblocks, WIP, date) stay on the right, unchanged.
- [x] Each row of the sub-ticket table has a copy-id and a dependency link, at the right end of the row.
- [x] The parent breadcrumb on a sub-ticket's page has a copy-id and a dependency link for the parent.
- [x] Copying from any of these puts the ticket id on the clipboard and does not navigate.
- [x] The dependency control opens `/deps` focused on that ticket and does not navigate to the ticket page.
- [x] The dependency control appears only for tickets that are actually in the dependency graph, as it does today.

---
id: IF-0060
title: Same bar layout everywhere: created/closed on the right, aligned
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
index_note: bar is identical on every page: GitHub centred, created/closed + links + notes right, all 32px
---

# IF-0060 · Same bar layout everywhere: created/closed on the right, aligned

## Context

Measured in the browser, the bar is not the same on every page:

- **Dashboard:** centre reads `[created/closed] [GitHub]`.
- **Everywhere else:** centre reads `[GitHub] [created/closed]`.

The order flips. `relocate()` moves `#todayPocket` into the bar with
`appendChild`, which is a *move* — on the dashboard it lands before GitHub, but
on a sub-page the pocket is already server-rendered in the bar, so appending it
again drags it to the end. The same bug is latent for anything else with an id
that the bar already renders.

And the pocket is 28px tall where every other bar control is 32px, so it doesn't
sit on the same line as the buttons beside it.

## Approach

**The pocket belongs with the ticket controls, on the right.** It is a pair of
links into ticket lists — the same kind of thing as the to-do and scratchpad
buttons, not the same kind of thing as GitHub. So it joins `#ifc-actions` as its
first child: `[created/closed] [project links] [scratchpad] [to-do]`, with the
icon buttons staying where they already are at the far right. GitHub keeps the
centre to itself.

**Relocation stops reordering what it doesn't own.** `relocate()` gains a
`place()` helper that skips any element already inside its target slot, so the
server-rendered order on a sub-page is left exactly as it is, and only the
dashboard's header controls actually move. Placement is explicit (first / last)
rather than "wherever appendChild happens to put it".

**One control height.** The bar's buttons are 32px; the pocket becomes 32px too,
so the whole row shares a baseline.

## Acceptance criteria

- [x] Bar order is identical on dashboard, ticket, epic and filter pages:
      switcher left, GitHub centred, then created/closed + links + notes right.
- [x] The pocket is the same height as the buttons beside it (32px), measured in
      a browser, not eyeballed. GitHub was the odd one out too — its height came
      from padding (28px) while the icon buttons were a fixed 32, so *nothing* in
      the bar shared a height. All four controls are now 32px on one baseline.
- [x] Relocation never reorders controls the bar already rendered.
- [x] The pocket still counts, toggles its window, and links correctly from
      every page; single-interface installs (no bar) still show it in the header.

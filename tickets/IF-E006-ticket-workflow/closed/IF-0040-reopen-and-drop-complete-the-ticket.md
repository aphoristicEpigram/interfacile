---
id: IF-0040
title: reopen and drop complete the ticket lifecycle
epic: IF-E006
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-12
closed: 2026-07-12
updated: 2026-07-12
index_note: reopen mirrors close; drop records WONT_FIX with the why
---

# IF-0040 · reopen and drop complete the ticket lifecycle

## Context

`close` was one command, but undoing it meant hand-editing three frontmatter
fields and moving the file — breaking the "never hand-edit state" promise. And
there was no honest way to record "we're not doing this" with the reason kept.

## Approach

`interfacile reopen ID` mirrors close: status OPEN, strips `closed:` and the
stale `index_note:`, moves the file back to `open/`. `interfacile drop ID
--why "..."` records a deliberate WONT_FIX with the reason on the record;
it satisfies dependencies exactly like CLOSED, so droppers unblock rather
than strand. Both share the close command's rewrite/move machinery.

## Acceptance criteria

- [x] reopen restores an open ticket (fields, bay) and re-blocks dependants.
- [x] drop requires --why, records it, and unblocks dependants (reported).
- [x] Both refuse epics and no-op states with clear messages; covered by
      unit tests.

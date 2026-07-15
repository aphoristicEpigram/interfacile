---
id: IF-0104
title: Ticket page says blocked when every blocker is closed
epic: IF-E007
status: CLOSED
risk: MEDIUM
priority: 1
effort: 2h
created: 2026-07-15
closed: 2026-07-15
updated: 2026-07-15
---

# IF-0104 · Ticket page says blocked when every blocker is closed

## Context

`EM-2222-E-iv` is ready to start. Its three blockers — `EM-2222-E-i`, `-ii` and
`-iii` — are all closed, and the board agrees: `scan()` marks it `blocked: false`,
and it appears in the unblocked list.

Its own ticket page says **"Dependency graph · blocked by 3"**.

The dependency button counts every incoming edge and never asks whether the
ticket at the other end is finished:

    blocked_by = sum(1 for b, t in dep_graph["edges"] if t == tid)

So a dependency that has been *satisfied* still reads as a dependency that is
*blocking*, and the one screen you open to decide whether you can start work is
the one screen that tells you that you can't. The board and the ticket page
disagree about the same ticket, and the ticket page is the one that's wrong.

Worse, the state you actually want to see — this is unblocked *now*, the thing
you were waiting for landed — is not celebrated anywhere. It is the most useful
transition on the board and it is currently invisible.

## Approach

Count blockers that are still open, not edges. A blocker whose status is CLOSED
or WONT_FIX is satisfied — that is already what `ticket.DONE` means and what
`scan()` uses to decide `blocked`, so the ticket page should ask the same
question rather than a different one.

Then say the answer plainly, and distinguish three states instead of one:

- **blocked by N** — work you cannot start (the warning state)
- **unblocked** — it had blockers, they are all done, you can start now (the
  good state, and visually distinct: this is the thing worth noticing)
- no blockers at all — nothing to say beyond what it blocks

The same rule drives the ticket page's `blocked` chip and the dependency button's
summary, so the two can never contradict each other or the board again.

## Acceptance criteria

- [x] `EM-2222-E-iv`, whose three blockers are all closed, does not say "blocked by 3" anywhere on its page.
- [x] A ticket with at least one OPEN blocker still says "blocked by N", counting only the open ones.
- [x] A ticket whose blockers are all closed reads clearly as unblocked, and is visually distinct from a ticket that never had blockers.
- [x] The ticket page's view of blocked/unblocked agrees with `scan()`'s `blocked` flag for the same ticket.
- [x] A WONT_FIX blocker satisfies a dependency, exactly as `interfacile ready` already treats it.
- [x] A test covers a ticket whose blockers are all closed and fails if it is reported as blocked.

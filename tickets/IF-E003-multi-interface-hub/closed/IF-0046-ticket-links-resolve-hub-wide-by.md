---
id: IF-0046
title: Ticket links resolve hub-wide by id prefix
epic: IF-E003
status: CLOSED
risk: LOW
priority: 1
effort: 2h
created: 2026-07-13
closed: 2026-07-13
updated: 2026-07-13
index_note: /ticket/<id> 302s to the interface owning the prefix; shared prefixes warned
---

# IF-0046 · Ticket links resolve hub-wide by id prefix

## Context

Typing `/ticket/EM-2196` while the "wrong" interface was active 404ed, even
though id prefixes are unique per project — the URL was unambiguous, the hub
just refused to look sideways.

## Approach

When the active interface doesn't know the id, find the interface whose
configured prefix owns it and 302 there with the `ifc` cookie set — the id
works whichever board is showing. Unknown prefixes still 404, and the hub
warns at startup when two interfaces share a prefix (that's the one case
that would make ids ambiguous).

## Acceptance criteria

- [x] `/ticket/<other-project-id>` redirects with the owning interface's
      cookie and renders (verified over HTTP).
- [x] Unknown prefixes 404; the active interface's own missing ids don't
      bounce (unit-tested owner lookup).
- [x] Shared-prefix hubs get a startup warning.

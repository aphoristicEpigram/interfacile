---
id: IF-0077
title: Tag filter in the dashboard backlog controls
epic: IF-E007
status: CLOSED
risk: LOW
priority: 2
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0077 · Tag filter in the dashboard backlog controls

## Context

Tags (blocked/decision/wip/pinned/review state) could be filtered on the
/filter grid but not from the backlog controls at the top of the home page.

## Approach

A tags select beside the backlog's blocked filter, wired like its siblings:
STATE.fTag, passFilters, the filtering flag, hash persistence, and the change
listener. Decision detection reuses the title test ticketRow already runs.

## Acceptance criteria

- [x] The backlog filters by blocked/decision/wip/pinned/untagged and reviewed/not-reviewed from the top controls, and the choice survives in the URL hash.

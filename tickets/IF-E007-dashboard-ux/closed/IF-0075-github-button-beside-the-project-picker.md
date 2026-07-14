---
id: IF-0075
title: GitHub button beside the project picker
epic: IF-E007
status: CLOSED
risk: LOW
priority: 4
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0075 · GitHub button beside the project picker

## Context

With the pocket centred, GitHub rode the right-hand cluster - but as a
repo-level link it reads better beside the project picker on the left.

## Approach

A #ifc-left slot after the switcher: sub-pages render the GitHub link into it
server-side; the dashboard's relocate() moves its ghBtn there. Same flex row
styling, hidden when empty.

## Acceptance criteria

- [x] On every page of the hub the GitHub button sits directly right of the project picker; pocket stays centred, links/notes stay right.

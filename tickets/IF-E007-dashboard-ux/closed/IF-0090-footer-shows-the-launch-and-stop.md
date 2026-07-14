---
id: IF-0090
title: Footer shows the launch and stop-all commands, click to copy
epic: IF-E007
status: CLOSED
risk: LOW
priority: 4
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0090 · Footer shows the launch and stop-all commands, click to copy

## Context

Restarting the hub means remembering two commands. The dashboard is where you
notice you need them, so it should quietly offer both.

## Approach

One muted mono line in the dashboard footer — `interfacile hub` ·
`interfacile stop --all` — each a click-to-copy button riding the existing
copy-one handler; the line sits at 70% opacity and wakes on hover.

## Acceptance criteria

- [x] Footer shows both commands on one subtle line, above the theme picker.
- [x] Clicking either copies it and flashes "copied".

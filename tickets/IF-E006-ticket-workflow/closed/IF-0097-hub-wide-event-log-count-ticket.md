---
id: IF-0097
title: Hub-wide event log: count ticket interactions so automations can hang off them
epic: IF-E006
status: CLOSED
risk: MEDIUM
priority: 2
effort: 3h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0097 · Hub-wide event log: count ticket interactions so automations can hang off them

## Context

Automations need to hear about ticket interactions — and to hear about them at
the GROUP level, across every project the hub serves, not per repo. Nothing
recorded them: a close from `interfacile close` and a pin from a dashboard
click left no common trace to trigger off.

## Approach

New `events.py`: one hub-wide log at `~/.config/interfacile/events.json`
holding a monotonic `seq` (the cursor a poller catches up from), cumulative
`counts` (hub-wide and per interface), and a capped `recent` window. The
server records pin / unpin / review / unreview / reorder against the active
interface; the CLI records new / close / reopen / drop — so both a click and a
command land in the same stream. `GET /api/events?since=<seq>[&interface=]`
is the automation surface. record() never raises: a log that cannot be written
must not be why a close fails. Policy stays out — this is plumbing only.

Also fixes a bug the CLI hook exposed: the ticket-flow tests ran real
CLI commands without isolating XDG_CONFIG_HOME, so they wrote into the
developer's real global log. They now get a temp home, like the procs tests.

## Acceptance criteria

- [x] pin/unpin/review/unreview/reorder (server) and new/close/reopen/drop
      (CLI) all append to one hub-wide log with per-interface attribution.
- [x] GET /api/events returns seq + counts + interfaces + events; `since`
      returns only what followed the cursor; `interface` narrows it.
- [x] Counts are cumulative; `recent` is capped (500); a corrupt log reads as
      empty and never breaks the interaction it was recording.
- [x] Verified live: a dashboard pin/review in clean-paste and a CLI
      new/close in interfacile appear in one stream with one cursor.
- [x] Tests isolate XDG_CONFIG_HOME so the suite never writes the real log.
- [x] Suite green (70).

---
id: IF-0101
title: Event log names the same interface two different ways
epic: IF-E003
status: CLOSED
risk: LOW
priority: 2
effort: 1h
created: 2026-07-15
closed: 2026-07-15
updated: 2026-07-15
---

# IF-0101 · Event log names the same interface two different ways

## Context

The hub-wide event log carries an `interface` field, and the per-interface counts
are keyed on it. Two writers fill that field, and they disagree:

- the CLI (`ticket._log`) writes `os.path.basename(repo.root)` — the raw folder name
- the server (`server._record`) writes `ACTIVE_SLUG` — the slugified name

For a project whose folder name contains an underscore or a space, those are
different strings. The live log already shows it: acting on a ticket from the
terminal in `~/Projects/clean_paste_lite` records `interface: clean_paste_lite`,
while clicking one in the dashboard for the same project records
`interface: clean-paste-lite`.

So one project's history is split across two keys. `GET /api/events?interface=…`
returns only the half that matches whichever spelling the caller guessed, and the
per-interface counts under-report by however much came from the other writer.
Nothing errors — the numbers are just quietly wrong.

## Approach

Give both writers one way to name an interface. The slug is the right answer,
because it is already what the URL, the `ifc` cookie and `?interface=` use; the
basename is the accident.

Expose the slugifier the registry already uses as a named function, and have
`ticket._log` call it instead of `os.path.basename`, so a ticket created or closed
from the terminal lands under the same key as one clicked in the dashboard.

Events already written keep their old spelling. Rewriting a log that other tools
may have read is a bigger promise than this ticket makes, and the split stops
either way.

## Acceptance criteria

- [x] `ticket._log` and `server._record` write the same `interface` value for the same repo.
- [x] A repo whose folder name contains an underscore (`clean_paste_lite`) records the slug (`clean-paste-lite`) from both the CLI and the dashboard.
- [x] There is one slug function used by both, not two implementations that agree by coincidence.
- [x] A test covers a repo whose basename and slug differ, and fails if they diverge again.

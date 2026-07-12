---
id: IF-0041
title: JSON output for tickets, ready, show, lint
epic: IF-E006
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-12
closed: 2026-07-12
updated: 2026-07-12
index_note: --json on tickets/ready/show/lint with derived blocked_by
---

# IF-0041 · JSON output for tickets, ready, show, lint

## Context

Scripts, CI gates, and agents had to parse pretty-printed columns to read the
board. The scaffolded skills deserved something sturdier to stand on.

## Approach

`--json` on the four read commands. One stable card shape (id, title, epic,
status, risk, priority, effort, dates, depends_on, blocks, derived
`blocked_by`, path); `show --json` adds full frontmatter + body; `lint --json`
returns errors/warnings as objects plus the skills-staleness notice, exit
code unchanged.

## Acceptance criteria

- [x] All four commands emit valid JSON honouring their filters.
- [x] `blocked_by` is the derived open-dependency list.
- [x] Exit codes match the human output; covered by unit tests.

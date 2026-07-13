---
id: IF-0053
title: Ticket a scratchpad note; one capture skill for both sources
epic: IF-E006
status: CLOSED
risk: LOW
priority: 2
effort: 4h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
index_note: interfacile scratch (list/link) + capture-ticket skill over both sources
---

# IF-0053 · Ticket a scratchpad note; one capture skill for both sources

## Context

IF-0052 gave the to-do list a way out: pick an item, answer a few questions,
get a ticket, tick the item off. The scratchpad — the other half of the pop-out,
and the other half of the to-do line that started this — has no such exit. It is
where the longer thoughts go, and they stay there.

The two sources differ in shape, not in purpose: both are capture, and both leak
work that never reaches the board. Two skills for that would be two rituals to
remember and two files to keep in step.

## Approach

**One skill, both sources.** `todo-ticket` becomes **`capture-ticket`**: it reads
the to-do list *and* the scratchpad, shows what it found in each, and you pick.
Everything downstream is unchanged — clarifying questions, drafting delegated to
`new-ticket`, one item per run. (Safe to rename: the skill is a day old and
unreleased, so no repo has it installed but this one.)

**`interfacile scratch` — the CLI owns the file, as with `todo`.**

    interfacile scratch [--json]              blocks, numbered
    interfacile scratch link N --ticket ID    annotate block N with `→ ID`

A *block* is a run of non-blank lines — the natural unit of a note. Numbering is
1-based over every block, ticketed or not, so an index means the same thing on
the next run. `link` appends ` → ID` to the block's last line (a second id
extends it to `→ ID, ID`), never double-appends, and rewrites the file
line-for-line so everything else — blank lines, indentation, trailing newline —
is byte-identical. Annotating rather than cutting means the CLI never destroys
prose the user wrote.

The note state helpers in `server.py` grow a root-parameterised pair
(`load_note_at`/`save_note_at`) so `todo` and `scratch` share one path resolver
instead of a third copy.

## Acceptance criteria

- [x] `interfacile scratch` lists blocks with stable 1-based indices; `--json`
      emits `n`/`text`/`tickets` per block.
- [x] `interfacile scratch link N --ticket ID` appends `→ ID` to block N's last
      line; a second id yields `→ ID, ID`; re-running with the same id is a
      no-op (exit 0).
- [x] Every other byte of the scratchpad survives a `link` — blank lines,
      indentation, and the trailing newline included.
- [x] Out-of-range `N`, and `link` without `--ticket`, exit non-zero. (Also an
      id the repo's scheme could never resolve: writing a pointer to nowhere is
      worse than refusing.)
- [x] An empty/missing scratchpad lists cleanly (exit 0), like `todo`.
- [x] `todo-ticket` is renamed `capture-ticket`, covers both sources, and still
      delegates drafting to `new-ticket`; no `todo-ticket` remains in
      `templates/` or `.claude/skills/`.
- [x] Tests cover block parsing, `link` (append, second id, idempotent,
      out-of-range) and byte-preservation.
- [x] Docs updated: README, `tickets/README.md`, scaffold + cli docstrings.

## Note on landing

The id rules are the **repo's**, not ours: `Repo` now compiles the "id as it
appears in prose" pattern (and the `(ID, ID)` / `→ ID, ID` tails) from the
configured prefix and width, alongside the id regexes it already owned. A first
pass invented a generic `TICKET_ID_RE` in `server.py` — which silently shadowed
the config-driven one already there, and would have matched ids from any scheme.
The tests caught it. Nothing in capture hardcodes an id shape.

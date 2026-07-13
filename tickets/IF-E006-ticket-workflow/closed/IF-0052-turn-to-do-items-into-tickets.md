---
id: IF-0052
title: Turn to-do items into tickets with an agent
epic: IF-E006
status: CLOSED
risk: LOW
priority: 2
effort: 4h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
index_note: interfacile todo (list/done, --json, --ticket) + the todo-ticket skill that drives it
---

# IF-0052 · Turn to-do items into tickets with an agent

## Context

The to-do pop-out is where a thought lands the moment it arrives — one line, no
ceremony. Promoting one of those lines into a real ticket is entirely manual
today: read the list, retype the item as a title, invent the context, then go
back and tick the box. So the list grows and the board doesn't, and the two
drift apart.

An agent can do that promotion, but only if the to-do list has a machine
contract. Right now the file is written by the dashboard's JS and by nothing
else; an agent editing it by hand would have to reimplement `parseTodo` /
`serializeTodo` and could still clobber whatever the open browser tab flushes a
second later.

## Approach

Two halves, CLI first so the skill has something safe to call.

**1. `interfacile todo` — the CLI owns the file.** A `todo` command alongside
the rest of the ticket flow:

    interfacile todo [--all] [--json]        list items; open only by default
    interfacile todo done N [--ticket ID]    check item N off

Items get a stable 1-based index over the whole file (open *and* done), so `N`
means the same thing whichever view you asked for. The parse/serialize pair
lives next to `load_note`/`save_note` in `server.py` — one source of truth for
the format the dashboard already reads and writes — and every write goes
through the same atomic tmp-then-replace as the other state files.
`--ticket ID` appends `(ID)` to the item text, so a ticketed to-do links back to
the ticket it became.

**2. `todo-ticket` skill.** Ships in `templates/skills/`, so `interfacile
skills` installs it in every repo like the other four. It lists the open items,
takes the one you pick, asks clarifying questions, then hands the answers to
`new-ticket` for the drafting and `interfacile new` — no duplicate ticket-writing
logic. On approval it creates the ticket and marks the to-do done with the new
id. One item per run, then it offers the next.

Marking done is the default; `--keep` (or just saying so) leaves the box
unticked.

**Open question:** the scratchpad is the other half of the original to-do line
("create tickets from scratch pad"). It's free-form prose, so finding the
actionable bits is a different problem — its own ticket, once this one is proven.

## Acceptance criteria

- [x] `interfacile todo` lists open items with stable indices; `--all` includes
      done ones; `--json` emits `n`/`done`/`text` per item.
- [x] `interfacile todo done N` ticks item N in `.interfacile/todo.md`, leaves
      every other line byte-identical, and is a no-op (exit 0) if already done.
- [x] `interfacile todo done N --ticket IF-0001` appends `(IF-0001)` to the item,
      and doesn't double-append if run twice.
- [x] Out-of-range `N` exits non-zero with a useful message.
- [x] Round-trip: a file the dashboard wrote, read and rewritten by the CLI, is
      unchanged except for the item asked for.
- [x] `todo-ticket` skill exists in `templates/skills/`, is installed by
      `interfacile skills`, and delegates drafting to `new-ticket`.
- [x] Tests cover the parse/serialize round-trip, `done` (plain, `--ticket`,
      idempotent, out-of-range), and that scaffold installs the new skill.
- [x] Docs updated: package docstring, `tickets/README.md` skill list, README
      feature bullet.

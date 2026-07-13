# How tickets work here

Tickets are markdown files with YAML frontmatter, organised by epic:

    tickets/
      {{PREFIX}}-E001-some-epic/
        {{PREFIX}}-E001-some-epic.md        <- the epic charter
        open/
          {{PREFIX}}-0001-do-the-thing.md
        closed/
          {{PREFIX}}-0002-already-done.md

Everything is driven by the `interfacile` CLI — no loose scripts. Run
`interfacile` (no arguments) in this repo to see the live board.

## The flow

| Step                        | Command                                                |
|-----------------------------|--------------------------------------------------------|
| See the board               | `interfacile tickets` (`--all` includes closed)        |
| See what can start now      | `interfacile ready`                                    |
| Create a ticket             | `interfacile new "Title" --epic E001`                  |
| Read one ticket             | `interfacile show {{PREFIX}}-0001`                     |
| Check its dependencies      | `interfacile deps {{PREFIX}}-0001`                     |
| Finish one                  | `interfacile close {{PREFIX}}-0001 --note "what shipped"` |
| Keep the tree honest        | `interfacile lint`                                     |
| Add an epic                 | `interfacile epics`                                    |

## A ticket's frontmatter

    ---
    id: {{PREFIX}}-0001
    title: Short imperative title
    epic: {{PREFIX}}-E001
    status: OPEN                    # OPEN | CLOSED | WONT_FIX | STANDING
    risk: LOW                       # LOW | MEDIUM | HIGH
    priority: 3                     # 1 (do first) .. 5 (someday)
    effort: 2h                      # 30m 1h 2h 4h 1d 2d 1w ...
    created: 2026-01-01
    depends_on: [{{PREFIX}}-0002]   # optional; only real ticket ids
    ---

The body carries three sections: **Context** (why), **Approach** (how), and
**Acceptance criteria** (checkboxes that define "done").

## Three rules

1. **A ticket's real state is its `status:` field.** The `open/` and `closed/`
   folders are for humans browsing the tree; `interfacile close` keeps them in
   step so you never have to.
2. **"Blocked" is never a status — it is derived.** A ticket is blocked while
   anything in its `depends_on` is still open, and unblocks by itself the
   moment the dependency closes.
3. **`interfacile lint` is the referee.** It validates ids, required fields,
   statuses, dates, and the dependency graph. Run it after any hand edit.

Agent skills for this flow live in `.claude/skills/` (`new-ticket`,
`work-ticket`, `close-ticket`, `ticket-status`, and `capture-ticket` — which
turns a line from the to-do pop-out, or a block from the scratchpad, into a
ticket, and points the note at what it became). Reinstall or update them any
time with `interfacile skills`.

---
name: capture-ticket
description: Turn a captured note into a proper ticket. Use when the user says "ticket my to-dos", "create a ticket from the to-do list or scratchpad", "triage my notes", or points at an item in either pop-out. Picks one item, asks what's missing, drafts it via new-ticket, then points the note at the ticket it became.
---

# Capture → ticket

The to-do list and the scratchpad are where thoughts land — a line, a paragraph,
no ceremony. This skill promotes one of them into a ticket with context, an
approach, and acceptance criteria, then leaves a pointer behind so the note and
the board stop drifting apart.

Two sources, one flow:

| source | what a captured thing is | how it's read | how it's marked |
|---|---|---|---|
| to-do (`📌`) | a checkbox line | `interfacile todo --json` | `interfacile todo done N --ticket ID` → `(ID)` |
| scratchpad (`📝`) | a block: a run of non-blank lines | `interfacile scratch --json` | `interfacile scratch link N --ticket ID` → `→ ID` |

The `interfacile` CLI owns both files. The pop-outs are probably open in a
browser tab writing to the same files, so **never edit them by hand** — every
read and write goes through the commands above.

## Steps

1. **Show what's captured.** Unless the user named a source, read both
   (`interfacile todo --json`, `interfacile scratch --json`). Items already
   carrying a ticket id have been done — say what they became if it's relevant,
   but don't offer them again.
2. **Pick one.** One item per run. If the user already said which they mean,
   match it and confirm in one line rather than making them pick twice.
3. **Read it honestly.** A captured note is a reminder, not a spec — the context
   is in the user's head. Before drafting, decide which it is:
   - **Work** → carry on.
   - **Several pieces of work** in one note → say so, propose the split, and let
     the user confirm before you create anything.
   - **Not work** (a thought, a link, a maybe-one-day) → say so and stop. Not
     everything captured wants to be a ticket.
4. **Draft it.** Hand over to the **`new-ticket`** skill from its intake step: it
   asks the questions (epic, why, rough how, acceptance criteria, dependencies),
   writes the body, waits for approval, and runs `interfacile new`. Don't
   duplicate that logic here. The note is the seed for the title, not the title
   itself — "add option to update status to blocked" becomes *Allow a ticket to
   be set to blocked*. A scratchpad block is usually long enough to quote into
   the ticket's Context; a to-do line rarely is.
5. **Point the note at its ticket** — the default, unless the user asked to leave
   it alone. Re-read the source (`--json`) first: the numbers move if a browser
   tab edited the file while you were drafting, so match the note by its **text**
   and use the number you just read, not the one from step 1.

       interfacile todo done <N> --ticket <ID>      # to-do
       interfacile scratch link <N> --ticket <ID>   # scratchpad

   Split one note into several tickets? Run it once per id — the ids accumulate
   (`→ IF-0053, IF-0054`) and nothing is double-appended. In the dashboard those
   ids are live links carrying the ticket's current status, which is the whole
   point: the note becomes a way to follow the work.
6. **Offer the next one.** Report the ticket id and path, then name the next
   uncaptured note and ask whether to keep going.

## Rules

- Never write `.interfacile/todo.md` or `.interfacile/scratchpad.md` yourself —
  `interfacile todo done` / `interfacile scratch link` only. They annotate; they
  never delete what the user wrote.
- Never mark a note before its ticket exists. If `interfacile new` fails, the
  note stays uncaptured.
- Don't invent requirements to fill out a thin note. Ask; and if something stays
  unresolved, record it as `**Open question:** ...` in the ticket's Approach
  rather than guessing.
- Creating the ticket is the whole job — implementing it is `work-ticket`.

---
name: abandon
description: Clean exit from a ticket that is mid-implementation. Leaves the ticket open, handles uncommitted changes per the user's choice (discard/keep/stash), optionally appends an Abandon Note, and halts cleanly. Use when the user says "abandon EM-XXXX", "drop this ticket", "stop working on EM-XXXX", or needs to exit mid-implementation without closing the ticket.
---

# Abandon Ticket

`abandon` is a clean mid-implementation exit. It does **not** close the ticket — the ticket stays in `open/` and can be resumed later. What it does is handle uncommitted work and leave a record of why work stopped.

## Core principle

**Abandon is not failure. Abandon is discipline.**

Sometimes a ticket reveals scope issues, blockers, or changed priorities mid-implementation. Stopping cleanly — with a note and a clear working tree — is better than leaving half-done changes in place. The ticket stays open and can be resumed with `/fix EM-XXXX` or re-scoped with `/scope EM-XXXX`.

## Quick start

1. **Ticket ID is mandatory.** The argument must be a ticket ID (e.g. `abandon EM-2008`). If missing, ask and stop.
2. Find the ticket in `open/`. If it's closed, say so and halt.
3. Show the user what's in the working tree for this ticket.
4. Ask two questions (changes disposition, leave a note?) and HALT.
5. Apply the user's choices.
6. Offer to remove any `## Pre-Work Staff Review` block if one was added this session.
7. Run lint.
8. Report and halt.

## Workflow steps

### Step 1 — Validate the ticket ID

The argument must match the `EM-NNNN` pattern (or a bare number that normalizes to it). If missing or invalid, output:

```
Ticket ID is required. Example: /abandon EM-2008
```

**HALT.**

### Step 2 — Locate the ticket

Run:

```bash
python scripts/ticket_hygiene/ticket.py find EM-XXXX
```

If the ticket is in `closed/`, output:

```
EM-XXXX is already closed. Only open tickets can be abandoned.
```

**HALT.**

If the ticket is not found, output:

```
EM-XXXX not found. Check the ticket ID and try again.
```

**HALT.**

### Step 3 — Show working tree context

Read the ticket's **Files Touched** section to get the list of affected files. Then run:

```bash
git diff HEAD -- <files from Files Touched>
git status
```

Display a concise summary of what changed:

```
Working tree for EM-XXXX:
  Modified: <list of modified files from Files Touched>
  Untracked: <list of new files related to the ticket>
  Uncommitted changes: <yes/no — N lines changed>
```

If there are no changes at all (working tree is clean for these files), note it:

```
No uncommitted changes found for EM-XXXX files.
```

### Step 4 — Ask two questions

Output exactly (substitute the real ticket ID):

```
Abandoning EM-XXXX. Two questions:

1. What to do with uncommitted changes?
   a) Discard them — git checkout HEAD -- <affected files>
   b) Keep them in the working tree (leave as-is)
   c) Stash them — git stash push -m "EM-XXXX abandoned work"

2. Leave a note in the ticket?
   a) Yes — append an ## Abandon Note with reason
   b) No — leave ticket file unchanged
```

**HALT.** Wait for the user to answer both questions. If only one is answered, ask for the other.

### Step 5 — Apply uncommitted-changes choice

**Choice 1a — Discard:**

Only revert files listed in the ticket's Files Touched section. Do not touch unrelated files.

```bash
git checkout HEAD -- <file1> <file2> ...
```

If a Files Touched file has never been committed (new file), use `git clean -fd <path>` to remove it, not `git checkout`.

**Choice 1b — Keep:**

Do nothing. Leave the working tree as-is. Note it in the output:

```
Uncommitted changes left in working tree.
```

**Choice 1c — Stash:**

```bash
git stash push -m "EM-XXXX abandoned work"
```

Note the stash reference in the output.

### Step 6 — Apply note choice

**Choice 2a — Leave a note:**

Ask for the reason (one question, one HALT):

```
Reason for abandoning EM-XXXX? (one line)
```

**HALT.** Wait for the reason, then append to the ticket file:

```markdown
## Abandon Note

**Abandoned:** <today's date YYYY-MM-DD>
**Reason:** <user's reason>
**Changes:** <one of: discarded / kept in working tree / stashed as "EM-XXXX abandoned work">
```

**Choice 2b — No note:**

Leave the ticket file unchanged.

### Step 7 — Offer to remove Pre-Work Staff Review

If the ticket file contains a `## Pre-Work Staff Review` block, output:

```
The ticket has a ## Pre-Work Staff Review block. Remove it? (y/n)
```

**HALT.** Wait for the user's answer.

- If **yes**: remove the `## Pre-Work Staff Review` block (the section header and all content up to the next `##` or end of file).
- If **no**: leave it in place.

If there is no `## Pre-Work Staff Review` block, skip this step entirely.

### Step 8 — Run lint

```bash
python scripts/ticket_hygiene/ticket.py lint
```

If lint reports errors for this ticket, fix them before halting. If lint reports errors for other tickets, note them but do not fix them here.

### Step 9 — Report and halt

Output exactly:

```
EM-XXXX abandoned. Ticket remains open.
Resume with /fix EM-XXXX or /scope EM-XXXX when ready.
```

**HALT.**

---

## Non-negotiable rules

- **Ticket ID is mandatory.** Do not proceed without it.
- **Only abandon open tickets.** Closed tickets cannot be abandoned.
- **Never commit during abandon.** Never close the ticket.
- **Discard choice (1a) only reverts files listed in Files Touched.** Do not touch unrelated files.
- **Always run lint before halting.**
- **The Pre-Work Staff Review removal offer is only made if the block is present.** Do not add or manufacture it.

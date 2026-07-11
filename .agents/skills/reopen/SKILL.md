---
name: reopen
description: Formally reopen a closed CleanPaste Lite ticket that was closed prematurely or in error. Moves the ticket from closed/ to open/, restores its status in frontmatter, records a reason, and appends a Reopen Note. Use when the user says "reopen EM-XXXX", "re-open this ticket", or needs to restore a closed ticket to active work.
---

# Reopen Ticket

`reopen` moves a closed ticket back to `open/` status. It does not re-scope or re-implement the ticket — those are separate steps. It records the reason for reopening and updates the ticket's frontmatter and body.

## Quick start

1. **Ticket ID is mandatory.** The argument must be a ticket ID (e.g. `reopen EM-1677`). If missing, ask and stop.
2. Locate the ticket with the hygiene tool.
3. Verify the ticket is in `closed/`. If already open, say so and halt.
4. Ask the user for a reason before doing anything.
5. Move the file, update frontmatter, append the Reopen Note.
6. Run lint. Fix any errors.
7. Report and halt.

## Workflow steps

### Step 1 — Validate the ticket ID

The argument must match the `EM-NNNN` pattern (or a bare number that normalizes to it). If the argument is missing or does not match, output:

```
Ticket ID is required. Example: /reopen EM-1677
```

Stop. Do not proceed.

### Step 2 — Locate the ticket

Run:

```bash
python scripts/ticket_hygiene/ticket.py find EM-XXXX
```

Read the output to get the full path to the ticket file.

### Step 3 — Verify the ticket is closed

Check that the path contains a `closed/` directory segment. If the ticket is in `open/`, output:

```
EM-XXXX is already open at tickets/<epic>/open/<filename>.
Nothing to do.
```

**HALT.**

If the ticket cannot be found at all, output:

```
EM-XXXX not found. Check the ticket ID and try again.
```

**HALT.**

### Step 4 — Ask for a reason

Output exactly:

```
Reason for reopening EM-XXXX?
```

**HALT.** Wait for the user's answer (one line is sufficient). Do not proceed without a reason.

### Step 5 — Move the ticket file

Move the ticket from `closed/` to `open/`:

```bash
mv tickets/<epic>/closed/<filename> tickets/<epic>/open/<filename>
```

The filename does not change. Only the directory changes.

### Step 6 — Update frontmatter

In the moved file, make the following changes to the YAML frontmatter block (between the `---` delimiters):

- Set `status: OPEN`
- Remove the `closed:` field (or set it to empty string — prefer removal)
- Remove the `closed_reason:` field (or set to empty — prefer removal)
- Remove the `closed_note:` field (or set to empty — prefer removal)

If any of those fields are absent, skip the removal for that field. Do not add fields that were not present.

### Step 7 — Append the Reopen Note

Append the following section to the end of the ticket file body (after all existing content):

```markdown
## Reopen Note

**Reopened:** <today's date YYYY-MM-DD>
**Reason:** <user's answer from Step 4>
```

Substitute the real date and the user's exact answer.

### Step 8 — Run lint

```bash
python scripts/ticket_hygiene/ticket.py lint
```

If lint reports errors for this ticket, fix them before halting. If lint reports errors for other tickets unrelated to this reopen, note them but do not fix them here.

### Step 9 — Report and halt

Output exactly:

```
EM-XXXX reopened → tickets/<epic>/open/<filename>
Run /scope EM-XXXX to reassess before implementing.
```

**HALT.**

---

## Non-negotiable rules

- **Ticket ID is mandatory.** Do not proceed without it.
- **Only reopen tickets from `closed/`.** Do not touch tickets that are already in `open/`.
- **Always ask for a reason before moving the file.** Do not skip the reason HALT.
- **Lint must pass for the reopened ticket after reopening.** Fix any lint errors introduced by the reopen before halting.
- **Do not auto-run `/scope` or `/fix` after reopening.** Output the reminder to run `/scope` but do not invoke it.
- **Do not commit during reopen.** The ticket file change is left uncommitted for the user to review.

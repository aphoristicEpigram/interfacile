---
name: ceremony
description: Run the ticket closure ceremony. Posts the retro, halts for explicit approval, closes the ticket with the hygiene tool, and finalizes according to the chosen commit mode (work-only, all, or no commit). Normally invoked by `/close` when the user chooses "commit and close now"; can also be invoked directly for a bare retro/close without A+ review or extra work.
---

# Ticket Closure Ceremony

Run the standard ticket closure ceremony. This skill covers posting the retro, halting for approval, closing the ticket, and committing (or skipping the commit). It does **not** write the retro, perform A+ review, complete "If I Had 3 More Hours" items, or create follow-up tickets — those are the responsibilities of `/fix`, `/fix-now`, and `/close`.

When invoked by `/close`, the commit mode may or may not already be known. If a mode was handed off, apply it exactly. If the mode is unknown, present the commit-mode menu and HALT for the user to choose before closing or committing.

## Core principle

**The ceremony is not optional.** A retro nobody reads is dead text, and a ticket nobody closes is still open. Every closure must be explicit, approved, and recorded.

## Quick start

1. **The ticket ID is mandatory.** The user's first message must include it
   (e.g. `ceremony EM-1677`, `close EM-1677`, or `EM-1677`). If it is missing,
   ask for it and stop until you receive it.
2. Verify the ticket file is in `tickets/<epic>/open/` and contains a
   `## Retro` section with `### If I Had 3 More Hours`.
3. Determine the **commit mode** in effect:
   - If `/close` handed off a mode, use it.
   - Otherwise, present the mode menu and **HALT** until the user chooses **work-only**, **all**, or **no commit**.

   The three modes are:
   - **work-only** — stage only files touched by this ticket (`/commit EM-XXXX`).
   - **all** — stage the entire working tree (`/commit-all`).
   - **no commit** — close the ticket but do not commit.
4. Post the entire retro in chat and **HALT** for explicit approval. If the retro has been updated since it was last posted (e.g., after bonus work or a Quality Closure note), post the entire updated retro again. The final retro must be visible in the chat history immediately before the close/commit.
5. After approval, close the ticket with the hygiene tool.
6. Run the forward-impact sweep: find open tickets that share touched files with the one just closed, and fix stale references or leave a pointer note on each.
7. If commit mode is not "no commit", commit according to the chosen mode (including any tickets updated by the sweep).
8. Perform the final halt.

## Step 1 — Pre-flight checks

Find the ticket under `tickets/<epic>/open/` by searching for the ID in the
filename. Read it in full.

Verify:
- [ ] Ticket status is `OPEN` and the file is in `open/`.
- [ ] Ticket contains a `## Retro` section.
- [ ] Retro contains `### If I Had 3 More Hours` with at least two bullets.
- [ ] No open `depends_on` remain (check frontmatter and the hygiene tool).

If any check fails, stop and report.

## Step 2 — Post the retro and HALT

Post the entire `## Retro` section from the ticket file in chat, including any appended `## BONUS` section, then output:

```
EM-XXXX retro posted. Awaiting approval to commit.
```

If the retro has been modified since it was last posted in this conversation
(e.g., bonus items were completed or a Quality Closure note was added), post
the entire updated retro again. Do not rely on an earlier version.

**HALT.** Do not stage, commit, or move the ticket file. Wait for explicit
approval ("proceed", "go ahead", "yes", "ok", "lgtm", etc.).

## Step 3 — Commit mode selection (if not already set)

If `/close` passed in a commit mode (work-only / all / no commit), use it and skip this step.

Otherwise, present this menu **exactly as written** and **HALT**:

```
Choose a commit mode for EM-XXXX:

  work-only — commit only the files touched by this ticket
  all       — commit the entire working tree
  no-commit — close the ticket without committing

Which mode? (work-only / all / no-commit)
```

Wait for the user to reply with one of the three exact keywords (accept common variants like "work only", "all", "none", or "no"). Record the chosen mode before proceeding.

## Step 4 — Close the ticket (after approval)

Dry-run first:

```bash
python scripts/ticket_hygiene/ticket.py close EM-XXXX --dry-run
```

Review the output. If correct:

```bash
python scripts/ticket_hygiene/ticket.py close EM-XXXX
```

If the tool warns about a missing retro or open dependencies, **do not use
`--force` at all.** Stop, report the issue, and fix the underlying problem
(write the missing retro, close/resolve the open dependency, etc.), then
re-run the dry-run. Only re-attempt the real close once it passes cleanly
without `--force`.

## Step 5 — Forward-impact sweep

Run this regardless of commit mode — it happens right after the ticket
moves to `closed/`, before any commit decision takes effect.

```bash
python scripts/ticket_hygiene/ticket.py impact EM-XXXX
```

This lists open tickets that share files with EM-XXXX's **Files Touched**.
`depends_on`/`blocks` cross-references are already handled automatically by
`ticket.py close` in Step 4 — this step is about tickets that share code but
aren't formally linked, which the hygiene tool can't catch on its own.

For each ticket the sweep returns:

1. Open it and check whether anything it says about the shared file(s) is
   now stale because of what EM-XXXX just changed — a path that moved, a
   function/class renamed or removed, an assumption the closed ticket
   invalidated.
2. If something is stale, fix the reference directly (update the path or
   name in place).
3. Otherwise, leave a one-line pointer so whoever picks up that ticket next
   knows to check compatibility:
   ```markdown
   > Related: EM-XXXX (closed <date>) touched `<shared/path>` — <one-line relevance note>.
   ```
   Place it at the top of `## Context`, or wherever the ticket already keeps
   cross-references.

If the sweep returns no tickets, note "No forward-affected tickets found"
and continue — this is not a failure and does not block closure.

Any ticket files edited in this step must be staged and committed alongside
EM-XXXX in Step 6 (see the work-only note there).

## Step 6 — Commit according to the chosen mode

### work-only

Stage the files created or modified for this ticket (the ticket file plus files
from the ticket's **Files Touched** section):

```bash
python3 .agents/skills/commit/scripts/commit.py EM-XXXX
```

If Step 5's forward-impact sweep edited any other ticket files, stage those
explicitly too — the helper above only knows about EM-XXXX's own scope:

```bash
git add tickets/<epic>/open/EM-YYYY*.md   # one per ticket the sweep touched
```

Then commit. The commit message must contain both `Ceremony:` and `Approved:`
markers.

```bash
git commit -m "$(cat <<'EOF'
EM-XXXX: <one-line description>

Ceremony: <2-3 sentence retro summary>

Approved: <quote the exact phrase the user used to approve the retro>
EOF
)"
```

### all

Stage the entire working tree and commit (no-arg defaults to commit-all):

```bash
python3 .agents/skills/commit/scripts/commit.py
```

Use the same commit message template as above.

### no commit

Skip this step. Output:

```
EM-XXXX closed. No commit made.
```

## Step 7 — Final HALT

If a commit was made and succeeded, output:

```
EM-XXXX CLOSED. Ceremony complete. Awaiting instructions.
```

If no commit was made, output:

```
EM-XXXX closed. No commit made. Awaiting instructions.
```

You may run `python scripts/ticket_hygiene/show_path.py --ready` and list newly
unblocked tickets — one line each, no commentary.

**HALT.** Do not open the next ticket. Do not start refactoring. Do not
generate follow-up work.

## Non-negotiable rules

- The ticket ID is mandatory. Do not proceed without it.
- Do not skip the retro-posting halt.
- The entire retro (including any `## BONUS` section) must be posted in chat
  immediately before the final close/commit. If the retro has been modified
  after it was last posted, it must be reposted.
- Do not commit without explicit human approval.
- Do not skip the forward-impact sweep (Step 5), even in no-commit mode. If
  `ticket.py impact` returns nothing, say so explicitly rather than omitting
  the step.
- **Never use `ticket.py close --force`.** If the close command warns about
  anything, fix the actual issue and re-run the dry-run — do not force past it.
- Do not use `--no-verify` without reporting the issue first.
- Every **HALT** is real — do not proceed without explicit human approval.

---
id: EM-1146
title: "Ticket Closure Ceremony — Step-by-Step Checklist"
status: STANDING
risk: LOW
effort: N/A
depends_on: []
blocks: []
created: 2026-06-03
---

# EM-1146 — Ticket Closure Ceremony

**Epic:** EM-E020
**Type:** Standing process ticket
**Status:** STANDING — permanent reference, updated as process evolves

---

## When to Use This

Every time a ticket is closed — whether as planned work, side-effect closure, superseding, or "won't fix."

---

## Step 1 — Verify the Work

- [ ] Read the ticket's acceptance criteria.
- [ ] Check the codebase for the described work. **Do not trust the ticket — trust the code.**
- [ ] If any criterion is unmet, leave the ticket open and add a comment.
- [ ] Run the test suite. It must pass.
- [ ] Run `mypy`. It must be clean.
- [ ] If the ticket has `test_em{N}_*.py` files, classify each one:
  - **Graduate** to the appropriate category directory and drop the EM prefix.
  - **Move to `tests/regression/`** as a permanent anchor (keep the EM prefix).
  - **Delete** if it is implementation scaffolding now covered elsewhere.
  See `docs/guides/tests.md` for the full graduation policy.

> **Rule:** No ticket is closed on assumption. Every closure has a code reference noted.

---

## Step 2 — Write the Retro

Append a retro section to the ticket file (or create one if it doesn't exist):

```markdown
## Retro

**Closed:** YYYY-MM-DD
**Actual effort:** Xh
**Tickets closed:** N
**Test count at close:** X passed, Y skipped, Z xfailed

### Design Decisions
- What architectural or process decisions were made?
- Why were they made?

### What Went Well
- 3–5 bullet points.

### What Didn't Go Well
- 3–5 bullet points. Be honest.

### Issues
- Bugs found, friction points, surprises.

### Future Items
- Ideas that surfaced but were out of scope.

### If I Had 3 More Hours
- **NON-NEGOTIABLE.** Every retro must include this section.
- What would you do with more time? What was left unfinished? What would you tighten?
- This is where the real learning lives. Skip it and the retro is incomplete.

### Would I Inherit This Code?
- Yes / No / Conditional, with reasoning.
```

**Then post the entire retro in chat.** The chat message is the ceremony. The file append is the record. Both are required — a retro in the file that was never read by another human is dead text.

---

## Step 2b — Await Approval Before Committing

**This is a hard stop. Do not skip.**

After posting the retro in chat, output:

```
Retro posted for EM-XXXX. File updated with retro; ticket is still in open/.
Ready to close and commit. Awaiting approval to proceed.
```

**Do not run `git add` or `git commit` until the user explicitly approves.**

Why? The retro is a checkpoint. The user may want to:
- Add something to the retro
- Correct a fact
- Decide not to close the ticket after all
- Ask a follow-up question

Committing without approval destroys the checkpoint.

**If you commit without approval, you failed the ceremony.**

---

## Step 3 — Close the Ticket

After approval, run the atomic close command:

```bash
python scripts/ticket_hygiene/ticket.py close EM-XXXX
```

This command:
- Updates frontmatter: `status: CLOSED`, `closed: YYYY-MM-DD`
- Moves the file from `open/` to `closed/` **within its own epic**
- Updates forward-affected tickets (removes the closed ID from `depends_on` / `blocks`, appends a closure note)
- Regenerates `TICKET_INDEX.md`
- Runs the ticket hygiene audit and reports the result

Always review the dry-run output first if anything looks unusual:

```bash
python scripts/ticket_hygiene/ticket.py close EM-XXXX --dry-run
```

**Rules:**
- **Never** create new closed epic folders. Closed epics are locked.
- If the tool refuses because the retro is missing, do not override with `--force` unless there is an explicit reason.
- If the tool warns about OPEN dependencies, review them before overriding with `--force`.

The older manual steps (`close-cleanup`, hand-editing frontmatter, moving the file, and regenerating the index) are still available but are no longer the canonical path.

---

## Step 4 — Commit

The commit message **must** contain BOTH:
- `Ceremony:` followed by the retro summary
- `Approved:` proving human approval was given

The **`commit-msg` hook** blocks any commit touching `closed/` that lacks either marker.

```bash
git add -A
git commit -m "EM-XXXX: Brief description

Ceremony: Full retro summary here. Design decisions, what went well,
what didn't go well, issues, future items, inheritability assessment.

Approved: <who approved>

- What changed
- Why
- Test results"
```

**If the commit is blocked:** The hook detected `closed/` files without `Ceremony:` or `Approved:`.
You skipped a step. Go back. Post the retro in chat. Await approval. Retry.

**Hook installation:**
```bash
cp scripts/git-hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
cp scripts/git-hooks/commit-msg .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg
cp scripts/git-hooks/prepare-commit-msg .git/hooks/prepare-commit-msg && chmod +x .git/hooks/prepare-commit-msg
```

---

## Step 5 — Halt and Await Instructions

**This step is non-negotiable.**

After the commit succeeds, **STOP**. Do not open the next ticket. Do not start the next task. Do not "helpfully" continue working.

The ceremony is a deliberate boundary. Crossing it without explicit human instruction destroys the checkpoint.

**What to output:**
```
EM-XXXX CLOSED. Ceremony complete. Awaiting instructions.
```

**What NOT to do:**
- Do not begin EM-XXXX+1
- Do not start refactoring "while I'm here"
- Do not generate follow-up work unless explicitly asked
- Do not summarize and then keep going

---

## Common Mistakes

| Mistake | Why It Happens | How to Avoid |
|---|---|---|
| Closing on assumption | Ticket says it's done, nobody checks code | Step 1 — always verify |
| Creating new closed epic folders | Don't know the convention | Step 3 — the tool moves the file within the same epic |
| Forgetting to update TICKET_INDEX.md | It's a separate file, easy to miss | Step 3 — the command regenerates the index automatically |
| No code line references in retro | Haste | Step 2 — require line refs |

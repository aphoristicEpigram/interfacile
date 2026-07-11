---
name: approve
description: Complete the "If I Had 3 More Hours" follow-up items from a ticket's retro after the user has approved doing them. Runs a final A+ review, implements every 3-hours bullet, updates the retro, and commits (or skips commit) based on user choice. Use when the user says "approve the 3 More Hours for EM-XXXX", "do the follow-ups for EM-XXXX", "complete the If I Had 3 More Hours items", or similar. Requires a ticket ID as the first argument.
---

# Approve Follow-ups

Complete the **"If I Had 3 More Hours"** items from a ticket's retro after the
user has approved doing them. This skill runs a final A+ review, implements
every bullet, updates the retro, and commits according to the user's chosen mode.

## Core principle

**Scope creep is not the enemy. Mediocrity is.**

The retro's "If I Had 3 More Hours" section captures work the author believed
would make the ticket A+. Do it now. Do not settle for "good enough."

## Quick start

1. **The ticket ID is mandatory.** The user's first message must include it
   (e.g. `approve EM-1677` or `EM-1677`). If it is missing, ask for it and stop
   until you receive it.
2. Run the pre-flight helper:
   ```bash
   python3 .agents/skills/approve/scripts/approve_ticket.py EM-XXXX
   ```
3. Read every file in **Files Touched** and perform the A+ review.
4. Fix any polish issues and test gaps.
5. Complete every "If I Had 3 More Hours" bullet.
6. Update the retro with a **Follow-up Work Completed** note.
7. Ask for commit mode and commit (or skip).
8. Halt.

## Step 1 — Pre-flight checks

Find the ticket file in `tickets/<epic>/open/`. It must be in `open/`.

Verify:
- [ ] Ticket is in `open/` (not already closed)
- [ ] Ticket contains a `## Retro` section
- [ ] Retro contains `### If I Had 3 More Hours`

If any check fails, stop and report.

Use the helper script to verify these automatically.

## Step 2 — A+ code review

Read every file listed in **Files Touched**. Produce the review block and apply fixes per [references/a-plus-review.md](../../references/a-plus-review.md) (heading variant: `## A+ Review — EM-XXXX follow-ups`).

Run the full PR gate before proceeding (see [references/pr-gate.md](../../references/pr-gate.md)).

## Step 3 — Complete "If I Had 3 More Hours"

Read the `### If I Had 3 More Hours` section. Do **every single bullet** now.

For each item:
1. Read it.
2. Implement the most concrete useful interpretation.
3. Run `pytest tests/ -q` after each item — fix regressions before continuing.
4. Append `(✓ done)` to the bullet.

Do not skip vague items — interpret them concretely and implement.

After all items, run the full PR gate again (see [references/pr-gate.md](../../references/pr-gate.md)).

## Step 4 — Update the retro

Append a **Follow-up Work Completed** note to the retro:

```markdown
### Follow-up Work Completed

**A+ review:** <verdict — e.g. "A+ after polish" / "A+ no changes needed">
**Review fixes applied:** <list what was fixed, or "None">
**"3 More Hours" items completed:** <N of N>
**Final test count:** <X passed, Y skipped, Z xfailed>
```

## Step 5 — Ask for commit mode and commit

Ask for commit mode — once, after all work is complete:

```
Follow-up work for EM-XXXX is done. How should I commit?

  a) Work only — stage and commit only the files touched by this follow-up work
  b) All — stage and commit everything for a clean working tree
  c) No commit — do the work but don't commit
```

**HALT.** Wait for an explicit answer.

If the chosen mode is not **c) No commit**, proceed to commit using `/commit` or `/commit-all`.

### Mode a — work only

```bash
python3 .agents/skills/commit/scripts/commit.py EM-XXXX
```

### Mode b — all (clean tree)

```bash
python3 .agents/skills/commit/scripts/commit.py
```

### Mode c — no commit

Skip this step. Output:

```
EM-XXXX follow-ups completed. No commit made.
```

### Commit message (modes a and b)

The pre-commit hook requires both `Ceremony:` and `Approved:` markers.

```bash
git commit -m "$(cat <<'EOF'
EM-XXXX: complete If I Had 3 More Hours follow-ups

Ceremony: Completed <N> "If I Had 3 More Hours" items for EM-XXXX. <one sentence summary of what was done.>

Approved: <quote the exact phrase the user used to approve doing this follow-up work>

- A+ review: <verdict>
- 3 More Hours: <N items completed>
- Final tests: <X passed>
EOF
)"
```

## Step 6 — HALT

Output exactly:

```
EM-XXXX follow-ups completed. Awaiting instructions.
```

**HALT.** Do not close the ticket. Do not open the next ticket. Do not start
refactoring. Do not generate follow-up work unless the user asks for it.

## Non-negotiable rules

- The ticket ID is mandatory. Do not proceed without it.
- Do not skip pre-flight checks.
- Do not skip "If I Had 3 More Hours" items.
- **Never use `ticket.py close --force`.** Fix the actual issue before closing — never force past it.
- Do not use `--no-verify` without reporting the issue first.
- Every fix must be accompanied by a green PR gate before continuing.
- **Do not close the ticket.** This skill completes follow-ups; use `/close` to close.

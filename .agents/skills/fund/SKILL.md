---
name: fund
description: Do additional work that has been funded after a ticket was written or closed — typically items from "If I Had 3 More Hours" or "Future Items". Implements the funded items, verifies them, updates the ticket (BONUS section if already closed, retro append if open), then hands off to /close (open tickets) or asks for a commit directly (closed tickets). Use when the user says "fund the 3 More Hours for EM-XXXX", "do the bonus work", "let's fund this follow-up", or similar. Requires a ticket ID as the first argument.
---

# Fund Additional Work

`fund` implements work that was intentionally deferred in a ticket's retro and is now being funded. It is the bridge between "recorded future idea" and "done."

Typical funded items:

- A bullet from `### If I Had 3 More Hours`.
- A bullet from `### Future Items`.
- Any other explicitly funded bonus work on an already-closed or still-open ticket.

`fund` does **not** decide scope — the user must tell you what is funded. It does the work properly (A+ review, implementation, verification), records the outcome, and then:

- **Open ticket**: hands off immediately to `/close` Step 0 — the full post-work decision menu lives there.
- **Closed ticket**: asks for a commit decision directly (the ticket is already closed; no ceremony needed).

## Core principle

**Scope creep is not the enemy. Mediocrity is.**

Funded work is not a shortcut. It gets the same staff-engineer treatment as the original ticket: ground-truth review, green PR gate, and clear documentation.

## Quick start

1. **The ticket ID is mandatory.** The user's first message must include it (e.g. `fund EM-1677` or `EM-1677`). If it is missing, ask for it and stop until you receive it.
2. Identify the funded items. Ask if the user did not specify.
3. Read the ticket and the affected code. Perform an A+ review.
4. Implement each funded item.
5. Run the PR gate after each meaningful change.
6. Update the ticket (BONUS section if closed; Funded Work Completed note if open).
7. Hand off per ticket state (see Step 6).

## Step 1 — Identify the funded work

If the user did not explicitly list the funded items, ask:

```
What from EM-XXXX is being funded?

  a) One or more "If I Had 3 More Hours" bullets
  b) One or more "Future Items" bullets
  c) Other explicitly described work
```

For (a) or (b), quote the exact bullets you will implement. For (c), restate the work in one sentence and ask the user to confirm.

**HALT** until the funded scope is explicit. Do not start implementation on vague funding.

## Step 2 — Read ground truth

Read:

- The ticket file (open or closed).
- Every file in **Files Touched** that relates to the funded work.
- Any new files or modules referenced by the funded items.
- `git log --oneline -20` for relevant recent changes.
- `git status` and `git diff HEAD -- <files>` for any uncommitted work that may already cover the funded items.

If the ticket is **closed**, read `docs/guides/engineering-reference.md §3.3` ("Updating a Closed Ticket") before modifying the file.

## Step 3 — A+ review

Read every relevant file. Produce the review block and apply fixes per [references/a-plus-review.md](../../references/a-plus-review.md) (heading variant: `## A+ Review — EM-XXXX funded work`).

Run the full PR gate before continuing (see [references/pr-gate.md](../../references/pr-gate.md)).

## Step 4 — Implement the funded work

Follow the same code rules as `/fix` (see [fix/references/workflow.md](../fix/references/workflow.md) §2–§3):

- **Always choose A+.** If you see a better approach than the bullet describes, stop and ask.
- **Rust-portable only.** No `__dict__` dynamic dispatch, monkey-patching, `importlib` tricks, or mutable class state.
- **Security-first.** No command injection, no plaintext secrets, no reserved test values.
- **Raise conflicts.** If the approach conflicts with existing code in an unanticipated way, stop and raise it.

After each meaningful change, run the PR gate again (see [references/pr-gate.md](../../references/pr-gate.md)).

## Step 5 — Update the ticket

### If the ticket is closed

Append a `## BONUS` section at the **bottom** of the closed ticket file, after the existing `## Retro` section. Do not rewrite or delete the original retro. Do not reopen the ticket or move it back to `open/`.

```markdown
---

## BONUS — YYYY-MM-DD

### What changed
<what was implemented>

### Why it wasn't in the original closure
<time, scope, or discovery reason>

### Verification
- pytest: <X passed>
- mypy: clean
```

Mark the funded bullet in the original retro with `(✓ done in BONUS YYYY-MM-DD)` or `(✓ ticketed as EM-YYYY)` if it became a new ticket.

### If the ticket is open

Append a `### Funded Work Completed` note to the `## Retro` section:

```markdown
### Funded Work Completed

**Items funded:** <bullets or description>
**A+ review:** <verdict>
**Review fixes applied:** <list or "None">
**Final test count:** <X passed, Y skipped, Z xfailed>
```

Mark the funded bullets with `(✓ done)`.

## Step 6 — Hand off

### If the ticket was open

Output:

```
EM-XXXX funded work complete. Handing off to /close.
```

Immediately execute `/close EM-XXXX` starting at **Step 0**. `/close` presents the full post-work decision menu (extra work? / commit & close now or move on?) and owns all remaining decisions including commit mode.

### If the ticket was closed (BONUS path)

The ticket is already closed — no ceremony needed. Ask for a commit decision:

```
EM-XXXX bonus work complete. How should I commit the BONUS changes?

  a) Work only — stage and commit only the BONUS-related files
  b) All — stage and commit everything for a clean working tree
  c) No commit — leave changes uncommitted
```

**HALT.** If the chosen mode is not **c) No commit**, proceed to commit using `/commit` or `/commit-all`.

Apply the chosen mode:

- **Mode a**: `python3 .agents/skills/commit/scripts/commit.py EM-XXXX`
- **Mode b**: `python3 .agents/skills/commit/scripts/commit.py`
- **Mode c**: skip commit.

Then output:

```
EM-XXXX bonus work complete. Awaiting instructions.
```

**HALT.**

## Non-negotiable rules

- The ticket ID is mandatory. Do not proceed without it.
- Do not begin implementation until the funded scope is explicit.
- Do not reopen a closed ticket. Use `## BONUS` only.
- Do not skip the PR gate after funded changes.
- Do not commit without explicit instruction.
- Every **HALT** is real — do not proceed without explicit human approval.

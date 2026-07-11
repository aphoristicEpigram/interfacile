---
name: fix-now
description: Implement a CleanPaste Lite ticket from start to finish as a staff engineer, assuming scope has already been approved. Use when the user says "fix-now EM-XXXX", "fix now EM-XXXX", "implement now EM-XXXX", or any variation that explicitly skips the scope gate. Requires a ticket ID as the first argument.
---

# Fix Ticket Now

Implement a CleanPaste Lite ticket end-to-end **without re-running scope**. This skill assumes the ticket already has a `## Pre-Work Staff Review` block with verdict **VALID — proceed**, or that the user has explicitly approved scope out-of-band.

Use this only when the user is certain the ticket is ready for implementation. If there is any doubt, use `/fix EM-XXXX` instead — it will run scope automatically if needed.

## Quick start

1. **The ticket ID is mandatory.** The user's first message must include it (e.g. `fix-now EM-1677` or `EM-1677`). If it is missing, ask for it and stop until you receive it.
2. Run the precondition helper:
   ```bash
   python3 .agents/skills/fix-now/scripts/fix_now_ticket.py EM-XXXX
   ```
3. If the helper reports open dependencies, halt.
4. Proceed with Steps 2–6 from the `/fix` skill — the implementation workflow is identical.

## Workflow

### Step 1 — Fast pre-review gate

Find the ticket under `tickets/<epic>/open/` by searching for the ID in the filename. Read it in full.

**Do not run scope.** Trust that scope has already been done.

Verify only:
- Ticket status is `OPEN`.
- No open `depends_on` remain.

If a `## Pre-Work Staff Review` block is present, read it for context, but do not require it.

**If open dependencies exist**, halt and report them.

### Steps 2–6 — Implement, verify, retro, hand off

Proceed with **Steps 2–6 from the `/fix` skill** ([`.agents/skills/fix/SKILL.md`](../fix/SKILL.md)).

The implementation workflow is identical to `/fix` from Step 2 onwards:
- Step 2: Read ground truth
- Step 3: Implementation plan + HALT (including the `ticket.py impact` collision check)
- Step 4: Implement (code rules, test data policy, mid-implementation PR gate)
- Step 5: Final verification
- Step 6: Write retro and hand off to `/close`

The only difference from `/fix` is Step 1: scope is skipped here.

## Non-negotiable rules

- The ticket ID is mandatory. Do not proceed without it.
- Do not run scope inside this skill. If scope is missing, redirect the user to `/fix EM-XXXX`.
- Do not commit without explicit instruction.
- Do not revert or overwrite changes you did not make — check `git diff` first.
- Do not make architectural decisions unilaterally.
- Do not choose the easier option when a better one exists.
- Every **HALT** is real — do not proceed without explicit human approval.
- At the retro/handoff step: do not present the decision menu, do not mention `/approve` or `/ceremony`. Delegate immediately to `/close` Step 0.

---
name: fix
description: Implement a CleanPaste Lite ticket from start to finish as a staff engineer. Use when asked to fix, implement, work on, or close a CleanPaste ticket; when a ticket may or may not have a staff review; or when the user says "do this ticket", "fix EM-XXXX", or "start work on EM-XXXX". If no VALID staff review exists, automatically runs /scope first, then continues with implementation. Requires a ticket ID as the first argument.
---

# Fix Ticket

Implement a CleanPaste Lite ticket end-to-end. If the ticket does not yet have a `## Pre-Work Staff Review` block with verdict **VALID — proceed**, run `/scope EM-XXXX` first, then continue with implementation.

## Quick start

1. **The ticket ID is mandatory.** The user's first message must include it (e.g. `fix EM-1677` or `EM-1677`). If it is missing, ask for it and stop until you receive it.
2. Run the precondition helper:
   ```bash
   python3 .agents/skills/fix/scripts/fix_ticket.py EM-XXXX
   ```
3. If the helper reports a missing staff review, run `/scope EM-XXXX` first (see Step 1 below), then continue.
4. If the helper reports open dependencies, halt.
5. If preconditions pass, read the generated scaffold and the actual code.
6. Post the implementation plan and **HALT** for approval.
7. After approval, implement, verify, write the retro, then immediately execute the `/close` skill from **Step 0**.

## Workflow

### Step 1 — Pre-review gate

Find the ticket under `tickets/<epic>/open/` by searching for the ID in the filename. Read it in full.

Verify it contains a `## Pre-Work Staff Review` block with verdict **VALID — proceed**.

**If present and VALID:** proceed to Step 2.

**If missing or not VALID:**

1. Output:
   ```
   ⛔ No valid staff review found for EM-XXXX.

   Running /scope EM-XXXX first, then continuing with implementation.
   ```
2. Execute the `/scope EM-XXXX` skill fully:
   - Read the ticket, epic, and dependencies.
   - Read the actual code and apply the rubric.
   - If the ticket is valid, insert a `## Pre-Work Staff Review` block.
   - If the ticket is invalid, outdated, or too large, follow the scope skill's verdict path and stop — do not continue with fix.
3. After scope completes with a VALID verdict, re-read the ticket to confirm the review block, then continue from Step 2.

**Do not halt** when the review is missing — auto-run scope instead.

### Step 2 — Read ground truth

Read the actual codebase. Trust code, not the ticket.

- Read every file in the ticket's **Files Touched** section.
- Verify every referenced class, function, and module exists in its current form.
- Run `git log --oneline -20`.
- Check `depends_on` in frontmatter — verify each is in a `closed/` directory. If any are open, stop and report.

### Step 3 — Implementation plan + HALT

Before posting the plan, check for file collisions:

```bash
python scripts/ticket_hygiene/ticket.py impact EM-XXXX
```

If overlaps exist, include the output at the top of the plan under a callout:

```
> ⚠️  File collisions: EM-YYYY shares <file> — review before implementing.
```

If no overlaps, suppress entirely.

Post the implementation plan (template in [references/workflow.md](references/workflow.md) §1) and **HALT**. Include:
- Ticket title and honest effort estimate
- A+ approach (one paragraph)
- Files to touch with change descriptions
- Implementation order
- Test plan
- Concerns

Wait for explicit approval before writing code.

### Step 4 — Implement

Follow the approved plan.

**Code rules and test data policy:** see [references/workflow.md](references/workflow.md) §2–§3.

After each meaningful change, run the mid-implementation PR gate:

```bash
pytest tests/ -q
mypy clean_paste_lite/ tests/
python scripts/ticket_hygiene/ticket.py lint
```

Run the extended suite only if the ticket modifies performance-critical code, benchmark tests, or `PerformanceConfig`. See [references/pr-gate.md](../../references/pr-gate.md) for full criteria.

### Step 5 — Final verification

1. Read every acceptance criterion and verify it against actual code.
2. Run the full PR gate:
   ```bash
   pytest tests/ -q -n auto
   mypy clean_paste_lite/ tests/
   python scripts/ticket_hygiene/ticket.py lint
   ```
3. Classify test files: unit → `tests/unit/`, integration → `tests/integration/`, regression → `tests/regression/`. Delete placeholder tests.
4. Update `AGENTS.md` or `README.md` if public APIs or architecture changed.
5. If `AGENTS.md` was modified, update its `<!-- Last verified: YYYY-MM-DD -->` date.

### Step 6 — Write retro and hand off to `/close`

Append a `## Retro` section to the bottom of the ticket file. Template and required fields: see [references/workflow.md](references/workflow.md) §5.

After the retro is written:

1. Output exactly:
   ```
   EM-XXXX retro written. Handing off to /close.
   ```
2. Immediately execute the `/close EM-XXXX` skill starting at **Step 0**.

**Do not** post the retro in chat here — `/close` → `/ceremony` will do that.
**Do not** present the decision menu — `/close` Step 0 owns it.
**Do not** mention `/approve`, `/ceremony`, or commit modes here.

See [`.agents/skills/close/SKILL.md`](../../close/SKILL.md) for the full post-retro workflow.

## Non-negotiable rules

- The ticket ID is mandatory. Do not proceed without it.
- Do not commit without explicit instruction.
- Do not revert or overwrite changes you did not make — check `git diff` first.
- Do not make architectural decisions unilaterally.
- Do not choose the easier option when a better one exists.
- Every **HALT** is real — do not proceed without explicit human approval.
- At Step 6: do not present the decision menu, do not offer simplified choices, do not mention `/approve` or `/ceremony`. Delegate immediately to `/close` Step 0.

---
name: close
description: Post-retro decision skill for a CleanPaste Lite ticket. Decides whether to do extra work (A+ review + "If I Had 3 More Hours", future-item tickets, both, or neither), whether to commit and close now or move on to the next ticket, and how to commit. Closes the ticket with the hygiene tool only when the user chooses to commit/close now. Use when the user says "close EM-XXXX", "commit and close", "close the ticket", or after `/fix`/`fix-now` writes a retro. Requires a ticket ID as the first argument.
---

# Close Ticket

`close` is the post-retro decision point. It does **not** implement the original ticket — that is `/fix` or `/fix-now`. It handles two separate questions:

1. **Extra work before closure?** Complete "If I Had 3 More Hours", create follow-up tickets for Future Items, both, or neither.
2. **What next?** Commit and close the ticket now, or move on to the next ticket and leave this one open.

These two decisions are independent. A user can ask for extra work and still decide to move on without committing, or choose no extra work and commit immediately.

When the user chooses **commit and close now**, `/close` invokes `/ceremony` to perform the actual close and commit. When the user chooses **move on**, `/close` performs any selected extra work, updates the retro, and halts — the ticket stays in `open/`.

## Core principle

**Scope creep is not the enemy. Mediocrity is.**

During the final review, do not settle for "good enough." If something can be cleaner, more elegant, more Rust-portable, or better tested: fix it. The close step is the last chance to make the work A+.

## Quick start

1. **The ticket ID is mandatory.** The user's first message must include it (e.g. `close EM-1677` or `EM-1677`). If it is missing, ask for it and stop until you receive it.
2. Run the pre-flight helper:
   ```bash
   python3 .agents/skills/close/scripts/close_ticket.py EM-XXXX
   ```
3. Build the **recommendation** (see Step 0) and output it directly above the **decision menu** (verbatim — do not paraphrase the menu itself) and **HALT** for the user's choices.
4. Record extra-work mode (a/b/c/d) and next-step choice (i/ii).
5. If the extra-work mode includes implementation work, read every file in **Files Touched** and perform the A+ review (see [references/a-plus-review.md](../../references/a-plus-review.md)).
6. Fix any polish issues and test gaps.
7. Complete every "If I Had 3 More Hours" item (if selected).
8. Create follow-up tickets for Future Items (if selected).
9. Update the retro with a **Quality Closure** note (if any extra work was done).
10. If the next step is **commit & close now**, run `/ceremony` — ceremony will handle commit mode and the forward-impact sweep.
11. If the next step is **move on**, halt with the ticket still open.

## Step 0 — Post-retro decision menu

### Build the recommendation

Before outputting the menu, read `### If I Had 3 More Hours` and `### Future Items` in the retro and form a real opinion — this is not a formality.

For each bullet, judge:
- **Size and risk.** Small, low-risk, well-understood → do now. Open-ended, speculative, or a scope shift → ticket for later.
- **Whether it's still in context.** If finishing it means re-reading files you already have loaded and reasoning you already did, doing it now is cheap. If it needs new investigation or touches code outside this ticket's Files Touched, the context advantage is gone and a ticket loses little.
- **Whether the bullets are mixed.** It's fine for some items to warrant "do now" and others "ticket" in the same retro — say so explicitly rather than forcing one verdict on all of them.

Output a short recommendation block **immediately before** the menu (this is the one allowed exception to "no preamble" below):

```
Recommendation: <a/b/c/d> — <one sentence: why this mode fits what's in "3 More Hours" and "Future Items">
```

If the bullets split between do-now and ticket-later, say so in the same sentence (e.g. "b for items 1–2 — small and in context; d for item 3 — bigger scope, ticket it"). Keep it to 1–2 sentences. This is a recommendation, not a decision — the user still chooses.

### The menu

Output the following text **exactly as written** — substitute the real ticket ID for EM-XXXX, but do not change any other wording. Do not paraphrase. Do not collapse or simplify the options. Do not add preamble beyond the recommendation block above.

```
EM-XXXX retro written. What would you like to do next?

Extra work before closing?

  a) None
  b) Complete "If I Had 3 More Hours" (A+ review + implement every bullet)
  c) Complete "If I Had 3 More Hours" + create tickets for Future Items
  d) Create tickets for Future Items only

What next?

  i) Commit and close now (I'll ask for commit mode after)
  ii) Move on — leave EM-XXXX open, no commit
```

**HALT.** Output nothing else after the menu. Do not ask "shall I proceed?". Do not mention `/approve`, `/ceremony`, or commit modes yet. Do not offer a shortened or combined version of the choices. Wait for the user to answer both questions.

After the user responds, record:
- Extra-work mode: a / b / c / d
- Next step: i / ii

If the user chooses **(ii) Move on**, the ticket is **not closed** and no commit is made. Any selected extra work is still performed and recorded in the retro.

## Step 1 — Pre-flight checks

Find the ticket file in `tickets/<epic>/open/`. It must be in `open/`.

Verify:
- [ ] Ticket is in `open/` (not already closed)
- [ ] Ticket contains a `## Retro` section
- [ ] Retro contains `### If I Had 3 More Hours`
- [ ] No open `depends_on` remain (check frontmatter and the hygiene tool)

If any check fails, stop and report.

Use the helper script to verify these automatically.

## Step 2 — A+ code review

**Skip this step if extra-work mode is (a) or (d).** In mode (a) there is no extra work to review; in mode (d) only Future Items are being ticketed. Proceed to the appropriate later step after Step 1.

Read every file listed in **Files Touched**. Produce the review block and apply fixes per [references/a-plus-review.md](../../references/a-plus-review.md). Run the full PR gate before proceeding (see [references/pr-gate.md](../../references/pr-gate.md)).

## Step 3 — Complete "If I Had 3 More Hours"

**Run this step only if extra-work mode is (b) or (c).**

Read the `### If I Had 3 More Hours` section. Do **every single bullet** now.

For each item:
1. Read it.
2. Implement the most concrete useful interpretation.
3. Run `pytest tests/ -q` after each item — fix regressions before continuing.
4. Append `(✓ done)` to the bullet.

Do not skip vague items — interpret them concretely and implement.

After all items, run the full PR gate again (see [references/pr-gate.md](../../references/pr-gate.md)).

## Step 4 — Create follow-up tickets for Future Items

**Run this step only if extra-work mode is (c) or (d).**

For each bullet under `### Future Items` in the retro:

1. Choose a concise title from the first line of the bullet.
2. Create the ticket in the same epic as the closing ticket:
   ```bash
   python scripts/ticket_hygiene/ticket.py create --auto-id "<title>" --epic EM-EYYY
   ```
3. Open the created ticket file and add context:
   - A one-sentence summary of the future item.
   - A link back to the parent ticket (`Follow-up from EM-XXXX`).
   - The full future-item text under `## Context`.
   - `depends_on: [EM-XXXX]` in frontmatter if the new work is blocked by the parent ticket.
4. Append `(✓ ticketed as EM-YYYY)` to the original future item in the retro.

After creating all follow-up tickets, run ticket hygiene:

```bash
python scripts/ticket_hygiene/ticket.py lint
```

## Step 5 — Update the retro

**Skip this step if extra-work mode is (a).**

Append a **Quality Closure** note to the retro:

```markdown
### Quality Closure

**A+ review:** <verdict — e.g. "A+ after polish" / "A+ no changes needed" / "Skipped">
**Review fixes applied:** <list what was fixed, or "None">
**"3 More Hours" items completed:** <N of N, or "Skipped">
**Future Items ticketed:** <list tickets, or "None">
**Final test count:** <X passed, Y skipped, Z xfailed>
```

In mode (d), note explicitly that the "If I Had 3 More Hours" items were **not** completed and only Future Items were ticketed.

## Step 6 — Commit & close now, or move on

### If the next step is "commit & close now"

Before invoking `/ceremony`, repost the entire updated retro in chat
(including any Quality Closure note or `## BONUS` section). The ceremony skill
will post it again as part of the approval halt, but the close step must not
silently hand off without ensuring the latest retro is visible in the chat.

Hand off to the `/ceremony` skill. Ceremony will ask for commit mode (work-only / all / no-commit) if it is not already known.

See [`.agents/skills/ceremony/SKILL.md`](../../ceremony/SKILL.md) for the full ceremony workflow, halt messages, and commit template.

After `/ceremony` finishes, output exactly:

```
EM-XXXX CLOSED. Ceremony complete. Awaiting instructions.
```

You may run `python scripts/ticket_hygiene/show_path.py --ready` and list newly unblocked tickets — one line each, no commentary.

**HALT.** Do not open the next ticket. Do not start refactoring. Do not generate follow-up work.

### If the next step is "move on"

Do **not** close the ticket. Do **not** commit. Output exactly:

```
EM-XXXX left open with uncommitted changes. Awaiting your next ticket instruction.
```

**HALT.** Wait for the user to tell you the next ticket or task. Do not start the next ticket unprompted.

## Non-negotiable rules

- The ticket ID is mandatory. Do not proceed without it.
- Do not skip pre-flight checks.
- Always build and post the recommendation before the menu — do not present the menu bare, and do not let the recommendation pick for the user.
- Do not skip "If I Had 3 More Hours" items when the extra-work mode includes them.
- Do not close the ticket or commit when the user chose "move on".
- **Never use `ticket.py close --force`.** If the hygiene tool warns about a
  missing retro or open dependencies, fix the actual issue before closing —
  never force past it.
- Do not use `--no-verify` without reporting the issue first.
- The retro must be visible in chat in its final form before the ticket is
  closed. If it changed after the last post, repost it.
- Every fix must be accompanied by a green PR gate before continuing.

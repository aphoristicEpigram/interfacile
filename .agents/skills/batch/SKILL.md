---
name: batch
description: Work through a list of tickets sequentially as a staff engineer. Accepts two to N ticket IDs and presents two independent choice menus — one for how scoping and Q&A work across the batch, one for how ceremony and closure work at the end. Handles all four standard scope paths (bulk Q&A, sequential Q&A, no questions, skip scope) and five ceremony paths (per-ticket, deferred per-ticket, combined approval, defer all, commit-all). Before the menu, filters out any ticket ID that is already closed and reports what was dropped. Use when the user says "batch EM-XXXX EM-YYYY", "work through these tickets", or provides a list of tickets to implement in sequence.
---

# Batch Ticket Work

Orchestrate sequential implementation of multiple tickets with user-controlled
scoping and ceremony behaviour. This skill wraps the standard
`/scope → /fix → /close → /ceremony` chain, but controls *when* HALTs happen
and *how* closure is batched across the list.

No ticket lifecycle step is skipped — the full quality bar of `/scope`, `/fix`,
and `/close` applies to every ticket. What changes is the cadence of HALTs and
approvals across the batch.

## Core principle

**Batching is a sequencing tool, not a quality-cutting tool.**

Running multiple tickets in sequence does not lower the bar for any individual
ticket. Every ticket still gets a staff review, a full implementation, a retro,
and a ceremony. What batch mode controls is *when you stop to talk to the user*
— not whether the work is done properly.

---

## Quick start

1. **Ticket IDs are mandatory.** The argument must contain two or more IDs
   (e.g. `batch EM-1677 EM-1820 EM-1903`). If fewer than two are provided,
   stop and ask.
2. **Filter out already-closed tickets** (Step 0a) and report what was
   dropped, before showing the decision menu.
3. Output the **dual decision menu** (Step 0) and **HALT** for both choices.
4. Record scoping mode (a/b/c/d) and ceremony mode (α/β/γ/δ/ε).
5. Execute the scoping phase for all tickets per the chosen mode.
6. Build each ticket in sequence.
7. Execute the ceremony phase per the chosen mode.

Parse ticket IDs from the argument — accept space-separated or comma-separated
lists. Strip any leading/trailing whitespace. Reject any ID that does not match
the `EM-NNNN` pattern and report it before proceeding.

---

## Step 0a — Filter already-closed tickets

Before the decision menu, check every parsed ID and drop the ones that are
already closed. A closed ticket has nothing left for batch to do — scoping,
building, and ceremony are all no-ops on it — so silently including it would
either error out or waste a scoping/build pass for nothing.

For each ticket ID:

```bash
python3 scripts/ticket_hygiene/ticket.py find EM-XXXX
```

- If the returned path contains `/closed/` → the ticket is already closed.
  Drop it from the batch list.
- If the returned path contains `/open/` → keep it.
- If the tool reports no match → the ticket ID does not exist. Treat this the
  same as an invalid ID: report it and ask the user how to proceed (do not
  silently drop a nonexistent ID the way you drop a closed one — a typo is a
  user error worth surfacing, not a no-op).

If any tickets were dropped for being already closed, report them before the
decision menu:

```
Already closed, skipping: <EM-XXXX> (<title>), <EM-YYYY> (<title>)
Proceeding with: <EM-ZZZZ> [...]
```

If **zero** tickets remain after filtering, halt immediately — there is
nothing to batch:

```
All <N> tickets in this batch are already closed. Nothing to do.
```

**HALT.** Do not proceed to Step 0.

If **exactly one** ticket remains after filtering, continue anyway — do not
re-apply the "two or more" gate to the post-filter list. That gate exists to
stop a batch invocation for what was obviously always a single-ticket job; a
batch that *started* with two or more and lost members to closure is a
different situation, and forcing the user to restart with `/fix` is not
warranted. Say `Only <EM-XXXX> is still open after filtering — continuing.`
in the confirmation output before Step 0, then proceed through Step 0 as
normal (mode menus still apply, ceremony modes γ/ε just cover one ticket).

---

## Step 0 — Dual decision menu

Output the following text **exactly as written**. Substitute real ticket IDs
where shown — using the **post-filter list from Step 0a**, not the original
argument — but do not change any other wording, reorder the options, or add
preamble.

```
Batch: <EM-XXXX> <EM-YYYY> [...]

━━━━ Scoping mode ━━━━

How should I handle scope and open questions across the batch?

  a) Bulk scope + Q&A — scope all tickets upfront, collect every open question
     into one combined list, HALT once for your answers, update all tickets,
     then build sequentially. Surfaced issues are consolidated into a single
     follow-up ticket at the end.

  b) Sequential scope + Q&A — scope ticket 1, HALT if there are questions,
     get your answers, update, then move to ticket 2 and repeat. Each ticket
     scoped and approved before the next begins.

  c) Bulk scope, no questions — scope all tickets upfront, make all
     architectural judgment calls myself, no question HALTs. Build starts
     immediately after scoping.

  d) Skip scope — assume scope is already done for all tickets. Build as-is
     (equivalent to /fix-now × N). No scope HALTs.

━━━━ Ceremony mode ━━━━

How should closure and ceremony work at the end?

  α) Per-ticket ceremony — after each ticket's retro is written, run the full
     /close decision menu and /ceremony before starting the next ticket. N halts
     for N retros. Safest.

  β) Build all, then sequential ceremony — build all tickets first (no ceremony
     halts mid-build), then work through /close + /ceremony for each ticket one
     at a time in sequence.

  γ) Build all, combined approval — build all tickets, post all retros in one
     combined message, HALT once for your approval, then close and commit each
     ticket sequentially.

  δ) Build all, defer ceremony — build all tickets, write all retros, HALT and
     leave all tickets open. You run /ceremony per ticket later.

  ε) Build all, commit-all — build all tickets, write all retros, HALT once for
     your approval, close all tickets with the hygiene tool, then one
     /commit-all for a clean tree.
```

**HALT.** Wait for both choices before doing any work. If the user answers only
one, ask for the other.

After receiving both choices, echo back the chosen modes and the ordered ticket
list before proceeding:

```
Confirmed:
  Tickets: <EM-XXXX> → <EM-YYYY> → [...]   (post-filter list from Step 0a)
  Scoping: <mode letter and name>
  Ceremony: <mode letter/symbol and name>

Starting scoping phase.
```

---

## Phase 1 — Scoping

### Mode a — Bulk scope + Q&A

For **each ticket** in order:
1. Run the full `/scope` assessment (read ticket, epic, deps, code, apply
   rubric).
2. If already implemented: follow the standard already-implemented path
   (write retro, note for human review — do **not** close yet; ceremony handles
   closure).
3. If invalid: follow the standard verdict path but **pause the batch** and
   report. Ask the user how to proceed before continuing.
4. If the effort estimate exceeds 1.5 dev-days: apply **Auto-split large
   tickets** below instead of pausing. Then scope each resulting child ticket
   with steps 1–4 of this mode, same as any other batch member.
5. If valid and ≤ 1.5d: do **not** insert the `## Pre-Work Staff Review` block
   yet and do **not** ask questions yet. Collect every open question into the
   combined list (see below).

After all tickets are scoped, assemble and output the **Combined Question List**:

```markdown
## Batch Open Questions — <EM-XXXX> / <EM-YYYY> / [...]

### EM-XXXX — <ticket title>

#### Q1. <concise question>
**Context:** <one sentence>

| Option | Pros | Cons | Time estimate |
|--------|------|------|---------------|
| **A.** | ... | ... | ... |
| **B.** | ... | ... | ... |

**Suggestion:** <recommended option and why>

---

### EM-YYYY — <ticket title>
...
```

If a ticket has no open questions, note it with a single line:
`EM-XXXX — no open questions; Pre-Work Staff Review written.` and write the
review block immediately for that ticket.

**HALT.** Wait for explicit answers to every open question before continuing.

After receiving answers:
- Write the `## Pre-Work Staff Review` block for each ticket incorporating the decisions.
- Create the **Combined Follow-up Ticket** (see §Combined Follow-up Ticket below).
- Output: `All tickets scoped. Starting build phase.`

---

### Mode b — Sequential scope + Q&A

For **each ticket** in sequence:
1. Output: `Scoping <EM-XXXX> (<N> of <total>).`
2. Run the full `/scope` assessment.
3. Handle already-implemented and invalid cases per the standard paths (pause
   the batch and report if invalid).
4. If the effort estimate exceeds 1.5 dev-days: apply **Auto-split large
   tickets** below instead of pausing. Insert the resulting children into the
   sequence at this ticket's position and continue this mode's steps 1–5 for
   each of them in order.
5. If valid (≤ 1.5d) with open questions: output the question block for **this
   ticket only** (same format as mode a, but single-ticket). **HALT** and wait
   for answers. Update the ticket. Then move to the next.
6. If valid (≤ 1.5d) with no questions: write the review block immediately and
   move to the next ticket without halting.

After all tickets are scoped, output:
`All tickets scoped. Starting build phase.`

---

### Mode c — Bulk scope, no questions

For **each ticket** in order:
1. Run the full `/scope` assessment.
2. Handle already-implemented and invalid cases per standard paths (pause and
   report if invalid).
3. If the effort estimate exceeds 1.5 dev-days: apply **Auto-split large
   tickets** below instead of pausing. Scope each resulting child ticket with
   steps 1–4 of this mode, same as any other batch member.
4. If valid (≤ 1.5d) with open questions: make the best architectural judgment
   call. Document every decision in the `## Pre-Work Staff Review` block under
   a `**Judgment calls:**` bullet. Do **not** halt.
5. Write the `## Pre-Work Staff Review` block for every valid ticket.

After all tickets are scoped, output:
`All tickets scoped (no questions asked). Starting build phase.`

---

### Auto-split large tickets (modes a, b, c)

Batch uses a stricter size threshold than standalone `/scope`, whose single-unit
threshold is ≤ 2d: inside a batch, any ticket whose `/scope` effort estimate
exceeds **1.5 dev-days** gets split automatically instead of pausing the batch
to ask — as long as the split is safe. This keeps individual batch items
manageable without turning batching into a scope-cutting tool.

**Safety is judged per workstream, not per ticket.** A ticket with one coupled
core and several separable edges is not "unsafe to split" as a whole — pull out
everything that *is* safely separable, and only fold the genuinely coupled
remainder into a single child. Never let one atomic piece be an excuse to keep
the whole 5-day ticket as one unit — a 5d ticket that has one 1.5–2d atomic
core plus three independently testable 1-day workstreams should come out as
four children (the core, plus one per separable workstream), not one 5d ticket.
Only fall back to "keep as one unit" when the *entire* ticket is genuinely
atomic end to end — there's no separable piece left to pull out.

**A given workstream is safe to split off** when it can be built, tested, and
merged independently — its own acceptance criteria, its own passing test
suite, not left relying on a sibling workstream that hasn't landed yet.

**A given workstream is not safe to split off — fold it into whichever child
it's coupled to, and keep pulling out everything else:**
- It's part of one atomic change where tests can't go green until every
  coupled piece lands together (e.g. a schema change and its consumer, a
  rename spanning definition and every call site).
- Splitting it off would require a temporary shim, stub, or feature flag purely
  to keep tests passing in the gap between it landing and the piece it's
  coupled to landing. If achieving green tests in between requires inventing
  scaffolding that isn't part of either piece's real acceptance criteria,
  that's the shim this rule exists to prevent — keep those two pieces together,
  but still split off everything else that doesn't have this problem.

**When the estimate exceeds 1.5d (whole-ticket split, or partial split leaving
one coupled remainder above 1.5d):**
1. Run the `/scope` Section 7 decomposition procedure (see
   [`scope/SKILL.md`](../scope/SKILL.md)): the parent ticket becomes a tracking
   wrapper (`child_tickets` frontmatter, `## Workstream Decomposition` table),
   and each child gets its own ticket file with `parent:` frontmatter and its
   own context/approach/files/tests/acceptance criteria. Coupled workstreams
   that can't be safely separated become one child together, not one child per
   coupled piece.
2. Replace the parent's slot in the batch's working ticket order with its
   children, in decomposition order. The parent ticket file stays as the
   tracking wrapper — it is not itself scoped, built, or closed as a unit in
   this batch; only its children are.
3. Report the split and do **not** halt:
   `EM-XXXX (est. Yd) split into EM-XXXX-A, EM-XXXX-B, [...] — continuing batch with children.`
   If one child remains above 1.5d because its pieces are genuinely coupled,
   say so: `EM-XXXX-B stays at Zd — <coupled pieces> can't be split without a shim.`
4. Continue this mode's scoping steps for each child ticket as an ordinary
   batch member.

**Only when the entire ticket is one atomic unit with no separable
workstream at all:** do not split, do not pause the batch. Note it in the
state table: `EM-XXXX — Yd, kept as single ticket (unsafe to split: <reason>)`
and continue scoping it as one ticket.

All child tickets remain part of this batch invocation — nothing produced by a
split is deferred out. Update the batch state table and any ticket lists shown
to the user to reflect the expanded order after a split.

---

### Mode d — Skip scope

For **each ticket**:
1. Verify the ticket exists in `tickets/<epic>/open/`.
2. Verify no open `depends_on` remain.
3. If a `## Pre-Work Staff Review` block is present, read it for context.
4. Do **not** run scope. Proceed directly to the build phase for this ticket.
5. Auto-split does not apply in this mode — scope isn't being run, so there's
   no fresh effort estimate to split on. If a ticket is already known to be
   oversized, run `/scope` on it before starting the batch.

Output: `Scope skipped for all tickets. Starting build phase.`

---

## Phase 2 — Build

Build each ticket **in the order they were listed**, regardless of scoping mode.

For **each ticket**:

1. Output: `Building <EM-XXXX> (<N> of <total>).`
2. Run the full `/fix-now` implementation workflow:
   - Read ground truth (all files in **Files Touched**, git log, deps check).
   - Post the implementation plan and **HALT for approval** (this HALT is
     always honored in all scoping and ceremony modes).
   - After approval, implement.
   - Run the PR gate after each meaningful change.
   - Run final verification.
   - Write the retro (append `## Retro` to the ticket file).
3. **Do not** hand off to `/close` yet — ceremony mode controls when that
   happens (see Phase 3 below).
4. Output: `EM-XXXX built and retro written.`

The implementation plan HALT is **non-negotiable** in all modes. If the user
wants to skip it, they must say so explicitly ("proceed without plan approval")
and you may continue — but note the skip in the retro.

---

## Phase 3 — Ceremony

### Mode α — Per-ticket ceremony

After each ticket's retro is written (Phase 2 step 2), immediately execute
`/close EM-XXXX` from **Step 0**. `/close` presents its standard post-retro
decision menu, halts for the user's choices, and handles A+ review, 3-hours
completion, future-item tickets, and the commit/continue decision including
commit mode selection.

After closure (or if the user chose "move on"), output:
`EM-XXXX done. Moving to next ticket.`

Then continue with Phase 2 for the next ticket.

This repeats until all tickets are processed. Final output:

```
Batch complete. All <N> tickets processed.
<list each ticket and its outcome: closed / left open>
Awaiting instructions.
```

---

### Mode β — Build all, then sequential ceremony

Phase 2 runs for all tickets without ceremony halts. After all tickets are
built and retros written, output:

```
All <N> tickets built. Beginning ceremony phase.

Tickets in order:
  1. EM-XXXX — <title>
  2. EM-YYYY — <title>
  ...
```

Then, for each ticket in order, run the standard `/close` decision menu and
`/ceremony` workflow as in mode α. One ticket at a time; wait for each
ceremony to complete before starting the next.

Final output same as mode α.

---

### Mode γ — Build all, combined approval

Phase 2 runs for all tickets without ceremony halts. After all tickets are
built and retros written, output the **Combined Retro Review**:

```markdown
## Batch Retro Review — <EM-XXXX> / <EM-YYYY> / [...]

---

### EM-XXXX — <ticket title>

<paste the full ## Retro section from the ticket file>

---

### EM-YYYY — <ticket title>

<paste the full ## Retro section from the ticket file>

---
```

Then output:

```
All <N> retros posted. One approval closes and commits all tickets.

Extra work before closing? (applies to all tickets)
  a) None
  b) Complete "If I Had 3 More Hours" for every ticket
  c) Complete "If I Had 3 More Hours" + create Future Item tickets for all
  d) Create Future Item tickets only

Commit mode for all tickets:
  - work-only — commit each ticket's files separately, in sequence
  - all — one commit-all after closing all tickets
  - no-commit — close all tickets but do not commit

Say "proceed" (or your approval phrase) plus your extra-work and commit-mode choices.
```

**HALT.** Wait for explicit approval plus both choices.

After approval:
- If extra work is selected, complete it for **every** ticket before any closure.
- Close each ticket with the hygiene tool in sequence.
- Commit per the chosen mode.
- Final output same as mode α.

---

### Mode δ — Build all, defer ceremony

Phase 2 runs for all tickets without ceremony halts. After all retros are
written, output:

```
All <N> tickets built. Retros written. All tickets left open.

To close each ticket, run:
  /ceremony EM-XXXX
  /ceremony EM-YYYY
  [...]

EM-XXXX left open. EM-YYYY left open. [...] Awaiting instructions.
```

**HALT.** Do not close or commit anything.

---

### Mode ε — Build all, commit-all

Phase 2 runs for all tickets without ceremony halts. After all retros are
written, output the **Combined Retro Review** (same format as mode γ). Then:

```
All <N> retros posted. One approval closes all tickets and runs commit-all.

Extra work before closing?
  a) None
  b) Complete "If I Had 3 More Hours" for every ticket
  c) Complete "If I Had 3 More Hours" + create Future Item tickets for all
  d) Create Future Item tickets only

Say "proceed" (or your approval phrase) plus your extra-work choice.
```

**HALT.** Wait for explicit approval plus extra-work choice.

After approval:
- If extra work is selected, complete it for **every** ticket before closure.
- Dry-run each close first, fix any warning it raises (missing retro, open
  dependencies, etc.), then close for real. Never pass `--force`:
  ```bash
  python scripts/ticket_hygiene/ticket.py close EM-XXXX --dry-run
  python scripts/ticket_hygiene/ticket.py close EM-XXXX
  python scripts/ticket_hygiene/ticket.py close EM-YYYY --dry-run
  python scripts/ticket_hygiene/ticket.py close EM-YYYY
  ...
  ```
- Run commit-all via the commit skill (no argument = commit-all default):
  ```bash
  python3 .agents/skills/commit/scripts/commit.py
  ```
- Commit with a batch message. The `Ceremony:` and `Approved:` markers are
  required by the pre-commit hook (see [`ceremony/SKILL.md`](../ceremony/SKILL.md)
  for the canonical commit template):
  ```bash
  git commit -m "$(cat <<'EOF'
  Batch: <EM-XXXX> <EM-YYYY> [...]: <one-line summary of what the batch accomplished>

  Ceremony: <2–3 sentences covering what each ticket did, key decisions, quality verdicts>

  Approved: <quote the exact phrase the user used to approve the batch>

  - Tickets closed: <N>
  - Final tests: <X passed>
  EOF
  )"
  ```

Final output:

```
Batch complete. <N> tickets closed. Commit-all done.
<list each ticket and its closure>
Awaiting instructions.
```

---

## Combined Follow-up Ticket (mode a only)

After mode a scoping completes and the user has answered all questions, create
one follow-up ticket that consolidates cross-cutting issues surfaced during
batch scoping:

- Issues that appeared in multiple tickets (shared patterns, shared risk)
- Architectural concerns that don't belong to any single ticket
- Test gaps that span the batch
- Questions answered in ways that suggest new work not covered by any ticket in
  the list

Use the next available EM-XXXX number:

```bash
python scripts/ticket_hygiene/ticket.py create --auto-id \
  "Batch follow-up: <EM-XXXX> / <EM-YYYY> / [...]" --epic <most relevant epic>
```

Populate the ticket with:
- A `## Context` section listing every surfaced issue with a back-reference to
  the ticket it came from.
- A `## Why a combined ticket?` section explaining the cross-cutting nature.
- No `depends_on` unless a specific ticket must close first.

If no cross-cutting issues were surfaced, do **not** create the ticket. Instead,
note: `No cross-cutting issues surfaced — combined follow-up ticket not needed.`

---

## State tracking

Maintain a mental (or in-chat) batch state table updated after each phase:

```
Batch state — <timestamp>
  EM-XXXX  scoped ✓  built ✓  ceremony pending
  EM-YYYY  scoped ✓  built —  ceremony —
  EM-ZZZZ  scoped —  built —  ceremony —
```

Print an updated state table after each phase completes.

---

## Handling mid-batch failures

If any ticket fails a pre-flight check, a PR gate, or a hygiene lint during the
batch, **stop the batch** and report:

```
⛔ Batch paused at EM-XXXX (<phase>).

<description of what failed and what output it produced>

Options:
  - Fix and continue: tell me what to do and I'll apply it, then resume.
  - Skip this ticket: I'll mark it skipped and move to the next.
  - Abort batch: I'll halt here. Already-built tickets remain as-is.
```

**HALT.** Wait for explicit direction before resuming.

---

## Non-negotiable rules

- Two or more ticket IDs are mandatory **in the original argument**. Do not
  proceed with fewer. (The post-filter list from Step 0a may legitimately
  drop below two — see Step 0a for how that's handled; this does not violate
  the rule, since the gate is evaluated before filtering.)
- **The implementation plan HALT is always honored**, regardless of scoping or
  ceremony mode. Do not build without explicit approval of the plan.
- Do not close or commit any ticket without explicit human approval (the form of
  that approval is controlled by ceremony mode, but approval is always required).
- Do not lower the quality bar for any individual ticket. Every ticket gets a
  full A+ implementation.
- **Never use `ticket.py close --force`.** If a close warns about a missing
  retro or open dependencies, stop the batch, fix the actual issue, and only
  then re-run the close — do not force past it.
- Do not use `--no-verify` without stopping to report the issue.
- Do not silently skip tickets. If a ticket cannot be processed, pause the batch
  and report. The one exception is Step 0a: dropping an already-closed ticket
  is expected, not a failure — but it must still be reported by name before
  the decision menu, never dropped without a trace. A ticket ID that does not
  exist at all is not covered by this exception — report it as an error.
- Rust-portability is a first-class constraint for every ticket in the batch.
- If scope reveals a ticket is invalid, pause the batch before proceeding. Do
  not auto-skip.
- If scope reveals a ticket exceeds the 1.5d batch threshold, apply **Auto-split
  large tickets** instead of pausing — see Phase 1. Judge safety per workstream:
  pull out every safely-separable piece and only fold genuinely coupled pieces
  together. One atomic piece is never a blanket excuse to keep the whole ticket
  as a single unit. Either way — full split, partial split, or (rarely) no
  split at all — the batch continues without pausing.
- **Utility skills (`rollback`, `reopen`, `abandon`) cannot be batched.** They
  are always single-ticket and always interactive. If any of these IDs appear
  in the batch argument, reject them immediately and ask the user to run those
  skills individually.

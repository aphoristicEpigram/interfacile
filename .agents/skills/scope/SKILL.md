---
name: scope
description: Perform a staff-engineer assessment of a CleanPaste Lite ticket before work begins. Use when asked to review a ticket for validity, architectural correctness, test coverage, documentation, and effort; to close or supersede outdated tickets; to decompose a large ticket into workstreams; to add a Pre-Work Staff Review block to an existing ticket; or to handle an already-implemented ticket by moving it to the close ceremony.
---

# Ticket Staff Review

Review any CleanPaste Lite ticket as a staff engineer. The goal is ground-truth validation against the actual codebase and the Rust-port direction (EM-E005), not a rubber-stamp.

## Core principle

**Scope creep is not the enemy. Mediocrity is.**

Always build the best version. If the ticket proposes a B solution and you see an A+ solution, recommend the A+ solution. If doing it properly requires more files, more tests, more docs, or splitting the ticket into workstreams — do that. We take the time to do it right. Decomposition is a sequencing tool, not a scope-cutting tool.

## Quick start

1. **The ticket ID is mandatory.** The user's first message must include it (e.g. `scope EM-1677` or `EM-1677`). If it is missing, ask for it and stop until you receive it.
2. Run the ground-truth helper:
   ```bash
   python3 .agents/skills/scope/scripts/scope_ticket.py EM-XXXX
   ```
3. Read the generated scaffold and the actual files it points to.
4. Apply the rubric in [references/rubric.md](references/rubric.md).
5. Determine whether the ticket is already implemented (see §4).
6. For valid, small tickets, decide whether outstanding questions require user input (see §5b).
7. Modify or create tickets as required by the verdict.

## Workflow

### 1. Read the ticket

Find the ticket under `tickets/<epic>/open/` or `tickets/<epic>/closed/`. Read it in full. Also read:
- The epic's top-level `.md` file.
- Any tickets listed in `depends_on` or `blocks`.

Use the helper script to locate these automatically.

If the ticket is already in `closed/`, report that and stop. Do not re-scope a closed ticket unless the user explicitly asks for a retro review.

### 2. Read the actual code

Trust code, not the ticket.

- Read every file in the ticket's **Files Touched** section.
- Verify the referenced modules, classes, and functions exist in their current form.
- Check `git log --oneline -20` for recent changes that affect the ticket.
- Check `git status` and `git diff HEAD -- <files>` for uncommitted changes that might already implement the ticket.
- If the ticket references Rust structures, read `tickets/EM-E005-rust-production-port/EM-E005-rust-production-port.md` and related Rust-port notes.

### 3. Assess

Answer all rubric questions in [references/rubric.md](references/rubric.md). Specifically decide:

- **Valid?** Does the problem exist and do the referenced files exist as described?
- **Already implemented?** Is the work already complete in `HEAD` or the uncommitted working tree?
- **Architecture?** Is the approach current, Rust-portable, and security-aligned?
- **Tests?** Are happy path, failures, edges, and adversarial cases covered?
- **Docs?** Are `docs/guides/`, `docs/security/`, `CHANGELOG`, or README updates needed?
- **Effort?** Honest dev-days. Use `≤ 2d` as the single-unit threshold.

#### Defining the A+ version

Every scope must answer: **what is the A+ version of this ticket?** — architecturally
beautiful, forward-thinking, clean, and elegant; fitting the core essence of the app
(local interception, reversible tokenization, tamper-evident record) and the Rust port.
Concretely:

- **Understand the codebase before choosing a direction.** The verdict is written from
  code you have read, not from the ticket's description of the code. Cite real files and
  line numbers; verify names with grep, not memory.
- **Write pseudocode.** For any non-trivial approach, the scoped ticket carries
  pseudocode (or a Rust signature sketch) grounded in real symbols — existing classes,
  functions, and config fields, not invented APIs. Pseudocode that names things that
  don't exist is zombie scope in disguise.
- **Rust is first-class.** Every approach states its Rust path: the crate, trait/enum
  shape, or an explicit "Python-only, dies at the port" note. An approach that cannot
  name its Rust mapping is not yet scoped.
- **Ask before committing to a direction — when necessary.** If two approaches are
  genuinely defensible and the choice belongs to the user (taste, product surface, scope
  trade-off), halt per §5b with a focused option table. If repo precedent or locked
  architecture already picks the winner, decide, cite the precedent, and record the
  rejected option in the review block — do not outsource decisions the record already
  answers.

#### The five hard questions

Ask these on every ticket before writing a verdict. (Inherited from the staff-engineer
handovers, §8.4 — now archived under `docs/archive/handovers/`; this skill is their
living home.)

1. **Is this ticket a zombie?** Do the files, classes, and functions it references exist
   in the current codebase, in their described form? If not, it is a zombie until proven
   otherwise — verify with grep, then close or rewrite. Do not scope against memory.
2. **Where does the essence live?** Before recommending CLOSED or WONT_FIX, name the
   concern the ticket was protecting and the living ticket or doc that now owns it. If
   the essence has no home, the ticket is not ready to close — capture the concern first.
3. **Is this a Rust port blocker?** If the answer is "unclear," treat it as a blocker
   until proven otherwise. EM-E005 alignment is a first-class constraint, not a future
   concern.
4. **Does this get in the way of scrubbing/unscrubbing or document rule config?** If yes,
   it does not belong in the product — recommend WONT_FIX rather than polishing it.
5. **Are we building the gold standard?** If the ticket proposes a B solution and an A+
   exists, name the A+ and recommend it. Scope creep is not the enemy; mediocrity is.

(The handover's remaining daily checks — every registry mutation emits a
`RegistryEvent`, no callables in serialized config types, no v2 flat-model residue — are
implementation-time questions. They belong to `/fix` verification, not scoping.)

### 4. Already implemented

If the implementation is already complete — either committed to `HEAD` or present as uncommitted changes in the working tree — the ticket does **not** need a Pre-Work Staff Review block. It needs closure.

**Detection heuristics (all must be true):**
- The acceptance criteria are satisfied by existing code (in `HEAD` or working tree).
- The files listed in **Files Touched** contain the described fix/feature.
- Tests covering the change exist and pass (or only trivial polish remains).

**Action:**

1. **Do not insert a Pre-Work Staff Review block.**
2. Verify the implementation by running the relevant tests. Fix only obvious, trivial issues (e.g. a single broken assertion) if they block closure; otherwise report them.
3. Check whether the ticket already has a `## Retro` section containing `### If I Had 3 More Hours`.
   - If yes: proceed to `/close EM-XXXX` (see below).
   - If no: write a retro in the ticket file now. Include:
     - What was implemented and why
     - Design decisions
     - Test count and results
     - `### If I Had 3 More Hours` with at least one concrete item
     - `### Would I Inherit This Code?` with reasoning
     - `### Documentation Updated` (or explicit "None")
     - Docs impact checklist
4. Post the retro in chat and output:
   ```
   EM-XXXX is already implemented. Retro posted. Awaiting approval to proceed with close ceremony.
   ```
   Then **stop** and wait for explicit approval.
5. After approval, run `/close EM-XXXX` and follow its instructions (A+ review, complete 3-hours items, close with the hygiene tool, commit, or move on).

### 5. Valid and small (≤ 2d)

If the ticket is not already implemented, decide whether there are outstanding questions that require user input.

Outstanding questions include:
- Architectural decisions with multiple reasonable options
- Taste or style trade-offs
- Scope expansion or contraction choices
- Tool/library selection
- Any ambiguity that affects the implementation plan

#### 5a. No outstanding questions

If the path forward is clear and no user decision is needed:

0. **Check for file collisions:**

   ```bash
   python scripts/ticket_hygiene/ticket.py impact EM-XXXX
   ```

   If overlaps are reported, print the table in chat. Add a `**File collisions:**`
   bullet to the Pre-Work Staff Review block listing the conflicting ticket IDs and
   shared files. If no overlaps, suppress output entirely — do not print anything.

1. Insert a `## Pre-Work Staff Review` block under the title and before `## Context`:

   ```markdown
   ## Pre-Work Staff Review

   **Verdict:** VALID — proceed
   **Architecture:** <one paragraph>
   **Tests:** <gaps, if any>
   **Docs:** <gaps, if any>
   **File collisions:** <EM-YYYY shares path/to/file.py — informational only> (omit if none)
   **Suggestions:**
   - <specific, actionable item>
   ```

2. Update the ticket file in place. Do not move it.
3. Output:
   ```
   EM-XXXX scoped with no open questions. Proceeding to /fix EM-XXXX.
   ```
4. Execute the `/fix EM-XXXX` skill workflow.

#### 5b. Outstanding questions exist

If user input is required before implementation can safely begin:

1. **Do not update the ticket file yet.** No `## Pre-Work Staff Review` block is written until the questions are resolved.
2. Halt and present each question in the output summary using this format:

   ```markdown
   ## Open Questions — EM-XXXX

   ### 1. <concise question>

   **Context:** <one sentence: why this matters for the implementation>

   | Option | Pros | Cons | Time estimate |
   |--------|------|------|---------------|
   | **A.** <description> | <pros> | <cons> | <e.g. +0h, +2h, +1d> |
   | **B.** <description> | <pros> | <cons> | <e.g. +0h, +2h, +1d> |

   **Suggestion:** <recommended option and why>
   ```

3. Wait for explicit answers from the user.
4. After receiving answers:
   - Update the ticket file with the `## Pre-Work Staff Review` block, incorporating the decisions.
   - Proceed to the `/fix EM-XXXX` skill workflow.

Do not begin implementation while questions are unresolved.

### 6. Invalid or outdated

Close the original ticket and supersede it.

Frontmatter update on the original:

```yaml
status: CLOSED
closed: YYYY-MM-DD
closed_reason: WONT_FIX
closed_note: "<one sentence why>"
superseded_by: [EM-XXXX]
```

Move the file from `open/` to `closed/` in the same epic directory.

Create one or more replacement tickets in the appropriate epic's `open/` directory. Use the next available EM-XXXX number. Include `supersedes: <original>` in frontmatter and a `**Supersedes:**` line in the header.

Match the existing ticket format exactly: frontmatter, `# EM-XXXX — <title>` header, then Context / A+ Architecture Review / Fix / Approach / Files Touched / Tests / Documentation Updates / Acceptance Criteria.

### 7. Too large (> 2d)

A large estimate is not a reason to cut scope. It is a signal that the work needs clean sequencing. Keep the parent ticket open as a tracking wrapper.

Update frontmatter:

```yaml
status: OPEN
child_tickets: [EM-XXXX-A, EM-XXXX-B, ...]
effort: <revised total estimate>
```

Append a `## Workstream Decomposition` table.

Create child tickets `<parent-id>-A`, `<parent-id>-B`, etc. in the same epic's `open/` directory. Each child must be independently workable with its own context, approach, files, tests, and acceptance criteria. Add `parent: <parent-id>` to frontmatter and a `**Parent:**` line in the header.

### 8. Output summary

End with:

```markdown
## Review Complete — EM-XXXX

**Verdict:** VALID (no questions) | VALID (questions pending) | INVALID (WONT_FIX) | DECOMPOSED | ALREADY IMPLEMENTED
**Action taken:** <one sentence>

### What changed
- <file created/modified>
- <file moved>

### Key findings
- <architectural observation>
- <test gap>
- <doc gap>

### Open questions (if any)
- <question> → <suggested answer>

### Suggested next step
<one sentence>
```

## Rules

- **Build the best version.** Do not trim scope to fit an estimate. Decompose for sequencing, not for cutting corners.
- **The ticket ID is mandatory.** Do not proceed without it.
- Do not ask for confirmation before acting — execute the full assessment in one pass, **except**:
  - in the already-implemented branch where you must halt for retro approval before closing;
  - when valid tickets have outstanding architectural/taste/option questions that require user input before implementation.
- Cite file paths and line numbers in findings.
- Rust-port alignment is a first-class constraint, not a future concern.
- Test gaps and doc gaps that are substantial standalone work become new tickets; trivial gaps are noted in the review block.

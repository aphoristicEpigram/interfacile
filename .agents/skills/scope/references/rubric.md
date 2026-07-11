# Staff Engineer Ticket Review Rubric

Use this rubric when assessing a CleanPaste Lite ticket. Cite file paths and line numbers in findings.

> **Core principle:** Scope creep is not the enemy. Mediocrity is. Always build the best version. If doing it properly requires more files, more tests, more docs, or splitting the ticket into workstreams, do that. Decomposition is for sequencing, not for cutting scope.

## Table of Contents

1. [Validity](#1-validity)
2. [Architecture](#2-architecture)
3. [Tests](#3-tests)
4. [Documentation](#4-documentation)
5. [Effort](#5-effort)
6. [Output Templates](#6-output-templates)

---

## 1. Validity

A ticket is **invalid / outdated** if any of the following are true:

- It references code, modules, or APIs that no longer exist or have been substantially renamed.
- The bug or feature it describes has already been fixed or shipped.
- It duplicates an existing open ticket.
- The architecture it proposes conflicts with decisions already made in the Rust port (EM-E005).

A ticket is **valid** if the problem it describes is real and the code it references exists in its described form.

### Checks

- [ ] Read the ticket file in full.
- [ ] Read the epic's top-level `.md` file.
- [ ] Read every ticket in `depends_on` and `blocks`.
- [ ] Read every file in **Files Touched**.
- [ ] Run `git log --oneline -20` to see recent relevant work.
- [ ] If Rust is referenced, read `tickets/EM-E005-rust-production-port/EM-E005-rust-production-port.md`.

---

## 2. Architecture

### Correct and current

- Does the proposed solution reflect the current module structure?
- Does it use the current names of classes, functions, and modules?
- Does it account for recently landed refactoring?

### Rust-portable

CleanPaste Python is the living Rust specification. Reject or fix proposals that rely on:

- Dynamic dispatch via `__dict__` or runtime reflection.
- Monkey-patching or `importlib` tricks.
- `Arc<RwLock<...>>` in the domain layer.
- `Option<&[u8]>` where a typed enum suffices.
- Other patterns that cannot be expressed cleanly in Rust.

### Security-aligned

- Does it align with EM-E018 security and adversarial patterns?
- Does it preserve EM-E001 PII detection contracts?
- Are secrets, keys, and sensitive data handled with the project's crypto posture?

### A+ alternative

- What is the A+ version — architecturally beautiful, forward-thinking, clean, and
  elegant; fitting the core essence of the app and the Rust port?
- Is there a more elegant, more performant, or more forward-compatible approach?
- If yes, name it and recommend it — even if it expands the surface of the ticket.
- Never recommend a smaller-scope B solution over a correct A+ solution just to keep the ticket small.
- The recommended approach carries pseudocode (or a Rust signature sketch) grounded in
  real symbols from the codebase — verified by reading the files, not recalled from the
  ticket text.

---

## 3. Tests

A good ticket specifies tests that are:

- **Behavioural**, not implementation-coupled.
- **Comprehensive**: happy path, all failure modes, edge cases.
- **Adversarial** where the security context demands it.
- **Rust-portable**: they describe *what* must hold, not *how* Python achieves it.

### Checklist

- [ ] Happy path covered.
- [ ] Failure modes covered.
- [ ] Edge cases covered.
- [ ] Adversarial / fuzzing cases included where appropriate.
- [ ] Tests target public behaviour, not private internals.

---

## 4. Documentation

A ticket that changes public APIs, types, architecture, or security posture must include documentation updates.

### Checklist

- [ ] `docs/guides/` updated if user-facing behaviour changed.
- [ ] `docs/security/` updated if security posture changed.
- [ ] `docs/architecture/` updated if module structure changed.
- [ ] `README.md` updated if quick-start or public surface changed.
- [ ] `AGENTS.md` updated if agent-facing conventions changed.
- [ ] `CHANGELOG` updated if a release-facing change is shipped.

---

## 5. Effort

Estimate honest dev-days. Prefer doing it right over doing it fast.

- **≤ 2 dev-days total**: single unit of work.
- **> 2 dev-days total**: must be decomposed into workstreams for clean sequencing.

A large estimate is never a reason to trim scope. It is only a signal that the work should be split into independently workable pieces.

When decomposing:

- Child tickets use IDs `<parent>-A`, `<parent>-B`, etc.
- Each child is independently workable.
- Order children so they can be worked sequentially.
- Add `child_tickets` to the parent frontmatter.
- Add a `## Workstream Decomposition` table to the parent body.

---

## 6. Output Templates

### Pre-Work Staff Review block (valid, ≤ 2d)

Insert under the title, before `## Context`:

```markdown
## Pre-Work Staff Review

**Verdict:** VALID — proceed
**Architecture:** <one paragraph>
**Tests:** <gaps, if any>
**Docs:** <gaps, if any>
**Suggestions:**
- <specific, actionable item>
```

### Close-as-WONT_FIX frontmatter

```yaml
status: CLOSED
closed: YYYY-MM-DD
closed_reason: WONT_FIX
closed_note: "<one sentence why>"
superseded_by: [EM-XXXX]
```

### Replacement / child ticket frontmatter

```yaml
---
id: EM-XXXX
title: 'T-XXXX: <descriptive title>'
epic: <epic id>
status: OPEN
risk: <HIGH | MEDIUM | LOW>
effort: <Nh | Nd>
priority: <1-3>
depends_on: []
blocks: []
created: YYYY-MM-DD
supersedes: <original ticket ID>   # for replacements
parent: <parent ticket ID>         # for children
---
```

### Workstream Decomposition table

```markdown
## Workstream Decomposition

This ticket exceeds the 2-dev-day threshold and has been broken into the following workstreams:

| ID | Title | Effort | Priority |
|----|-------|--------|----------|
| EM-XXXX-A | ... | ... | 1 |
| EM-XXXX-B | ... | ... | 2 |
```

### Final summary

```markdown
## Review Complete — EM-XXXX

**Verdict:** VALID (no questions) | VALID (questions pending) | ALREADY IMPLEMENTED | INVALID (WONT_FIX) | DECOMPOSED
**Action taken:** <one sentence>

### What changed
- <file created/modified>
- <file moved>

### Key findings
- <architectural observation>
- <test gap>
- <doc gap>

### Suggested next step
<one sentence>
```

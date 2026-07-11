---
id: EM-1148
title: "Ticket Creation Standard — Format, Frontmatter, and Acceptance Criteria"
status: STANDING
risk: LOW
effort: N/A
depends_on: []
blocks: []
created: 2026-06-03
---

# EM-1148 — Ticket Creation Standard

**Epic:** EM-E020
**Type:** Standing process ticket
**Status:** STANDING — permanent reference, updated as process evolves

---

## Filename Convention

```
EM-NNNN-short-kebab-description.md
```

- `EM-NNNN` — numeric ID, 4 digits minimum
- Optional suffix: `EM-NNNN-A` for sub-tickets (e.g., `EM-1138-A`). Use a hyphen before the letter.
- `short-kebab-description` — 3–6 words, lowercase, kebab-case
- **Must** match the frontmatter `id` field prefix

**Good:** `EM-1145-ticket-hygiene-audit.md`
**Bad:** `ticket-hygiene.md` (no ID), `EM-1145.md` (no description)

### Special Suffixes

| Suffix | Meaning | Example | When to Use |
|---|---|---|---|
| `.md` | Standard ticket | `EM-1150-foo.md` | All normal work |
| `.x.md` | Won't Fix | `EM-1103-receipt-print-footgun.x.md` | Idea rejected, decision documented |
| `.s.md` | Superseded | `EM-1138-session-scope-scrubconfig.s.md` | Replaced by another ticket |

> **Rule:** `.x.md` and `.s.md` tickets live in `closed/` and have `status: CLOSED` or `status: WONT_FIX`.

---

## Epic Directory Layout

Every epic lives at `tickets/EM-EXXX-descriptive-name/` with this structure:

```
tickets/EM-E001-lightweight-clipboard-scrubber/
  EM-E001-lightweight-clipboard-scrubber.md   # epic file
  open/                                       # open tickets
  closed/                                     # closed tickets
```

**Rules:**
- Epics are permanent containers — they are never closed or locked. See EM-1147.
- Every epic MUST have both `open/` and `closed/` subdirectories, even if one is empty.
- Ticket files live inside `open/` or `closed/` — never at the epic root.
- The epic markdown file lives at the epic root.
- `tickets/closed/` as an epic container is obsolete. All epics live directly under `tickets/`.
- `tickets/future/` is a valid staging area for epics that are not yet active.

---

## Status Symbols (TICKET_INDEX.md)

| Symbol | Meaning | Used For |
|---|---|---|
| ⬜ | Open | Tickets ready to start or in progress |
| 🔄 | In Progress | Tickets actively being worked |
| ✅ | Closed | Completed tickets with retro |
| 🏛️ | Standing | Permanent process tickets (never "done") |
| 🚫 | Blocked | Waiting on dependency |
| ⬆️ | Superseded | Replaced by another ticket (`.s.md`) |
| ❌ | Won't Fix | Rejected idea (`.x.md`) |

---

## Strict YAML Frontmatter Format

Every ticket file MUST start with exactly one YAML frontmatter block. The scanner uses `yaml.safe_load()` with duplicate-key detection — invalid YAML or duplicate keys will cause an ERROR.

### Standard Template

```yaml
---
id: EM-NNNN
title: "TNNNN: Human-Readable Title"
epic: EM-EXXX
status: OPEN
risk: LOW
effort: 2h
depends_on: []
blocks: []
created: YYYY-MM-DD
closed: YYYY-MM-DD
---
```

### Field Specification

| Field | Type | Required | Allowed Values | Notes |
|---|---|---|---|---|
| `id` | string | Yes | `EM-\d+(?:-[A-Z]+)?` | Must match filename prefix. Sub-tickets use a hyphen before the suffix, e.g. `EM-1138-A`. |
| `title` | string | Yes | Any, quoted if contains `:` or `"` | Always quote defensively. Include ticket ID for grepability. |
| `epic` | string | Yes | `EM-E\d+` | Short epic name (e.g. `EM-E020`, not `EM-E020-process-hygiene`) |
| `status` | string | Yes | `OPEN` \| `CLOSED` \| `STANDING` \| `WONT_FIX` | Exactly these four values. Case-sensitive. |
| `risk` | string | Yes | `LOW` \| `MEDIUM` \| `HIGH` | Exactly these three values. Case-sensitive. |
| `effort` | string | Yes | `Nh` \| `Nd` \| `N/A` | `N/A` for standing/process tickets. `h` = hours, `d` = days. |
| `depends_on` | list | Yes | `[]` or `[EM-XXXX, EM-YYYY]` | Empty list if none. Always bracket form with spaces after commas. |
| `blocks` | list | Yes | `[]` or `[EM-XXXX, EM-YYYY]` | Empty list if none. |
| `created` | string | Yes | `YYYY-MM-DD` | ISO date format. |
| `closed` | string | Conditional | `YYYY-MM-DD` | **Required when `status: CLOSED`**. **Must NOT exist when `status: OPEN` or `STANDING`**. Optional when `status: WONT_FIX`. |
| `index_note` | string | Optional | Any plain string | Populates the Notes column in the generated `TICKET_INDEX.md`. Set at closure (EM-1146 ceremony). **Must NOT exist when `status: OPEN`**. |
| `index_exempt` | boolean | Optional | `true` | Excludes the file from the generated index and suppresses `[index]` scanner warnings. Use on retros, Q&A docs, epic summaries, companion files. |

### Field Order

Use this exact order for consistency (scanner does not enforce order, but humans do):

```yaml
id:
title:
epic:
status:
risk:
effort:
depends_on:
blocks:
created:
closed:          # only when CLOSED
index_note:      # only when CLOSED — set during closure ceremony
index_exempt:    # only on non-primary docs (retros, Q&A, epic summaries)
```

### Quoting Rules

| Value Contains | Quote? | Example |
|---|---|---|
| `:` (colon) | **Yes** | `title: "T1150: Scanner"` |
| `"` (double quote) | **Yes**, use single quotes | `title: 'The "Foo" Bar'` |
| `#` (hash) | **Yes** | `title: "Issue #123"` |
| Emoji | **Yes** (defensive) | `title: "🏛️ Process Ticket"` |
| Plain alphanumeric | Optional | `epic: EM-E020` |
| List | Never | `depends_on: [EM-1145]` |
| Date | Never | `created: 2026-06-03` |

### List Format

Always use the compact bracket form:

```yaml
# GOOD
depends_on: [EM-1145, EM-1146]
blocks: []

# BAD — block style (harder to grep)
depends_on:
  - EM-1145
  - EM-1146

# BAD — missing spaces after commas
depends_on: [EM-1145,EM-1146]

# BAD — quotes inside list
depends_on: ["EM-1145", "EM-1146"]
```

### Prohibited

- **Duplicate keys.** The scanner raises ERROR.
- `status: OPEN` in a file inside `closed/`.
- `status: CLOSED` in a file inside `open/`.
- `closed:` field when `status` is `OPEN` or `STANDING`.
- Missing `closed:` field when `status` is `CLOSED` (active epics only; legacy in `tickets/closed/` grandfathered).
- Extra whitespace inside brackets: `[ EM-1145 ]`.

### Decomposition Tickets

When a large ticket is split into children, use letter suffixes on the parent ID
(`EM-NNNN-A`, `EM-NNNN-B`, …). Do **not** allocate new sequential IDs for
children. Use `depends_on`/`blocks` to express **execution order between
siblings**, not a parent/child ownership relationship. The epic field already
encodes ownership.

**Example — EM-1721 split into EM-1721-A / EM-1721-B / EM-1721-C:**

```yaml
# Parent decomposition ticket
EM-1721:
  child_tickets: [EM-1721-A, EM-1721-B, EM-1721-C]
  depends_on: [EM-1721-C]   # parent closes after the last child
  blocks: []

# Children chain their execution order
EM-1721-A:
  depends_on: []
  blocks: [EM-1721-B]

EM-1721-B:
  depends_on: [EM-1721-A]
  blocks: [EM-1721-C]

EM-1721-C:
  depends_on: [EM-1721-B]
  blocks: []
```

**Rules:**

1. **Children reuse the parent ID with a letter suffix.** Use `EM-2131-A`,
   `EM-2131-B`, `EM-2131-C`, not `EM-2132`, `EM-2133`, etc.
2. **Children must not `depends_on` the parent.** The parent is the epic
   container; depending on it would block children on a ticket that is
   intentionally kept open until they finish.
3. **The parent should `depends_on` the final child** (or all children if they
   are parallel). This makes the parent ready only after the work it tracks is
   complete.
4. **Optional:** Add `child_tickets: [EM-NNNN-A, EM-NNNN-B, ...]` to the parent
   frontmatter for discoverability.
5. **Avoid reciprocal `blocks` or reciprocal `depends_on`.** If A blocks B, B
   must not block A. The hygiene scanner flags these as errors.
6. **Closed tickets should not depend on open tickets.** A closed ticket whose
   `depends_on` points to an open ticket means the dependency graph is inverted
   or the closure was premature.

---

## Body Structure

```markdown
# EM-NNNN — Title

**Epic:** [Link to epic]
**Status:** OPEN / CLOSED / etc.
**Risk:** 🟡 Medium — brief reason
**Blocks:** — or EM-XXXX

## Context

Why this ticket exists. 2–4 sentences.

## The Bug / The Task / The Decision

What needs to happen. If it's a bug, include:
- Found by (test name or reporter)
- Current behavior (with code snippet)
- Expected behavior

## Fix / Approach

What changes. Include code snippets if helpful.

## Acceptance Criteria

- [ ] Criterion 1 (verifiable in code)
- [ ] Criterion 2 (verifiable in tests)
- [ ] Criterion 3 (verifiable in docs)

## Effort

~Nh. Brief justification.
```

---

## Examples

### Standard Work Ticket

```yaml
---
id: EM-1151
title: "T1151: Fix Scanner Errors and Make CI Blocking"
status: OPEN
risk: LOW
effort: 30m
depends_on: [EM-1150]
blocks: []
created: 2026-06-03
---
```

### Standing Ticket

```yaml
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
```

Note: **No `closed:` field** because `status: STANDING`.

### Won't Fix Ticket

```yaml
---
id: EM-1103
title: "Receipt Print Footgun"
epic: EM-E011
status: WONT_FIX
risk: LOW
effort: N/A
depends_on: []
blocks: []
created: 2026-06-02
closed: 2026-06-02
---
```

Note: `closed:` is optional for `WONT_FIX` but recommended.

---

## Retro Section (Added at Closure)

See EM-1146 (Ticket Closure Ceremony) for the retro template.

---

## Prohibited (Beyond Format)

- **No hardcoded checksums in tests.** Use `make_token()` from `tests/support/helpers.py`.
- **No reserved fake values as test input.** See AGENTS.md Test Data Policy.

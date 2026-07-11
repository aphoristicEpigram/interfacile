---
id: EM-0001
title: Example Open Ticket
epic: EM-E001
status: OPEN
risk: MEDIUM
priority: 2
effort: 1d
depends_on: []
blocks: []
created: 2026-01-01
updated: 2026-01-01
---

# EM-0001 — Example Open Ticket

**Epic:** EM-E001 — Example Epic
**Status:** OPEN
**Risk:** MEDIUM
**Effort:** 1d

## Context

Why this ticket exists — the problem or opportunity in one or two short paragraphs.
The dashboard renders this body as Markdown; the table above the fold is built from
the YAML frontmatter.

## Scope

- A concrete, checkable list of what "done" means
- Kept small enough to close in the stated effort

## Out of scope

- Things a reader might expect here but that belong in another ticket

## Frontmatter fields (reference)

| Field | Meaning |
|---|---|
| `id` | `EM-####` (or compound `EM-####-A`). Must be unique. |
| `epic` | The `id` of the owning epic (`EM-Exxx`). |
| `status` | `OPEN`, `CLOSED`, or `WONT_FIX`. |
| `risk` | `LOW` / `MEDIUM` / `HIGH`. |
| `priority` | Integer; lower is more urgent. |
| `effort` | Free-form estimate, e.g. `1d`, `2-3d`. |
| `depends_on` | List of ticket ids that must close first. |
| `blocks` | List of ticket ids this one blocks. |
| `created` / `updated` | ISO dates. |

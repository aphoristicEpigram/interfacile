---
id: EM-1149
title: "Stale-Ticket Scanner — Design, Usage, and CI Wiring"
status: STANDING
risk: LOW
effort: N/A
depends_on: []
blocks: []
created: 2026-06-03
---

# EM-1149 — Stale-Ticket Scanner

**Epic:** EM-E020
**Type:** Standing process ticket
**Status:** STANDING — permanent reference, updated as process evolves

---

## Purpose

Automate the detection of stale tickets, mismatched frontmatter, and broken cross-references. Replaces manual `grep` work (see EM-1145, which took ~1.5 hours for 16 tickets).

## Script

**Location:** `scripts/ticket_hygiene/audit_tickets.py`
**Run:**
```bash
python scripts/ticket_hygiene/audit_tickets.py
```

## What It Checks

### ERROR (fails CI)

| Check | Description |
|---|---|
| Status/location mismatch | `status: OPEN` in `closed/` or `status: CLOSED` in `open/` |
| Missing `closed:` date | `status: CLOSED` without `closed:` in frontmatter (active epics only; legacy tickets in `tickets/closed/` are grandfathered) |
| Epic does not exist | `epic: EM-EXXX` but no matching directory in `tickets/`, `tickets/closed/`, or `tickets/future/` |
| Filename/id mismatch | Frontmatter `id` does not match filename prefix |
| Index orphan | Ticket ID referenced in `TICKET_INDEX.md` but no file exists |
| Missing retro | `status: CLOSED` without `## Retro` section (unless `retro_exempt: true`) |
| Missing required fields | `id`, `title`, `status`, `created` missing (epic files); full set for work tickets |

### WARNING (informational, does not fail CI)

| Check | Description |
|---|---|
| Missing `epic` field | Frontmatter has no `epic` key (exempt for epic files: `EM-E\d+`) |
| Not in TICKET_INDEX.md | Ticket file exists but is not referenced in the index (exempt if `index_exempt: true`) |
| Broken cross-reference | `depends_on`, `blocks`, or `closes:` references a non-existent ticket |
| Stale docs reference | Ticket ID in README.md or AGENTS.md with open indicator (⬜, 🔄, 📋, "open") but ticket is CLOSED/WONT_FIX. Run `ticket.py lint --docs`. |

## CI Integration

Wired into `scripts/ci/ci-smoke-test.sh` as step 5. **Blocking** — any ERROR fails the CI run.

```bash
# 5. Ticket hygiene
echo ""
echo "[5/5] Running ticket hygiene audit..."
$PYTHON scripts/ticket_hygiene/audit_tickets.py
```

Also enforced via pre-commit hook at `.git/hooks/pre-commit` (installed from `scripts/git-hooks/pre-commit`).

## Adding New Checks

1. Add a new `_check_*` function in `audit_tickets.py`.
2. Call it from `main()`.
3. Decide if it should be ERROR or WARNING.
4. Update this ticket with the new check.
5. Update the scanner's docstring.

## Future Enhancements

> **Implementation ticket:** EM-1150 built the scanner and wired it into CI.

- **Baseline mode:** `--baseline` flag that writes known issues to `.ticket_hygiene_baseline.json` and only fails on *new* issues.
- **Line-range validation:** For tickets with "code location to verify", check if the file and line range still exist.
- **Circular dependency detection:** `depends_on` chains that loop back to themselves.
- **Stale cross-references:** Tickets that reference epics which have been closed for >N days.
- **Docs drift (`--docs`):** Implemented in EM-1561. Scans README.md and AGENTS.md for ticket IDs with open indicators that are actually CLOSED/WONT_FIX. Also detects non-existent IDs and range-notation edge cases.

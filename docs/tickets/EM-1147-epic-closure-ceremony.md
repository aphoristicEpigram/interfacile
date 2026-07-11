---
id: EM-1147
title: "Epic Closure Ceremony — Step-by-Step Checklist"
status: STANDING
risk: LOW
effort: N/A
depends_on: []
blocks: []
created: 2026-06-03
---

# EM-1147 — Epic Closure Ceremony

**Epic:** EM-E020
**Type:** Standing process ticket
**Status:** STANDING — permanent reference, updated as process evolves

---

## When to Use This

Every time an epic is declared complete.

---

## Step 1 — Burn-Down Verification

- [ ] All child tickets in `open/` are either:
  - Closed (with retro and frontmatter updated)
  - Explicitly moved to another epic
  - Explicitly deferred with a note
- [ ] No orphan tickets remain in the epic's `open/` folder.
- [ ] Run the stale-ticket scanner: `python scripts/ticket_hygiene/audit_tickets.py`

---

## Step 2 — Side-Effect Audit

- [ ] Check if any tickets in *other* epics were fixed as side effects of this epic.
- [ ] For each suspected side-effect closure, verify the code (do not assume).
- [ ] Close verified side-effect tickets following EM-1146.
- [ ] Leave unverified tickets open with an audit note.

> **Rule:** Epics do not get to claim closures they did not verify. See EM-1145 for a cautionary tale.

---

## Step 3 — Write the Epic Retro

Update the epic's markdown file with a retro section:

```markdown
## Retro

**Status:** CLOSED YYYY-MM-DD
**Actual effort:** X days
**Tickets closed:** N (list them)
**Tickets superseded:** M (list them)
**Side-effect closures:** K (list them, with verification notes)
**Test count at close:** X passed, Y skipped, Z xfailed

### What Went Well
- 3–5 bullets

### What Didn't Go Well
- 3–5 bullets

### Metrics
- Tests, runtime, mypy errors, files touched, files deleted

### Related
- Links to child tickets, follow-up tickets, blocked tickets now unblocked
```

---

## Step 4 — Move the Epic File

- [ ] If the epic file is at the epic root (`tickets/EM-EXXX-foo/EM-EXXX-foo.md`), it stays there.
- [ ] If there is a `closed/` folder inside the epic, ensure all closed tickets are in it.
- [ ] **Do not** move the epic file into `tickets/closed/`. The epic folder itself is the canonical location.
- [ ] Update `TICKET_INDEX.md` epic section header with `✅ CLOSED`.

---

## Step 5 — Update TICKET_INDEX.md

- [ ] Update the epic's section in `TICKET_INDEX.md` with closure date and summary.
- [ ] Update all child ticket rows with closure notes.

---

## Step 6 — Commit

```bash
git add -A
git commit -m "EM-EXXX: Close epic — <epic title>

- Burn-down: N tickets closed
- Side-effect closures: K (verified)
- Retro in epic file
- Tests: X passed, Y skipped, Z xfailed"
```

---

## After Closure

- [ ] The epic is **locked**. No new tickets go into it.
- [ ] Future work that would have belonged here goes into a new epic or an active epic.
- [ ] If a closed epic ticket needs reopening, create a new ticket referencing the old one.

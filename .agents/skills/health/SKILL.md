---
name: health
description: Show ticket health dashboard — age (days since created:), blocking duration, and 🟢/🟡/🔴 severity for every open ticket. Read-only. Accepts an optional epic filter. Use when the user says "health", "ticket health", "show stale tickets", "what's been open longest", or "health EM-EXXX".
---

# Health

Display a per-ticket health table showing how long each open ticket has been open
and how long it has been blocked. No files are modified.

## Quick start

1. Parse the optional argument (epic filter or absent).
2. Run `ticket.py health` with the filter if given.
3. Print the output verbatim.
4. HALT.

---

## Step 0 — Argument parsing

Accept arguments in any of these forms:

- `health` — show all open tickets
- `health EM-EXXX` — filter to one epic
- `health --epic EM-EXXX` — same

If the argument looks like a ticket ID (`EM-NNNN`, not an epic ID), say:

```
/health takes an epic ID (e.g. EM-E020) or no argument.
To see one ticket, use: ticket.py find EM-XXXX
```

**HALT.**

---

## Step 1 — Run health command

```bash
python scripts/ticket_hygiene/ticket.py health [--epic <EPIC>]
```

Omit `--epic` if no filter was given. Print the output verbatim.

If the command exits non-zero, print the error and **HALT**.

---

## Step 2 — Report

After printing the health table, output:

```
🟢 < 14 days  |  🟡 14–30 days  |  🔴 > 30 days
Age column: days since created:   Blk column: days blocked by open depends_on
```

Then **HALT**. Do not suggest fixing, scoping, or opening any ticket.

---

## Non-negotiable rules

- **Read-only.** Do not modify any ticket files.
- **No auto-start.** Do not suggest or begin `/fix`, `/scope`, or `/reopen` after showing the table.
- **Epic filter is optional.** Absent argument shows all epics.

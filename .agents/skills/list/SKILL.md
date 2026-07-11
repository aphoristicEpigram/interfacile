---
name: list
description: Show open tickets, their blocking/unblocked state, and what is ready to work on. A convenience entry point for the ticket hygiene tooling. Accepts an optional epic filter (e.g. EM-E018) or "all" (default). Use when the user says "list tickets", "what's ready", "show open tickets", or "list EM-E018".
---

# List Tickets

`list` surfaces open tickets, their readiness, and blocking state. It is a read-only convenience command — it does not modify any files, open any tickets, or start any work.

## Quick start

1. Accept an optional argument: an epic ID (e.g. `EM-E018`), `all` (default), or `--health`.
2. Run lint first to surface any hygiene errors.
3. Run the readiness tool to find unblocked tickets.
4. Display the results grouped by epic.
5. If `--health` was passed, append the health table (see Step 4b).
6. Halt. Do not auto-open or scope any ticket.

## Workflow steps

### Step 1 — Parse the argument

If an argument is provided:
- If it matches the pattern `EM-ENNNN` (epic ID), apply it as a filter: show only that epic's tickets.
- If it is `all` or absent, show all epics.
- If it matches `EM-NNNN` (ticket ID, not epic ID), say:

  ```
  /list takes an epic ID (e.g. EM-E018) or "all", not a ticket ID.
  To see a single ticket, read tickets/<epic>/open/<filename> directly.
  ```

  **HALT.**

### Step 2 — Run lint

```bash
python scripts/ticket_hygiene/ticket.py lint
```

If lint reports errors, display them before the ticket list:

```
Hygiene errors (fix before working on affected tickets):
  <lint output>
```

Do not abort — continue to the ticket list even if lint errors are present.

### Step 3 — Show unblocked tickets

```bash
python scripts/ticket_hygiene/show_path.py --ready
```

Capture the output. If the script does not exist or exits non-zero, note the error and continue.

### Step 4 — Run stats (if available)

```bash
python scripts/ticket_hygiene/ticket.py stats
```

If the command is unavailable or exits non-zero, skip silently.

### Step 4b — Health table (only if `--health` was passed)

```bash
python scripts/ticket_hygiene/ticket.py health [--epic <EPIC>]
```

Apply the epic filter if one was also given. Append the output after the stats block under a `Health` header:

```
Health (age & blocking duration):
  <output from ticket.py health>
```

If the command exits non-zero, note the error and continue.

### Step 5 — Display the summary

Output the following format, substituting real data. Apply the epic filter if one was given:

```
Open tickets — <YYYY-MM-DD>

Ready to work on (unblocked):
  EM-XXXX  <title>  [<effort> | <risk>]
  EM-YYYY  <title>  [<effort> | <risk>]

Blocked (open depends_on):
  EM-ZZZZ  <title>  <- waiting on EM-AAAA

Stats: <N> open, <M> unblocked, <epic count> epics
```

Group tickets under their epic when showing more than one epic:

```
Open tickets — <YYYY-MM-DD>

=== EM-E018 — Security and Adversarial ===

  Ready:
    EM-2008  Input validation security defaults  [medium | high]

  Blocked:
    EM-2009  Prompt injection hardening  <- waiting on EM-2008

=== EM-E005 — Rust Production Port ===
  ...

Stats: <N> open, <M> unblocked, <epic count> epics
```

If no tickets are ready, output:

```
No unblocked tickets found. All open tickets are blocked or waiting.
```

**HALT.** Do not offer to open, scope, or fix any ticket.

---

## Non-negotiable rules

- **This skill is read-only.** It does not modify any files.
- **Do not auto-open or scope any ticket** after displaying the list.
- **If lint errors are reported, display them before the ticket list** — do not suppress them.
- **The epic filter is optional.** Absent argument means show all.

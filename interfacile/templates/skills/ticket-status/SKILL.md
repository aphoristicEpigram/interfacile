---
name: ticket-status
description: Report the health of the ticket board. Use when the user asks "where are we", "ticket status", "what's open", "what's blocked", or "is the board clean". Combines lint, the open list, and what's ready to start.
---

# Ticket status

Run, in order:

    interfacile lint
    interfacile tickets
    interfacile ready

Then report, briefly:

- **Hygiene** — lint errors and warnings, each with its one-line fix.
- **Board** — open tickets per epic; call out anything blocked and which
  ticket it waits on.
- **Next up** — the top few from `ready`, in priority order.

Offer to fix lint findings; don't change anything unprompted.

# Example scaffold

A minimal, valid `tickets/` tree you can copy as a starting point. It exists so the
dashboard and hygiene tools have something to render on a fresh repo, and so the
frontmatter schema is demonstrated by example.

```
examples/
  tickets/
    EM-E001-example-epic/
      EM-E001-example-epic.md      # epic charter (index_exempt: true)
      open/
        EM-0001-example-open-ticket.md
      closed/
        EM-0002-example-closed-ticket.md   # includes the closure retro section
  engineering-reference.md         # epic-table markers for update_agents_epic_table.py
```

## Try it

From this kit's root:

```bash
# Point the tools at this example tree and open the dashboard
TICKET_DASHBOARD_REPO="$(pwd)/examples" python scripts/dev/ticket_report_server.py
#   ...or:  python scripts/dev/ticket_report_server.py --repo "$(pwd)/examples"

# Run the hygiene lint against the example tree
TICKET_DASHBOARD_REPO="$(pwd)/examples" python scripts/ticket_hygiene/ticket.py lint

# Generate the index the dashboard/tools expect
TICKET_DASHBOARD_REPO="$(pwd)/examples" \
  python scripts/ticket_hygiene/generate_index.py > examples/tickets/TICKET_INDEX.md
```

## Use as a starting point for a real project

1. Copy `examples/tickets/` to `tickets/` at your project root.
2. Rename the epic dir/id and ticket ids to your scheme.
3. Generate `tickets/TICKET_INDEX.md` (command above, without the `examples` override).
4. Follow `docs/tickets/EM-1148-ticket-creation-standard.md` for the full schema.

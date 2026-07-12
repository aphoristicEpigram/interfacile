---
id: IF-0036
title: Generic ticket flow in the package: CLI commands + scaffolded skills
epic: IF-E006
status: CLOSED
risk: MEDIUM
priority: 1
effort: 1d
created: 2026-07-12
closed: 2026-07-12
updated: 2026-07-12
index_note: the flow is now the package: new/tickets/ready/show/deps/close/lint + scaffolded skills
---

# IF-0036 · Generic ticket flow in the package: CLI commands + scaffolded skills

## Context

The ticket tooling lived in `scripts/ticket_hygiene/` — 5,000+ lines copied
from other projects, full of hardcoded prefixes (`EM-`, `TH-`), known-id
allowlists, and project-specific classifier rules. The agent skills in
`.agents/` were equally bespoke. Nothing about the flow was reusable by a repo
that runs `interfacile init`.

## Approach

Fold the flow into the package itself, driven entirely by each repo's
`.interfacile/config.json`:

- `interfacile/ticket.py` — `new / tickets / ready / show / deps / close /
  lint` as first-class CLI subcommands. Zero dependencies (reuses the server's
  frontmatter parser). No hardcoded ids: the prefix comes from config, else is
  inferred. "Blocked" is derived from `depends_on`, never a status.
- `interfacile/scaffold.py` + `interfacile/templates/` — four generic skills
  (`new-ticket`, `work-ticket`, `close-ticket`, `ticket-status`) and a
  `tickets/README.md` process doc, installed into `.claude/skills/` by
  `interfacile init` / `interfacile skills`, with `{{PREFIX}}` substituted per
  repo.
- Delete `scripts/ticket_hygiene/`, `scripts/git-hooks/`, and `.agents/`.

## Acceptance criteria

- [x] `interfacile init` on a fresh folder installs skills + process doc and a
      working board (verified on a scratch repo, prefix inferred as `DR`).
- [x] Full lifecycle works: `new` (epic as `E1`/`E001`/full id), derived
      blocking, `ready` ordering, `close` refuses while blocked, moves the
      file to `closed/`, stamps dates, and reports what it unblocked.
- [x] `interfacile lint` passes on this repo's own ticket tree.
- [x] `interfacile skills` is idempotent and doubles as the upgrade path.
- [x] No hardcoded prefixes, ticket ids, or per-project rules anywhere in the
      package.

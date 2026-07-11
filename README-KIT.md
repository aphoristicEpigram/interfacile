# Ticket Dashboard Kit

The reusable **mechanics** of the ticket portfolio dashboard + hygiene tooling,
decanted from `clean_paste_lite` so the same workflow can drive multiple projects
from one place. Contains **no tickets** — only the engine and an example scaffold.

## Requirements

- Python 3.11+ (the dashboard server is standard-library only)
- `PyYAML` — the only third-party dependency, used by the hygiene package
  (`pip install pyyaml`)
- `git` on PATH (WIP detection, closure ceremony, rollback, hooks)

## Pointing the tools at a repo

By default the tools target the repo they live in (`tickets/` under the current
directory). To drive **another** project's tickets — the whole point of a shared
control repo — set the target repo root:

```bash
# Environment variable (applies to server + hygiene engine)
export TICKET_DASHBOARD_REPO=/path/to/clean_paste_lite

# ...or per-invocation on the server:
python scripts/dev/ticket_report_server.py --repo /path/to/clean_paste_lite
```

`--repo` overrides `$TICKET_DASHBOARD_REPO`, which overrides the built-in default.

**What honors the target, and what doesn't (by design):**

- **Honor it** — the dashboard server (`ticket_report_server.py`) and the hygiene
  engine (`scripts/ticket_hygiene/*`: lint, audit, index, health, impact, path).
  These *observe and report*, so they can point anywhere.
- **Stay CWD-relative** — the agent skills (`.agents/skills/*`) and git hooks
  (`scripts/git-hooks/*`). These *act inside the project you're working in*, so
  they intentionally operate on the current repo's `tickets/`. Pinning them to a
  fixed repo would misfire when the agent is working elsewhere.

> Heads-up: because the pre-commit hook shells out to `audit_tickets.py`, a
> globally-exported `TICKET_DASHBOARD_REPO` will redirect that scan too. Prefer
> `--repo`/a per-command export when you only want to *look at* another repo.

## What's inside (extract at a repo root — paths are repo-relative)

| Path | Role |
|---|---|
| `scripts/dev/ticket_report_server.py` | Local dashboard server (stdlib only). Honors `--repo` / `$TICKET_DASHBOARD_REPO`. |
| `ticket-dashboard.html` | Bookmarkable launcher; detects the running server and redirects |
| `scripts/ticket_hygiene/` | Hygiene engine: `ticket.py` (CLI), `audit_tickets.py`, `classifier.py` (+ `classifier_rules.yaml`), `generate_index.py`, `link_validator.py`, `show_health.py`, `show_impact.py`, `show_path.py`, `update_agents_epic_table.py`, `_source_scanner.py` |
| `.agents/skills/` | Agent skills: new-ticket, scope, approve, fix, fix-now, close, abandon, reopen, rollback, batch, ceremony, fund, health, list, commit, commit-all, **deprecate-term, fix-ci** (+ shared `references/`) |
| `scripts/git-hooks/` | `pre-commit` (ticket hygiene scan), `commit-msg` + `prepare-commit-msg` (closure-ceremony enforcement/injection) |
| `docs/tickets/` | Process docs: creation standard, closure & epic-closure ceremonies, stale-ticket scanner, dashboard guide |
| `tests/tooling/` | `test_ticket_hygiene.py`, `test_ticket_hygiene_source_scanner.py` |
| `examples/` | A minimal valid `tickets/` tree + epic-table marker file. See `examples/README.md`. |

## Install into a new repo

1. Extract this archive at the target repo root (it recreates the paths above).
2. `pip install pyyaml`.
3. Create your `tickets/` tree — copy `examples/tickets/` as a starting point, or
   follow `docs/tickets/EM-1148-ticket-creation-standard.md`.
4. Add the UI/scan-state files to that repo's `.gitignore`:
   ```
   .ticket-pins.json
   .ticket_hygiene_last_run
   .ticket_hygiene_baseline.json
   ```
5. (Optional) install the git hooks: point `core.hooksPath` at them or symlink
   into `.git/hooks/` — `git config core.hooksPath scripts/git-hooks`.
6. Run the dashboard: `python scripts/dev/ticket_report_server.py`
7. Hygiene CLI: `python scripts/ticket_hygiene/ticket.py lint`
8. Index: `python scripts/ticket_hygiene/generate_index.py > tickets/TICKET_INDEX.md`
9. Tests: `pytest tests/tooling/` (they self-insert `scripts/ticket_hygiene` on the path).

## Path assumptions to know

- The hygiene engine's `TICKETS_DIR` resolves to `$TICKET_DASHBOARD_REPO/tickets`
  (default: `./tickets`) — run hygiene commands from the repo root, or set the var.
- `ticket_report_server.py` reads/writes, under the target root: `tickets/`,
  `.ticket-pins.json`, `.scratchpad.md`, `.todo.md`, and `docs/architecture/adr/`
  (the last three are optional panels).
- `update_agents_epic_table.py` rewrites the epic table between
  `<!-- BEGIN EPIC TABLE -->` / `<!-- END EPIC TABLE -->` in
  `docs/guides/contributing/engineering-reference.md` (see `examples/`). Edit
  `AGENTS_PATH` in that script if you keep the doc elsewhere.

## Deliberately NOT in this kit

- **The tickets themselves** — they stay in each project repo.
- **Per-project state**: `.ticket-pins.json`, `.ticket_hygiene_last_run`,
  `.scratchpad.md`, `.todo.md` (regenerated on use).
- **`fix-ci/ci-fix-log.md`** — a stale per-project running log; the `fix-ci`
  skill itself is included.
- **CleanPaste product code** and its `AGENTS.md` — not required by the mechanics.

# interfacile

One portable engine that turns a folder of markdown tickets into a live
project-portfolio **interface** — burn-up / throughput charts, a priority×risk
matrix, a walkable dependency graph, per-epic breakdowns, and every ticket
rendered from its own markdown. Standard-library Python, no build step: the
dashboard is computed live on each request by scanning `tickets/`.

The goal is **one engine, many interfaces** — a single install drives the
dashboard + hygiene tooling for any number of project repos, each with its own
theme and config, while every project's tickets, scratchpad, and todos stay in
that project's own repo.

## Status

This repo was just consolidated from two diverged forks into a single source of
truth (Phase 0). The engine currently lives at
`scripts/dev/ticket_report_server.py` and still carries some baked-in,
project-specific config (branding, the `EM-` id scheme, epic map, colours) —
these get externalized next.

Roadmap:

1. **Phase 0 — Consolidate** ✅ one repo, v1 base + v2 cosmetics preserved.
2. **Phase 1 — `interfacile.json`** — lift palette, epic map, id prefix,
   branding, and panel order out of the source into a per-repo config; ship the
   blue and violet-neon colour presets.
3. **Phase 2 — Package** — `pyproject.toml` + an `interfacile` command
   (`pipx install -e .`); `interfacile serve --repo PATH`.
4. **Phase 3 — Multi-tenant hub** — `interfacile hub` serves many repos from one
   process with an in-header switcher.
5. **Phase 4 — `interfacile init`** — scaffold config + hooks + skills into a
   consuming repo and register it with the hub.

## Using it today

See [README-KIT.md](README-KIT.md) for the current install-and-run mechanics
(pointing the tools at a target repo via `--repo` / `$TICKET_DASHBOARD_REPO`, the
hygiene CLI, git hooks, and the example `tickets/` tree under `examples/`).

Quick start against the bundled examples:

```bash
python3 scripts/dev/ticket_report_server.py --repo examples --port 8790
# → http://127.0.0.1:8790
```

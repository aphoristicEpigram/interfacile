# interfacile

One portable engine that turns a folder of markdown tickets into a live
project-portfolio **interface** — burn-up / throughput charts, a priority×risk
matrix, a walkable dependency graph, per-epic breakdowns, and every ticket
rendered from its own markdown. Standard-library Python, no build step: the
dashboard is computed live on each request by scanning `tickets/`.

**One engine, many interfaces** — a single install drives the dashboard for any
number of project repos, each with its own prefix, brand, and theme, switchable
from one hub. Every project's tickets, scratchpad, and todos stay in that
project's own repo; only a small `.interfacile.json` describes how its interface
looks.

## Install

```bash
pip install -e .          # from this repo (editable); or `pipx install .`
```

That puts an `interfacile` command on your PATH.

## Use

```bash
# Serve one repo — defaults to the current directory:
cd /path/to/your-repo && interfacile

# ...or point at it explicitly:
interfacile serve --repo /path/to/your-repo

# Serve several repos from one process, with a centered switcher dropdown
# (flag order = switcher order):
interfacile hub --repo /path/to/repo-a --repo /path/to/repo-b --port 8788
```

Each repo it serves needs a `tickets/` folder and (optionally) a
`.interfacile.json` at its root. With no config a repo uses the built-in
defaults; add config to set its prefix, brand, favicon, epics, theme, and port —
see [examples/configs/](examples/configs/).

`python -m interfacile …` works too, and `scripts/dev/ticket_report_server.py`
remains as a back-compat shim.

## Configuring an interface

`.interfacile.json` (hidden dotfile) at a repo root controls that interface.
Two ready-made examples reproduce the current boards:

- [`examples/configs/theyre-here.interfacile.json`](examples/configs/theyre-here.interfacile.json) — `TH-` / violet-neon
- [`examples/configs/clean-paste.interfacile.json`](examples/configs/clean-paste.interfacile.json) — `EM-` / blue

Full field reference: [examples/configs/README.md](examples/configs/README.md).

## Roadmap

1. **Phase 0 — Consolidate** ✅ one repo, v1 base + v2 cosmetics preserved.
2. **Phase 1 — `.interfacile.json`** ✅ prefix, epic map, brand, favicon/icon,
   theme, and port read from a per-repo config; blue + violet-neon presets.
3. **Phase 3 — Multi-interface hub** ✅ `interfacile hub` serves many repos from
   one process with a centered header switcher (cookie-driven).
4. **Phase 2 — Package** ✅ `pyproject.toml` + the `interfacile` command.
5. **Phase 1b — Custom palettes** — let a repo define its own colours in JSON,
   not just pick a preset.
6. **Phase 4 — `interfacile init` + registry** — scaffold a repo's config (and
   hooks/skills) and register it so `interfacile hub` needs no `--repo` flags.

See [README-KIT.md](README-KIT.md) for the hygiene CLI, git hooks, and the
example `tickets/` tree under `examples/`.

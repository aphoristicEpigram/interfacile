# interfacile

One portable engine that turns a folder of markdown tickets into a live
project-portfolio **interface** — burn-up / throughput charts, a priority×risk
matrix, a walkable dependency graph, per-epic breakdowns, and every ticket
rendered from its own markdown. Standard-library Python, no build step: the
dashboard is computed live on each request by scanning `tickets/`.

**One engine, many interfaces.** A single install drives the dashboard for any
number of repos, each with its own prefix, brand, and theme — switchable from
one hub via a top-bar dropdown. Every project's tickets, scratchpad, and todos
stay in that project's own repo; only a small `.interfacile.json` describes how
its interface looks.

interfacile is built with interfacile — this repo has its own `tickets/` and
`.interfacile.json`, so `cd interfacile && interfacile` shows its own board.

## Install

```bash
pip install -e .        # from this repo (editable) — or `pipx install .`
```

That puts an `interfacile` command on your PATH. (Python 3.8+, no dependencies.)

## Instantiate an interface

**A new or existing repo — one command:**

```bash
cd /path/to/your-repo
interfacile init          # writes .interfacile.json, seeds a starter tickets/
                          # tree if none exists, and registers the repo
interfacile               # serve the current directory
```

`init` guesses the id prefix from any tickets already present (e.g. `TH-0004`
→ `TH`), otherwise from the folder name. Edit the generated `.interfacile.json`
to set the brand, favicon, epics, and theme.

**Prefer to hand-write it?** Drop a `.interfacile.json` at the repo root (see
[examples/configs/](examples/configs/)) and run `interfacile`.

**Just want to see it?** Serve the bundled demo (or this repo itself):

```bash
interfacile serve --repo examples     # the EX- demo board
```

Each served repo needs a `tickets/` folder and, optionally, a
`.interfacile.json`. With no config a repo uses the built-in defaults.

## Run a hub (several interfaces, one process)

Register each repo once, then launch with no arguments:

```bash
interfacile register /path/to/repo-a
interfacile register /path/to/repo-b
interfacile list          # show registered interfaces
interfacile hub           # serve them all, switch with the top-bar dropdown
```

Or pass them explicitly (flag order = switcher order):

```bash
interfacile hub --repo /path/to/repo-a --repo /path/to/repo-b
```

The registry lives at `~/.config/interfacile/registry.json`. The hub opens at a
pretty branded loopback URL like `http://interfacile.localhost:8788/` (plain
`http://localhost:8788/` also works).

## Configuring an interface

`.interfacile.json` (a hidden dotfile) at a repo root controls that interface —
`brand` (name, `favicon`, `icon`), `ids` (`prefix`, `digits`), `epics`, `theme`
(`blue` / `violet-neon`, or a custom palette), and `server.port`. Two worked
examples:

- [`examples/configs/theyre-here.interfacile.json`](examples/configs/theyre-here.interfacile.json) — `TH-` / violet-neon
- [`examples/configs/clean-paste.interfacile.json`](examples/configs/clean-paste.interfacile.json) — `EM-` / blue

Full field reference: [examples/configs/README.md](examples/configs/README.md).

## Ticket format & tooling

Tickets are markdown with YAML front-matter (`id`, `title`, `status`, plus
optional `epic`, `risk`, `priority`, `effort`, `depends_on`, dates). One folder
per epic; the `open/` and `closed/` subfolders are for humans — a ticket's real
state is its `status:` field. See a live example under
[examples/tickets/](examples/tickets/).

Beyond the dashboard, the repo also carries the ticket **hygiene** engine
(`scripts/ticket_hygiene/` — lint, audit, index, health) and optional **git
hooks** (`scripts/git-hooks/`); see [docs/tickets/](docs/tickets/) for the
process docs.

## How it works

- `ids.prefix` drives a family of id regexes, so nothing hard-codes a prefix.
- A single transform reskins every HTML response (theme colours, signature
  strip, brand, favicon, tagline) — guarded so a repo with no config pays
  nothing and looks unchanged.
- The hub holds a registry of interfaces; each request activates its interface
  (chosen by an `ifc` cookie) under a lock, so the shared engine stays correct
  on this local single-user server.

## Roadmap

1. **Consolidate** ✅ one repo, from two diverged forks.
2. **`.interfacile.json`** ✅ prefix, epics, brand, favicon, theme, port per repo.
3. **Multi-interface hub** ✅ `interfacile hub` + top-bar switcher.
4. **Package** ✅ `pyproject.toml` + the `interfacile` command.
5. **`init` + registry** ✅ scaffold a repo and register it.
6. **Custom palettes** — let a repo define its own colours in JSON *(next)*.

# interfacile 🧩

> One portable engine that turns a folder of markdown tickets into a live,
> themeable project-portfolio dashboard — many projects, one switchable hub.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

<p align="center">
  <img src="demo.jpg" alt="The interfacile dashboard — KPIs, charts, priority×risk matrix, backlog, dependency chains and epic breakdown" width="840">
</p>

interfacile scans a `tickets/` folder of markdown files and serves a **live**
dashboard — burn-up & throughput charts, a priority×risk matrix, a walkable
dependency graph, per-epic breakdowns, and every ticket rendered from its own
markdown. Standard-library Python, no build step: everything is computed on each
request, so you edit a ticket, refresh, and it's there.

**One engine, many interfaces.** A single install drives the dashboard for any
number of repos — each with its own id prefix, brand, theme, and keyboard
shortcut — switchable from **one hub** via a top-bar dropdown. Every project's
tickets, scratchpad, and todos stay in its own repo; a small `.interfacile.json`
describes how its interface looks.

> interfacile is built with interfacile — this repo tracks its own work in
> [`tickets/`](tickets/), so `cd interfacile && interfacile` shows the board
> pictured above.

## Features

- 📋 **Live markdown tickets** — one folder per epic, YAML front-matter, rendered
  and editable in place. No database, no build step.
- 📈 **Real dashboards** — burn-up, throughput, priority×risk matrix, effort
  remaining, dependency chains, per-epic breakdowns, health KPIs.
- 🛰️ **Multi-interface hub** — serve many repos from one process and switch
  between them with a dropdown or a **per-project keyboard shortcut**.
- 🎨 **14 theme presets + custom palettes** — each interface gets a distinctive
  background so you always know which project you're in. Light & dark automatic.
- 🧰 **Zero dependencies** — standard-library Python 3.8+, one command to install.
- 🔒 **Your data stays put** — tickets/scratchpad/todos live in each project's
  own repo; interfacile only reads them.

## Install

```bash
# from source (PyPI packaging is on the roadmap):
git clone https://github.com/aphoristicEpigram/interfacile
cd interfacile
pipx install .            # recommended — or:  pip install -e .
```

That puts an `interfacile` command on your PATH. Requires Python 3.8+.

## Quick start

**Turn any repo into an interface — one command:**

```bash
cd /path/to/your-repo
interfacile init          # writes .interfacile.json, seeds a starter tickets/
                          # tree if none exists, and registers the repo
interfacile               # serve the current directory → opens your browser
```

`init` guesses the id prefix from existing tickets (e.g. `TH-0004` → `TH`),
otherwise from the folder name. Edit the generated `.interfacile.json` to set the
brand, favicon, epics, theme, and shortcut.

**Just want to look around first?** Serve the bundled demo:

```bash
interfacile serve --repo examples     # a self-contained EX- demo board
```

## Run a hub (many projects, one switcher)

Register each repo once, then launch with no arguments:

```bash
interfacile register /path/to/repo-a
interfacile register /path/to/repo-b
interfacile list          # show registered interfaces
interfacile hub           # serve them all; switch with the dropdown or shortcut keys
```

Or pass them explicitly (flag order = switcher order):

```bash
interfacile hub --repo /path/to/repo-a --repo /path/to/repo-b
```

The registry lives at `~/.config/interfacile/registry.json`. The hub opens at a
tidy branded loopback URL like `http://interfacile.localhost:8788/` (plain
`http://localhost:8788/` works too). Assign each interface a `shortcut` in its
config to jump straight to it from anywhere.

## Configuring an interface

A hidden `.interfacile.json` at a repo root controls that interface:

| Field | What it sets |
|---|---|
| `brand` | `name`, `favicon`, `icon`, `eyebrow`, `tagline` |
| `ids` | `prefix` (drives all id patterns) and `digits` |
| `epics` | per-epic titles + emoji |
| `theme` | a preset name, or a full custom palette |
| `shortcut` | a key that switches to this interface from anywhere |
| `server.port` | default port |

**Themes** — 14 built-in presets, each with its own background:
`blue`, `violet-neon`, `green`, `forest`, `teal`, `cyan`, `indigo`, `violet`,
`rose`, `crimson`, `orange`, `amber`, `lime`, `slate` — or supply your own
palette (a few semantic colours for light and dark). Full field reference and
worked examples: [`examples/configs/`](examples/configs/).

## Ticket format

Tickets are markdown with YAML front-matter; the body is free-form and rendered
when you open the ticket.

```yaml
---
id: TH-0003            # required — <PREFIX>-#### 
title: Streaming API   # required
epic: TH-E001          # groups the ticket under an epic
status: OPEN           # OPEN | CLOSED | WONT_FIX | STANDING
risk: MEDIUM           # HIGH | MEDIUM | LOW      → priority×risk matrix
priority: 2            # 1..N                     → matrix + backlog rank
effort: 2d             # 4h, 2d, 1-2d             → effort/burn-down
depends_on: [TH-0001]  # dependency graph edges
created: 2026-06-05
closed: 2026-06-20     # required when status is CLOSED
---
```

One folder per epic; the `open/` and `closed/` subfolders are for humans — a
ticket's real state is its `status:` field. See a live example under
[`examples/tickets/`](examples/tickets/).

The repo also carries a ticket **hygiene** engine
([`scripts/ticket_hygiene/`](scripts/ticket_hygiene/) — lint, audit, index,
health) and optional **git hooks** ([`scripts/git-hooks/`](scripts/git-hooks/)).

## How it works

- `ids.prefix` drives a family of id regexes, so nothing hard-codes a prefix.
- A single transform reskins every response (theme colours, signature strip,
  brand, favicon) — guarded so a repo with no config pays nothing.
- The hub keeps a registry of interfaces; each request activates its interface
  (chosen by an `ifc` cookie) under a lock — correct for this local, single-user
  server.

## Contributing

Contributions welcome — bug reports, colour presets, docs, features. See
[CONTRIBUTING.md](CONTRIBUTING.md) for dev setup and guidelines. The one rule:
**standard-library only, no runtime dependencies.**

## License

[MIT](LICENSE) © 2026 Andy Wingrave

## Roadmap

Consolidation, per-repo config, the multi-interface hub, packaging, `init` +
registry, and custom palettes are all **shipped**. What's next is tracked on
interfacile's own board (run `interfacile` in this repo, or browse
[`tickets/`](tickets/)): PyPI + CI, ticket creation from the CLI and dashboard, a
portfolio landing page, live reload, and more.

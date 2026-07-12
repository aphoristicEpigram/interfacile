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
tickets stay in its own repo, and everything interfacile needs lives in one
hidden `.interfacile/` folder — a small `config.json` describes how the interface
looks, alongside the (git-ignored) scratchpad, to-do, and pin state.

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

You run `interfacile` from *inside other repos*, so the command has to work from
**any** folder. That means getting it onto your `PATH` — do this **once, ever**.

**Step 1 — get the code.**

```bash
git clone https://github.com/aphoristicEpigram/interfacile
cd interfacile
```

**Step 2 — install it. Pick ONE route.**

*Route A — `pipx`* (cleanest, but only if you already have `pipx`):

```bash
pipx install .
```

*Route B — venv + symlink* (no extra tools needed; works on a stock Mac):

```bash
python3 -m venv venv
./venv/bin/pip install -e .
sudo ln -s "$PWD/venv/bin/interfacile" /usr/local/bin/interfacile
```

**Step 3 — prove it worked.** Move somewhere else on purpose, then run it:

```bash
cd ~
interfacile --version
```

A version number means you're done. `command not found` means Step 2 didn't take
— see **[Troubleshooting](#troubleshooting)**.

> ### The one trap ⚠️
>
> Running `pip install -e .` **with a venv active** installs `interfacile` into
> *that venv only*. Switch to another project (with its own venv) and the command
> vanishes:
>
> ```
> zsh: command not found: interfacile
> ```
>
> This is the single most likely way to get stuck, and it looks like a broken
> install when it isn't. Route B's `ln -s` is what prevents it: the symlink points
> at the venv's launcher script, which hard-codes its own Python in the shebang —
> so it runs correctly from any directory, with any venv active, or none at all.

Requires Python 3.8+. No runtime dependencies.

## Quick start — add a repo to the dashboard

> **Install interfacile first (above).** `interfacile init` is a command *you*
> run inside your project; it is not something your project provides. If it says
> `command not found`, you skipped the install.

**1. Go to the repo you want a dashboard for.**

```bash
cd /path/to/your-repo
```

**2. Set it up.**

```bash
interfacile init
```

That single command does everything:

- writes `.interfacile/config.json`
- seeds a starter `tickets/` tree, if the repo hasn't got one
- adds the `.gitignore` rules
- registers the repo with the hub

It is **safe to re-run** — an existing config, registration, and ignore rule are
all left alone.

**3. Look at it.**

```bash
interfacile          # serves this repo, opens your browser
```

**4. Add it to your hub.** The hub reads the registry **at startup**, so a newly
`init`-ed repo only shows up in the switcher after you **restart** it:

```bash
interfacile hub
```

**5. Make it yours (optional).** Edit the generated `.interfacile/config.json` to
set the brand name, favicon, epics, theme, `shortcut` (give each repo a distinct
key), and `server.port` (give each repo a distinct port). See
[Configuring an interface](#configuring-an-interface).

### What `init` decides for you

`init` guesses the id prefix from existing tickets (e.g. `TH-0004` → `TH`),
otherwise from the folder name. It also appends these lines to the repo's
`.gitignore`, so the config is committed while the state the dashboard writes
(pins, scratchpad, to-do) stays local and private:

```gitignore
.interfacile/*
!.interfacile/config.json
```

**Just want to look around first?** Serve the bundled demo — no setup, no repo:

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

A hidden `.interfacile/config.json` at a repo root controls that interface:

| Field | What it sets |
|---|---|
| `brand` | `name`, `favicon`, `icon`, `eyebrow`, `tagline` |
| `ids` | `prefix` (drives all id patterns) and `digits` |
| `epics` | per-epic titles + emoji |
| `links` | quick links in the header (`emoji`, `title`, `url`) |
| `theme` | a preset name, or a full custom palette |
| `shortcut` | a key that switches to this interface from anywhere |
| `server.port` | default port |

**Quick links** — add a `links` list to put your own buttons in the header, next
to the pin/scratchpad. Each is `{ "emoji", "title", "url" }`; add as many as you
want, in any order. `emoji` is optional (defaults to 🔗) and hovering a button
shows its `title`:

```json
"links": [
  { "emoji": "⚙️", "title": "Backend",         "url": "https://github.com/acme/api" },
  { "emoji": "🚀", "title": "Live application", "url": "https://app.acme.com" }
]
```

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

## Troubleshooting

### `zsh: command not found: interfacile`

interfacile isn't on your `PATH`. Almost always this means it was installed into
**one project's venv** instead of globally (see [The one trap](#the-one-trap-)).

Find out whether it exists at all:

```bash
which -a interfacile
```

**Nothing printed** → it was never installed. Do the [Install](#install) steps.

**It printed a path ending in `/venv/bin/interfacile`** → it's installed, just
trapped inside that venv. Symlink it out, and it'll work everywhere:

```bash
sudo ln -s /full/path/to/interfacile/venv/bin/interfacile /usr/local/bin/interfacile
```

### `command not found: pipx`

You don't have `pipx`, and you don't need it. Use **Route B** in
[Install](#install) instead — venv + symlink, no extra tools.

### My new repo isn't in the hub's switcher

The hub reads the registry **once, at startup**. **Restart `interfacile hub`.**
Confirm the repo actually registered with:

```bash
interfacile list
```

### `interfacile: no repos with a tickets/ folder to serve`

interfacile only serves a repo that has a `tickets/` directory. Run
`interfacile init` in it — that seeds a starter one for you.

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

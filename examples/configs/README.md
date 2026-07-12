# `.interfacile/config.json` — per-repo config

Everything interfacile needs lives in a hidden `.interfacile/` folder at a
consuming repo's root (next to its `tickets/`). The config is
`.interfacile/config.json` — the only committed file in that folder; the pins,
scratchpad, and to-do it writes alongside are git-ignored runtime state.
The engine reads the config on startup; **every field is optional** and falls
back to a sensible default, so a repo with no config file behaves exactly as the
engine's built-in defaults. Nothing about a project's *content* lives here —
tickets stay in the repo. This file only describes how that repo's interface
looks and what id scheme it uses.

> **Moving from an older layout?** The config used to be a flat `.interfacile.json`
> at the repo root. Move it to `.interfacile/config.json` — the root dotfile is no
> longer read.

Two ready-made examples in this folder — copy either into your repo as
`.interfacile/config.json` and change the names:

| File | Shows |
|---|---|
| [`starter.interfacile.json`](starter.interfacile.json) | the minimal useful config: brand, prefix, a preset theme, a shortcut |
| [`custom-theme.interfacile.json`](custom-theme.interfacile.json) | everything: named epics, header links, a full custom palette |

## Fields

### `brand`
| Key | Meaning |
|---|---|
| `name` | The `<h1>` title and the name used across page titles. |
| `favicon` | Emoji rendered into the **browser-tab** icon (an inline SVG). |
| `icon` | Emoji shown as a **mark right after the title** in the header (e.g. *interfacile 🧩*). Defaults to `favicon`. |
| `eyebrow` | The small uppercased kicker above the title. |
| `tagline` | Lead-in of the summary sentence, e.g. *"This project is tracked as **N tickets** across …"*. |

### `ids`
| Key | Meaning |
|---|---|
| `prefix` | Ticket id prefix. Drives **all** the id regexes: tickets match `PREFIX-####`, epics `PREFIX-E###`. Replaces the old hard-coded `EM-`/`TH-`. |
| `digits` | Zero-padded ticket-number width (default `4` → `TH-0001`). |

### `epics`
Map of epic code → `{ "title", "emoji" }`. Both key forms work — the bare code
(`"E001"`) and the full id (`"AA-E001"`).

Titles resolve in this order, so the section is **optional**:

1. what you put here, if anything;
2. otherwise the **epic's own charter** — the `title:` in the front-matter of the
   `PREFIX-E###-*.md` file inside the epic folder;
3. otherwise the epic folder's slug (`AA-E001-launch-readiness` → *Launch
   Readiness*);
4. otherwise the bare code.

Set it anyway if you want emoji, or titles that differ from the charters. The
quickest way to fill it in is to let the tool do it:

```bash
interfacile epics     # wizard: creates the folder + charter, and writes this section
```

```json
"epics": {
  "E001": { "title": "Character Development", "emoji": "🎭" },
  "E002": { "title": "Weekly Blogs",          "emoji": "📅" }
}
```

> The engine ships **no** built-in epic names. It used to, and a repo that left
> `epics` empty would silently inherit them — showing another project's epic
> titles against its own ids. Titles now always come from your repo.

### `links` (optional)
Per-project quick links, rendered as small emoji buttons in the header row next
to the pin/scratchpad. It's a **list**, so add as many as you like, in the order
you want them. Each link is an object with three fields:

| Field | Meaning |
|---|---|
| `emoji` | The glyph on the button. **Optional** — defaults to 🔗 if you leave it out. |
| `title` | Shown as a hover overlay so you can see where the link goes. Falls back to the URL if omitted. |
| `url` | Where the link points (opens in a new tab). **Required** — a link with no `url` is skipped. |

There's no fixed set and nothing is hard-coded: the buttons are exactly the
links you list, in your order.

```json
"links": [
  { "emoji": "⚙️", "title": "Backend",         "url": "https://github.com/acme/api" },
  { "emoji": "🎨", "title": "Frontend",        "url": "https://github.com/acme/web" },
  { "emoji": "🚀", "title": "Live application", "url": "https://app.acme.com" },
  { "emoji": "📈", "title": "Analytics",        "url": "https://analytics.acme.com" },
  {                "title": "Status page",      "url": "https://status.acme.com" }
]
```

The last entry above omits `emoji`, so it renders with the default 🔗. Hover any
button to see its `title`.

### `theme`
Either the **name of a built-in preset**, or an **inline palette object**. Each
preset has a distinctive background — the quickest cue for which interface you're
looking at. Built-ins:

`blue`, `violet-neon`, `green`, `forest`, `teal`, `cyan`, `indigo`, `violet`,
`rose`, `crimson`, `orange`, `amber`, `lime`, `slate`.

A custom theme is a small set of semantic roles for light and dark, plus an
optional top signature strip:

```json
"theme": {
  "name": "my-brand",
  "strip": ["#FF5F15", "#FFFF00", "#39FF14", "#00FFFF", "#E10098"],
  "light": {
    "ground": "#ece7f2", "surface": "#ffffff", "ink": "#14101f",
    "muted": "#675d77", "line": "#dcd5e6", "accent": "#06718a",
    "done": "#2b9e33", "warn": "#bd5410", "wontfix": "#948aa0"
  },
  "dark": {
    "ground": "#100b1a", "surface": "#1a1428", "ink": "#ece8f3",
    "muted": "#948ba6", "line": "#322640", "accent": "#8ff0fb",
    "done": "#46d332", "warn": "#ff8a4d", "wontfix": "#71697f"
  }
}
```

The engine expands these roles into the full CSS-variable set (surface tints,
soft badge backgrounds, borders, shadows) so you only specify the colours that
define the identity. Omit `strip` for no signature strip (the current blue board
has none). Omit `dark` to auto-derive a dark variant.

### `panels` (optional)
An array naming the dashboard sections in the order you want them, e.g.
`["kpis", "burnup", "throughput", "matrix", "deps", "epics"]`. Omit it entirely
to keep the default order and full set. (Exact panel keys are finalised when the
reorder feature lands; the two examples omit this and so render the standard
layout.)

### `shortcut` (optional)
A single key that switches to this interface from anywhere in the hub — press it
(e.g. `"1"`) and the board flips, no mouse needed. It's shown as a keycap in the
switcher menu, and ignored while you're typing in a field. Give each interface a
distinct key (digits are safest).

```json
"shortcut": "1"
```

### `server` (optional)
| Key | Meaning |
|---|---|
| `port` | Default port for this interface. Give each board its own so several can run at once. `--port` on the command line still overrides it. |

## Precedence

`--repo` / CLI flags  ▸  `.interfacile/config.json`  ▸  built-in defaults. A
missing file, a missing section, or a missing key each fall back to the level
below, so you can start with a two-line config and grow it. The built-in defaults
are deliberately generic — no project's brand, prefix, or epic names are baked
into the engine.

## Editing a live config

The config is re-read when the file changes, exactly like tickets are: **save
`config.json`, refresh the page, and it's there.** No restart, even under
`interfacile hub`.

The registry works the same way: a hub launched without explicit `--repo` flags
follows registry edits live, so `interfacile init` / `register` / `unregister`
show up in the switcher on the next refresh.

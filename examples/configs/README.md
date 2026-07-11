# `.interfacile.json` — per-repo config

Drop one `.interfacile.json` (hidden dotfile) at a consuming repo's root (next to
its `tickets/`). A visible `interfacile.json` also works as a fallback, but the
hidden name is preferred so it stays out of the way at the repo root.
The engine reads it on startup; **every field is optional** and falls back to a
sensible default, so a repo with no config file behaves exactly as the engine's
built-in defaults. Nothing about a project's *content* lives here — tickets,
scratchpad, and todos always stay in the repo. This file only describes how that
repo's interface looks and what id scheme it uses.

Two ready-made examples in this folder reproduce the current look of each board:

| File | Reproduces |
|---|---|
| [`theyre-here.interfacile.json`](theyre-here.interfacile.json) | the `TH-` / violet-neon board (port 8788) |
| [`clean-paste.interfacile.json`](clean-paste.interfacile.json) | the `EM-` / blue board (port 8787) |

## Fields

### `brand`
| Key | Meaning |
|---|---|
| `name` | The `<h1>` title and the name used across page titles. |
| `favicon` | Emoji rendered into the **browser-tab** icon (an inline SVG). |
| `icon` | Emoji (or, later, an image path) shown as a **mark beside the title** in the header. Defaults to `favicon`. |
| `eyebrow` | The small uppercased kicker above the title. |
| `tagline` | Lead-in of the summary sentence, e.g. *"This project is tracked as **N tickets** across …"*. |

### `ids`
| Key | Meaning |
|---|---|
| `prefix` | Ticket id prefix. Drives **all** the id regexes: tickets match `PREFIX-####`, epics `PREFIX-E###`. Replaces the old hard-coded `EM-`/`TH-`. |
| `digits` | Zero-padded ticket-number width (default `4` → `TH-0001`). |

### `epics`
Map of epic code (`E###`, the part after `PREFIX-E`) → `{ "title", "emoji" }`.
Unknown codes fall back to the bare code and a default emoji, so this is optional
but it's what makes the epic pages read nicely.

### `theme`
Either the **name of a built-in preset** (`"blue"` or `"violet-neon"`), or an
**inline palette object**. Naming a preset is the zero-risk way to keep today's
look. A custom theme looks like this — a small set of semantic roles for light
and dark, plus the optional top signature strip:

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

### `server` (optional)
| Key | Meaning |
|---|---|
| `port` | Default port for this interface. Give each board its own so several can run at once. `--port` on the command line still overrides it. |

## Precedence

`--repo` / CLI flags  ▸  `interfacile.json`  ▸  built-in defaults. A missing file,
a missing section, or a missing key each fall back to the level below, so you can
start with a two-line config and grow it.

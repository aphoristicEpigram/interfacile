# Ticket Portfolio Dashboard (local)

A local, dependency-free status dashboard for the `tickets/` tree. It scans ticket
frontmatter live, renders a portfolio view (per-epic breakdown, open backlog), and lets
you open, **edit, and save** any ticket's YAML + Markdown from the browser.

- **Script:** [`scripts/dev/ticket_report_server.py`](../../scripts/dev/ticket_report_server.py)
- **Launcher:** `ticket-dashboard.html` at the repo root (git-ignored, personal convenience)
- **Dependencies:** none — Python standard library only

## Run it

```bash
python scripts/dev/ticket_report_server.py          # serves http://127.0.0.1:8787 and opens a browser
python scripts/dev/ticket_report_server.py --port 9000
python scripts/dev/ticket_report_server.py --no-open # don't auto-open a browser
```

Stop with `Ctrl-C`. The root `ticket-dashboard.html` is a bookmarkable launcher: it detects a
running server and redirects, otherwise it shows the start command.

## What it shows

- **Header + KPIs** — total tracked, closed (+ won't-fix), open, epics fully closed, and a
  resolved-percentage meter.
- **Pinned · watching** — bookmark tickets to come back to. Pin/unpin with the 🔖 button at
  the top of any ticket page (or ✕ unpin in the panel). Pins survive closure and live in
  `.ticket-pins.json` at the repo root (git-ignored UI state, never touches ticket files).
- **Work in progress** — every ticket whose file is modified/untracked in the git working
  tree, most recently touched first.
- **Epic breakdown** — one proportional bar per epic (closed / open / won't-fix), sortable by
  remaining work, size, or progress.
- **Open backlog** — remaining tickets grouped by epic. Each ticket links to its detail view;
  `DECISION`/`CONCEPT` tickets are flagged; active session-layer work is highlighted.
- **Regenerate** — re-scans `tickets/` on demand so counts reflect the current working tree,
  including uncommitted edits and brand-new tickets.

## Ticket detail + editing

Clicking a ticket opens `/ticket/<id>`, which renders the frontmatter as a table and the body as
Markdown. **Edit file** swaps in a plain-text editor over the raw file (frontmatter + body);
**Save to disk** (or `⌘/Ctrl+S`) writes it back atomically and reloads.

**Caveat — saves are in place.** The editor writes the file where it already lives. If you change
`status:` (e.g. `OPEN` → `CLOSED`) it will **not** move the file between `open/` and `closed/`, and
it does not add closure ceremony. After a status change, reconcile with the ticket tooling:

```bash
python scripts/ticket_hygiene/generate_index.py > tickets/TICKET_INDEX.md
```

and follow [the closure ceremony](EM-1146-ticket-closure-ceremony.md) for real closes.

## Counting model

The dashboard counts every ticket **file** whose frontmatter `id` matches `EM-####` (including
compound sub-tickets such as `EM-1856-A`). This is a live filesystem scan, so its grand total runs
higher than the generated [`TICKET_INDEX.md`](../../tickets/TICKET_INDEX.md) dashboard, which lists
only primary (non-`index_exempt`) tickets. Open counts agree between the two.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Dashboard shell |
| `GET` | `/api/data` | Live scan as JSON (drives the page and the Regenerate button) |
| `GET` | `/ticket/<id>` | Rendered ticket detail + editor |
| `GET` | `/filter?…` | Cross-epic ticket list. One `status` renders a full-width card grid; several stack open → closed → won't-fix. Extra params: `wip=1`, `pinned=1` |
| `POST` | `/api/save` | Write a ticket file — body `{"id", "content"}`; refuses paths outside `tickets/` |
| `POST` | `/api/pin` | Pin/unpin a ticket — body `{"id", "pinned"}`; state kept in `.ticket-pins.json` |

It binds to `127.0.0.1` only. Because `/api/save` writes to disk, don't expose it beyond localhost.

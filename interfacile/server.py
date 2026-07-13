#!/usr/bin/env python3
"""Local ticket-portfolio dashboard server.

Serves a self-contained status dashboard for the tickets/ tree at
http://localhost:8787. Scans ticket frontmatter live on every request, so
the in-page "Regenerate" button reflects the current state on disk (including
uncommitted edits and brand-new tickets). Clicking a ticket opens a rendered
view of that ticket's YAML frontmatter + Markdown body.

Standard library only -- no third-party dependencies.

    python scripts/dev/ticket_report_server.py            # port 8787
    python scripts/dev/ticket_report_server.py --port 9000
    python scripts/dev/ticket_report_server.py --no-open  # don't auto-open browser
"""

import argparse
import collections
import datetime
import glob
import html
import json
import os
import re
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Target repo to scan. Defaults to the repo this script lives in (two levels up),
# but can point at any project via $TICKET_DASHBOARD_REPO or the --repo flag, so
# one copy of this kit can drive the dashboard for multiple project repos.
REPO_ROOT = os.path.abspath(
    os.environ.get("TICKET_DASHBOARD_REPO")
    or os.path.join(os.path.dirname(__file__), "..", "..")
)
TICKETS_DIR = os.path.join(REPO_ROOT, "tickets")

# Deliberately empty. Epic names belong to an interface, never to the engine —
# a repo gets them from its own .interfacile/config.json, or, failing that, from
# its own epic charters (discover_epic_meta). Seeding these with one project's
# epics silently leaks them into every repo that doesn't list its own.
EPIC_TITLES = {}
EPIC_EMOJI = {}
# --------------------------------------------------------------------------- #
# Per-interface identity. These module-level names are the *live* values the
# rest of the server reads; they start at the built-in defaults below and are
# overwritten from a repo's .interfacile/config.json at startup by apply_config().
# --------------------------------------------------------------------------- #
# These are engine defaults, so they must stay generic: anything project-flavoured
# here becomes the fallback that an under-specified repo silently inherits.
PFX = "TK"                       # ticket id prefix: <PFX>-1234, <PFX>-E001
ID_DIGITS = 4                    # zero-padded ticket-number width
BRAND = "Tickets"                # <h1> / page-title name
FAVICON = "🎟️"                   # browser-tab icon glyph
HEADER_ICON = "🎟️"               # mark shown beside the title (defaults to favicon)
EYEBROW = "Ticket portfolio &middot; engineering program"
TAGLINE = "This project is tracked as"
SERVER_PORT = 8787               # default listen port (config/--port override)
THEME_REMAP = {}                 # canonical(blue)->theme hex map; {} == blue
THEME_STRIP = None               # signature-strip colours, or None
THEME_OVERRIDE_CSS = ""          # custom-palette :root override, or ""
LINKS = []                       # per-project quick links: list of {emoji,title,url}
DOC_RULES = []                   # doc-link rules: list of {prefix,dir} from config
THEME_NAME = "blue"              # active theme's name ("custom" for a palette dict)

# Frozen copies of the built-in defaults. apply_config() falls back to *these*
# for any missing key, so switching interfaces resets cleanly instead of
# inheriting the previously-active interface's brand/prefix/epics/theme.
_DEF = {"pfx": PFX, "digits": ID_DIGITS, "brand": BRAND, "favicon": FAVICON,
        "icon": HEADER_ICON, "eyebrow": EYEBROW, "tagline": TAGLINE, "port": SERVER_PORT}
_DEF_EPIC_TITLES = dict(EPIC_TITLES)
_DEF_EPIC_EMOJI = dict(EPIC_EMOJI)


def make_id_res(prefix, digits=4):
    """Compile the whole family of ticket/epic id regexes for one prefix.

    A sub-ticket extends its parent's id with one more segment: <PFX>-2165 ->
    -A -> -A-iii. "-" and "." both separate segments (<PFX>-0117-B.1 is a child
    of <PFX>-0117-B), and the separator is optional, so the legacy glued form
    (<PFX>-0104A) parses to the same segments as <PFX>-0104-A."""
    p = re.escape(prefix)
    d = r"\d{%d}" % int(digits)
    return {
        "ticket_id":    re.compile(r"^%s-%s" % (p, d)),
        "epic_code":    re.compile(r"%s-E(\d+)" % p),
        "ticket_parts": re.compile(r"^(%s-%s)[-.]?(.*)$" % (p, d)),
        "ticket_split": re.compile(r"^%s-(%s)(?:-([A-Z]+))?$" % (p, d)),
        "dep_id":       re.compile(r"%s-%s(?:-[A-Z]+)?" % (p, d)),
        "sub_id":       re.compile(r"^(%s-%s)-([A-Z]+)$" % (p, d)),
        "ticket_link":  re.compile(r"\b%s-%s(?:-[A-Z]+)?\b" % (p, d)),
        "epic_link":    re.compile(r"\b%s-E(\d{1,3})\b" % p),
        "md_epic":      re.compile(r"^%s-E(\d{1,3})\b" % p),
        "md_ticket":    re.compile(r"^(%s-%s(?:-[A-Z]+)?)\b" % (p, d)),
    }


_IDRE = make_id_res(PFX, ID_DIGITS)
TICKET_ID_RE = _IDRE["ticket_id"]
EPIC_CODE_RE = _IDRE["epic_code"]
TICKET_PARTS_RE = _IDRE["ticket_parts"]
TICKET_SEG_RE = re.compile(r"[-.]")
ROMAN_DIGITS = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


# --------------------------------------------------------------------------- #
# Frontmatter parsing / scanning
# --------------------------------------------------------------------------- #
def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def split_frontmatter(text):
    """Return (list_of_fm_lines, body). Empty list if no frontmatter."""
    if not text.startswith("---"):
        return [], text
    end = text.find("\n---", 3)
    if end == -1:
        return [], text
    fm = text[3:end].lstrip("\n")
    body_start = text.find("\n", end + 1)
    body = text[body_start + 1:] if body_start != -1 else ""
    return fm.split("\n"), body


def frontmatter_scalars(fm_lines):
    """Top-level `key: value` pairs, in order. List blocks fold onto their key."""
    pairs = []
    last = None
    for line in fm_lines:
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            pairs.append([key, val])
            last = pairs[-1]
        elif last is not None and (line.startswith(" ") or line.lstrip().startswith("-")):
            extra = line.strip().lstrip("-").strip()
            if extra:
                last[1] = (last[1] + ", " + extra).lstrip(", ")
    return pairs


def epic_code(epic_field, path):
    m = EPIC_CODE_RE.search(epic_field or "")
    if not m:
        m = EPIC_CODE_RE.search(path)
    return "E" + m.group(1) if m else "E???"


def parse_date(s):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", (s or "").strip().strip('"').strip("'"))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _week_start(d):
    return d - datetime.timedelta(days=d.weekday())  # Monday


def _month_start(d):
    return d.replace(day=1)


def _next_month(d):
    return (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)


def _build_series(created_dates, closed_dates, closed_effort, bucket, step):
    """Bucketed created/closed counts + cumulative + closed-effort hours per bucket."""
    if not created_dates:
        return []
    cmap, xmap, emap = {}, {}, {}
    for d in created_dates:
        k = bucket(d)
        cmap[k] = cmap.get(k, 0) + 1
    for d in closed_dates:
        k = bucket(d)
        xmap[k] = xmap.get(k, 0) + 1
    for d, h in closed_effort:
        k = bucket(d)
        emap[k] = emap.get(k, 0.0) + h
    start = bucket(min(created_dates))
    end = bucket(max(created_dates + closed_dates + [datetime.date.today()]))
    pts = []
    cum_c = cum_x = 0
    w = start
    while w <= end:
        c, x = cmap.get(w, 0), xmap.get(w, 0)
        cum_c += c
        cum_x += x
        pts.append({"period": w.isoformat(), "created": c, "closed": x,
                    "cumCreated": cum_c, "cumClosed": cum_x,
                    "effortClosedH": round(emap.get(w, 0.0), 1)})
        w = step(w)
    return pts


DEP_ID_RE = _IDRE["dep_id"]
SUB_ID_RE = _IDRE["sub_id"]


def _iso(d):
    return d.isoformat() if d else ""


EFFORT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)(?:\s*[-–]\s*(\d+(?:\.\d+)?))?\s*([hd])\b")


def parse_effort(s):
    """Effort string -> hours (1d = 8h). '4h' -> 4, '1.5d' -> 12, '7-10d' -> 68 (midpoint).

    Returns None when the field is missing or unparseable (e.g. template
    placeholders like '{effort}')."""
    m = EFFORT_RE.match((s or "").strip())
    if not m:
        return None
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    val = (lo + hi) / 2.0
    return val * 8 if m.group(3) == "d" else val


def _git_info():
    """Remote URL (https form) + last-commit summary for the header button."""
    try:
        import subprocess

        def run(*args):
            r = subprocess.run(["git"] + list(args), cwd=REPO_ROOT,
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else ""

        url = run("remote", "get-url", "origin")
        if url.startswith("git@"):
            url = "https://" + url[4:].replace(":", "/", 1)
        if url.endswith(".git"):
            url = url[:-4]
        log = run("log", "-1", "--format=%h%x1f%s%x1f%cI")
        h, s, when = (log.split("\x1f") + ["", "", ""])[:3]
        branch = run("rev-parse", "--abbrev-ref", "HEAD")
        ahead = behind = None
        cm = re.match(r"^(\d+)\s+(\d+)$",
                      run("rev-list", "--left-right", "--count",
                          "origin/main...HEAD"))
        if cm:
            behind, ahead = int(cm.group(1)), int(cm.group(2))
        dirty = sum(1 for line in run("status", "--porcelain").splitlines()
                    if line.strip())
        if not url and not h:
            return None
        return {"url": url if url.startswith("http") else "",
                "hash": h, "subject": s, "when": when, "branch": branch,
                "ahead": ahead, "behind": behind, "dirty": dirty}
    except Exception:
        return None


ADR_DIR = os.path.join(REPO_ROOT, "docs", "architecture", "adr")
ADR_LINK_RE = re.compile(r"\bADR-(\d{1,4})\b")


def series_index(prefix, dirpath, kind=None):
    """All docs of one reserved series (ADR-*, PR-*, RFC-*, …) plus
    per-number link targets.

    Returns (records, by_num). records is every <prefix>-*.md under dirpath
    (recursive), newest number first, with title / **Status:** / **Date:**
    parsed from the head of each file. by_num maps int number -> /doc path
    when the number is unambiguous, or "" when several files share it
    (numbering collision: bare-text mentions link to the series index so the
    reader can choose)."""
    recs = []
    file_re = re.compile(r"^%s-(\d+)" % re.escape(prefix))
    pattern = os.path.join(dirpath, "**", prefix + "-*.md")
    for path in sorted(glob.glob(pattern, recursive=True)):
        m = file_re.match(os.path.basename(path))
        if not m:
            continue
        head = read_text(path)[:1500]
        tm = re.search(r"^#\s+%s-\d+\s*[:—–-]*\s*(.*)$" % re.escape(prefix),
                       head, re.M)
        sm = re.search(r"\*\*Status:\*\*\s*([A-Za-z][\w /-]*)", head)
        dm = re.search(r"\*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2})", head)
        recs.append({
            "n": int(m.group(1)),
            "num": prefix + "-" + m.group(1),
            "kind": kind or prefix.lower(),
            "title": (tm.group(1).strip() if tm else os.path.basename(path)),
            "status": (sm.group(1).strip() if sm else ""),
            "date": dm.group(1) if dm else "",
            "path": os.path.relpath(path, REPO_ROOT).replace(os.sep, "/"),
        })
    recs.sort(key=lambda r: (-r["n"], r["path"]))
    by_num = {}
    for r in recs:
        by_num[r["n"]] = "" if r["n"] in by_num else r["path"]
    return recs, by_num


def adr_index():
    """The built-in series: architecture decision records."""
    return series_index("ADR", ADR_DIR, kind="adr")


def doc_series():
    """Every document series the active interface knows about: the built-in
    ADR series plus the config's `documents` rules. Declaring a prefix (PR,
    RFC, …) just means the system picks its numbered docs up — the full ADR
    treatment: autolinked mentions, an index page, the dashboard jump box."""
    out = [{"prefix": "ADR", "dir": os.path.relpath(ADR_DIR, REPO_ROOT),
            "title": "Architecture decision records", "href": "/adrs"}]
    for rule in DOC_RULES:
        out.append({"prefix": rule["prefix"], "dir": rule["dir"],
                    "title": rule.get("title") or rule["prefix"] + " documents",
                    "href": "/docs/" + rule["prefix"]})
    return out


# Per-repo runtime state (pins, scratchpad, to-do) lives in a single hidden
# `.interfacile/` folder at the repo root instead of three loose dotfiles — same
# files, same formats, just tucked out of the way. It's gitignored, so this state
# never dirties a ticket file (which would flag it WIP) or lands in history. The
# folder is created lazily on first write; reads fall back to the old flat
# locations so existing pins/notes survive the move.
STATE_DIR = ".interfacile"


def _state_paths(root):
    d = os.path.join(root, STATE_DIR)
    return (os.path.join(d, "pins.json"),
            {"scratch": os.path.join(d, "scratchpad.md"),
             "todo": os.path.join(d, "todo.md")})


def _legacy_state_paths(root):
    return (os.path.join(root, ".ticket-pins.json"),
            {"scratch": os.path.join(root, ".scratchpad.md"),
             "todo": os.path.join(root, ".todo.md")})


PINS_FILE, NOTE_FILES = _state_paths(REPO_ROOT)
LEGACY_PINS_FILE, LEGACY_NOTE_FILES = _legacy_state_paths(REPO_ROOT)


def _ensure_state_dir():
    os.makedirs(os.path.dirname(PINS_FILE), exist_ok=True)


def load_pins():
    for path in (PINS_FILE, LEGACY_PINS_FILE):
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            return d if isinstance(d, dict) else {}
        except Exception:
            continue
    return {}


def save_pins(pins):
    _ensure_state_dir()
    tmp = PINS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(pins, fh, indent=1, sort_keys=True)
    os.replace(tmp, PINS_FILE)


# Scratch pad / to-do pop-outs: two free-form files in `.interfacile/`. No
# history, no schema — the dashboard just reads and rewrites the whole file, so
# whatever was there greets you on the next run.
def load_note_at(root, which):
    """A note from any repo — the CLI reads roots the server isn't serving."""
    for paths in (_state_paths, _legacy_state_paths):
        try:
            with open(paths(root)[1][which], encoding="utf-8",
                      errors="replace") as fh:
                return fh.read()
        except OSError:
            continue
    return ""


def save_note_at(root, which, content):
    path = _state_paths(root)[1][which]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    os.replace(tmp, path)


def load_note(which):
    return load_note_at(REPO_ROOT, which)


def save_note(which, content):
    save_note_at(REPO_ROOT, which, content)


# ...except the to-do note, which does have a shape: a markdown checkbox list.
# The pop-out panel parses and rewrites it in JS; `interfacile todo` does the
# same from the CLI, so the two agree on the format here rather than each
# guessing. Tolerant on the way in (a bare line is an unchecked item — you can
# paste a list in), strict on the way out (every item is `- [ ] text`).
TODO_RE = re.compile(r"^[-*]\s*\[([ xX])\]\s*(.*)$")


def parse_todo(text):
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = TODO_RE.match(line)
        if m:
            items.append({"done": m.group(1).lower() == "x", "text": m.group(2)})
        else:
            items.append({"done": False, "text": re.sub(r"^[-*]\s+", "", line)})
    return items


def serialize_todo(items):
    return "".join("- [%s] %s\n" % ("x" if i["done"] else " ", i["text"])
                   for i in items)


def load_todo(root):
    return parse_todo(load_note_at(root, "todo"))


def save_todo(root, items):
    save_note_at(root, "todo", serialize_todo(items))


# The scratchpad, by contrast, has no format — it's prose, and it stays prose.
# The one unit it does have is the block: a run of non-blank lines, which is how
# a thought gets written and how it gets read. `interfacile scratch` numbers
# those blocks so one can be pointed at the ticket it became. The id rules for
# that pointer are the repo's, not ours — they live with the id scheme in
# ticket.py; this half only knows where a block starts and stops.
def scratch_blocks(text):
    """(lines, spans) — spans are the [first, last] line index of each block,
    over the keepends line list, so a rewrite can be byte-for-byte exact."""
    lines = text.splitlines(True)
    spans, start = [], None
    for i, line in enumerate(lines):
        if line.strip():
            if start is None:
                start = i
        elif start is not None:
            spans.append((start, i - 1))
            start = None
    if start is not None:
        spans.append((start, len(lines) - 1))
    return lines, spans


def block_text(lines, span):
    return "".join(lines[span[0]:span[1] + 1]).rstrip("\n")


def set_line(lines, i, stem):
    """Rewrite one line's content, keeping its ending — "" at EOF, so a file
    that ended without a newline still does. Every other byte is untouched."""
    eol = lines[i][len(lines[i].rstrip("\r\n")):]
    lines[i] = stem + eol
    return "".join(lines)


def migrate_state(root):
    """One-time tidy-up: move any legacy flat state files into `.interfacile/`.
    Safe — these are the gitignored files the dashboard itself writes, and a move
    only happens when the new slot is free, so newer state is never clobbered."""
    new_pins, new_notes = _state_paths(root)
    old_pins, old_notes = _legacy_state_paths(root)
    pairs = [(old_pins, new_pins)] + [(old_notes[k], new_notes[k]) for k in old_notes]
    for src, dst in pairs:
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
            except OSError:
                pass                 # best-effort; read-fallback still finds it


def _working_tree_ids():
    """Ticket ids whose files are modified/untracked in git — 'active now'."""
    try:
        import subprocess
        out = subprocess.run(["git", "status", "--porcelain", "--", "tickets"],
                             cwd=REPO_ROOT, capture_output=True, text=True,
                             timeout=5)
        if out.returncode != 0:
            return set()
        ids = set()
        for line in out.stdout.splitlines():
            p = line[3:].strip().strip('"')
            if " -> " in p:  # rename: take the new path
                p = p.split(" -> ")[-1].strip().strip('"')
            m = _IDRE["md_ticket"].match(os.path.basename(p))
            if m:
                ids.add(m.group(1))
        return ids
    except Exception:
        return set()


_EPIC_KEY_RE = re.compile(r"(?:[A-Za-z]+-)?(E\d+)$")


def _epic_key(key):
    """Normalise a config `epics` key to the bare code the engine looks up.
    Both "E001" and the full "AA-E001" are accepted — writing the full id is the
    natural thing to do, and it used to be silently ignored."""
    m = _EPIC_KEY_RE.match(str(key).strip())
    return m.group(1) if m else str(key).strip()


def discover_epic_meta(tickets_dir=None, prefix=None):
    """Epic titles taken from the repo's *own* epic charters: {"E001": "Launch"}.

    This is what lets a repo with no `epics` in its config still show real names
    instead of bare codes. The title comes from the charter's front-matter, and
    failing that from the epic folder's slug (TK-E001-launch-readiness -> "Launch
    Readiness"). Config `epics` still wins over anything found here.
    """
    tickets_dir = TICKETS_DIR if tickets_dir is None else tickets_dir
    prefix = PFX if prefix is None else prefix
    code_re = re.compile(r"%s-(E\d+)-?(.*)$" % re.escape(prefix))
    titles = {}
    for d in sorted(glob.glob(os.path.join(tickets_dir, "%s-E*" % prefix))):
        m = code_re.match(os.path.basename(d))
        if not (m and os.path.isdir(d)):
            continue
        code, slug = m.group(1), m.group(2)
        title = ""
        for charter in sorted(glob.glob(os.path.join(d, "*.md"))):
            fm_lines, _ = split_frontmatter(read_text(charter))
            fm = {k: v for k, v in frontmatter_scalars(fm_lines)}
            if fm.get("id", "") == "%s-%s" % (prefix, code):
                title = fm.get("title", "").strip()
                break
        titles[code] = title or (slug.replace("-", " ").title() if slug else code)
    return titles


def _epic_record(code, found_titles):
    """A zeroed epic. Title: config wins, else the repo's own charter, else the code."""
    return {
        "id": code,
        "title": EPIC_TITLES.get(code) or found_titles.get(code) or code,
        "emoji": EPIC_EMOJI.get(code, "🎟️"),
        "open": 0, "closed": 0, "wf": 0, "standing": 0,
        "openTickets": [], "closedTickets": [], "wfTickets": [],
        "standingTickets": [],
        "lastCreated": None, "lastClosed": None,
        "effortOpenH": 0.0, "effortDoneH": 0.0,
    }


# The pocket's three windows, in one place: the counts (scan), the pages they
# link to (/filter), and the label the bar prints all read from here, so "7 days"
# can never mean one thing in the badge and another in the list behind it.
POCKET_WINDOWS = (("today", 0, "today"), ("week", 6, "7 days"),
                  ("month", 29, "30 days"))


def pocket_counts(tickets):
    """created/closed within each rolling window, counted on the server — the
    top bar shows this on pages that never load the board."""
    today = datetime.date.today()
    out = {}
    for key, days, _lab in POCKET_WINDOWS:
        since = _iso(today - datetime.timedelta(days=days))
        out[key] = {
            "created": sum(1 for t in tickets
                           if t.get("created") and t["created"] >= since),
            "closed": sum(1 for t in tickets
                          if t.get("closed") and t["closed"] >= since)}
    return out


def scan():
    """Walk tickets/, aggregate per-epic counts, and build a weekly time-series."""
    epics = {}
    found_titles = discover_epic_meta()   # charter-derived; config still wins
    id_index = {}
    all_created = []
    all_closed = []
    closed_effort = []           # (close date, effort hours) for effort velocity
    open_by_id = {}              # open-ticket dicts, for dependency resolution
    dep_edges = []               # (blocker id, blocked id) from depends_on/blocks
    node_meta = {}               # every ticket, for the dependency explorer
    all_tix = []                 # every ticket, for the recently-created panel
    today = datetime.date.today()
    four_weeks_ago = today - datetime.timedelta(days=28)
    effort_open_h = effort_done_h = effort_last4_h = 0.0
    unestimated_open = 0
    wip_ids = _working_tree_ids()
    pins = load_pins()
    pinned_list = []             # (pin timestamp, ticket record)
    wip_list = []                # (file mtime, ticket record)
    last_worked = {"mtime": 0.0}
    for path in glob.glob(os.path.join(TICKETS_DIR, "**", "*.md"), recursive=True):
        fm_lines, _ = split_frontmatter(read_text(path))
        if not fm_lines:
            continue
        fm = {k: v for k, v in frontmatter_scalars(fm_lines)}
        tid = fm.get("id", "")
        if not TICKET_ID_RE.match(tid):
            continue  # skip epic-definition + auxiliary docs
        id_index[tid] = path
        code = epic_code(fm.get("epic", ""), path)
        status = fm.get("status", "").upper()
        title = fm.get("title", tid).strip().strip('"')
        cdate = parse_date(fm.get("created"))
        xdate = parse_date(fm.get("closed"))
        risk = fm.get("risk", "").strip().strip('"').upper()
        priority = fm.get("priority", "").strip().strip('"')
        effort = fm.get("effort", "").strip().strip('"')
        effort_h = parse_effort(effort)
        meta = {"risk": risk if risk in ("LOW", "MEDIUM", "HIGH") else "",
                "priority": priority if priority.isdigit() else "",
                "effort": effort if effort_h is not None else "",
                "effortH": effort_h, "epic": code, "wip": tid in wip_ids,
                "pinned": tid in pins,
                "slug": os.path.basename(path)[:-3]}   # id-title filename, for "copy ids"
        node_meta[tid] = {"id": tid, "title": title, "status": status,
                          "created": _iso(cdate), "closed": _iso(xdate)}
        node_meta[tid].update(meta)
        try:
            mt = os.path.getmtime(path)
        except OSError:
            mt = 0.0
        if mt > last_worked["mtime"]:
            last_worked = {"mtime": mt, "ticket": tid, "title": title,
                           "epic": code, "status": status}
        if tid in pins or tid in wip_ids:
            rec = {"id": tid, "title": title, "status": status,
                   "created": _iso(cdate), "closed": _iso(xdate)}
            rec.update(meta)
            if tid in pins:
                pinned_list.append((str(pins[tid]), rec))
            if tid in wip_ids:
                wip_list.append((mt, dict(rec, when=datetime.datetime
                                          .fromtimestamp(mt).isoformat())))
        ep = epics.setdefault(code, _epic_record(code, found_titles))
        for field in ("depends_on", "blocks"):
            for dep in DEP_ID_RE.findall(fm.get(field, "")):
                if dep != tid:
                    dep_edges.append((dep, tid) if field == "depends_on" else (tid, dep))
        if status == "OPEN":
            ep["open"] += 1
            t = {"id": tid, "title": title, "created": _iso(cdate),
                 "blocked": False, "unblocks": 0}
            t.update(meta)
            ep["openTickets"].append(t)
            open_by_id[tid] = t
            if effort_h is not None:
                ep["effortOpenH"] += effort_h
                effort_open_h += effort_h
            else:
                unestimated_open += 1
        elif status == "CLOSED":
            ep["closed"] += 1
            t = {"id": tid, "title": title, "closed": _iso(xdate), "created": _iso(cdate)}
            t.update(meta)
            ep["closedTickets"].append(t)
            if effort_h is not None:
                ep["effortDoneH"] += effort_h
                effort_done_h += effort_h
                if xdate and xdate >= four_weeks_ago:
                    effort_last4_h += effort_h
        elif status == "WONT_FIX":
            ep["wf"] += 1
            t = {"id": tid, "title": title, "created": _iso(cdate)}
            t.update(meta)
            ep["wfTickets"].append(t)
        elif status == "STANDING":
            ep["standing"] += 1
            t = {"id": tid, "title": title, "created": _iso(cdate)}
            t.update(meta)
            ep["standingTickets"].append(t)

        # Every ticket lands here — it is what the id index and the pocket counts
        # are built from, and a ticket with no created date is still a ticket
        # (the recently-created panel, which wants one, filters on it below).
        all_tix.append({"id": tid, "title": title, "created": _iso(cdate),
                        "closed": _iso(xdate), "status": status,
                        "risk": meta["risk"], "priority": meta["priority"],
                        "effort": meta["effort"], "effortH": effort_h,
                        "epic": code})
        if cdate:
            all_created.append(cdate)
            if ep["lastCreated"] is None or cdate > ep["lastCreated"]:
                ep["lastCreated"] = cdate
        if xdate and status == "CLOSED":
            all_closed.append(xdate)
            closed_effort.append((xdate, effort_h or 0.0))
            if ep["lastClosed"] is None or xdate > ep["lastClosed"]:
                ep["lastClosed"] = xdate

    # Epics that exist as a folder + charter but have no tickets yet (a freshly
    # created one). Without this they'd be invisible until their first ticket
    # landed, which makes `interfacile epics` look like it did nothing.
    for code in found_titles:
        epics.setdefault(code, _epic_record(code, found_titles))

    # Dependency resolution: an open ticket is blocked when an open blocker
    # precedes it; unblocks = how many open tickets it is holding up.
    for blocker, blocked in dep_edges:
        b = open_by_id.get(blocker)
        t = open_by_id.get(blocked)
        if b is not None and t is not None:
            t["blocked"] = True
            b["unblocks"] += 1

    recent_closed = []
    for ep in epics.values():
        # Open: oldest first (undated last). Closed: most recently closed first.
        ep["openTickets"].sort(key=lambda t: t["created"] or "9999-99-99")
        ep["closedTickets"].sort(key=lambda t: t["closed"], reverse=True)
        ep["wfTickets"].sort(key=lambda t: t["created"] or "9999-99-99")
        ep["standingTickets"].sort(key=lambda t: t["created"] or "9999-99-99")
        recent_closed.extend(ep["closedTickets"])
        ep["lastCreated"] = _iso(ep["lastCreated"])
        ep["lastClosed"] = _iso(ep["lastClosed"])
        ep["effortOpenH"] = round(ep["effortOpenH"], 1)
        ep["effortDoneH"] = round(ep["effortDoneH"], 1)
    recent_closed.sort(key=lambda t: t["closed"], reverse=True)
    recent_closed = recent_closed[:12]
    # Recently created: highest ticket id first (ids are chronological);
    # compound sub-tickets keep parent -> A -> B order within a number.
    def _id_key(tid):
        m = _IDRE["ticket_split"].match(tid)
        return (-int(m.group(1)), m.group(2) or "") if m else (0, tid)
    recent_created = sorted((t for t in all_tix if t["status"] == "OPEN"),
                            key=lambda t: _id_key(t["id"]))[:12]

    series = {
        "day": {"unit": "day", "points": _build_series(
            all_created, all_closed, closed_effort,
            lambda d: d, lambda d: d + datetime.timedelta(days=1))},
        "week": {"unit": "week", "points": _build_series(
            all_created, all_closed, closed_effort,
            _week_start, lambda d: d + datetime.timedelta(days=7))},
        "month": {"unit": "month", "points": _build_series(
            all_created, all_closed, closed_effort,
            _month_start, _next_month)},
    }
    points = series["week"]["points"]

    totals = {"open": 0, "closed": 0, "wf": 0, "standing": 0}
    for ep in epics.values():
        for k in totals:
            totals[k] += ep[k]
    totals["total"] = sum(totals.values())

    # Planning metrics over the open set.
    open_ages = sorted((today - parse_date(t["created"])).days
                       for t in open_by_id.values() if t["created"])
    n_ages = len(open_ages)
    median_age = (open_ages[n_ages // 2] if n_ages % 2
                  else (open_ages[n_ages // 2 - 1] + open_ages[n_ages // 2]) / 2.0) if n_ages else 0
    stale_open = sum(1 for a in open_ages if a > 30)
    quick_wins = sum(1 for t in open_by_id.values()
                     if t["effortH"] is not None and t["effortH"] <= 4 and not t["blocked"])
    blocked_open = sum(1 for t in open_by_id.values() if t["blocked"])
    p1_open = sum(1 for t in open_by_id.values() if t["priority"] == "1")
    high_risk_open = sum(1 for t in open_by_id.values() if t["risk"] == "HIGH")
    wip_open = sum(1 for t in open_by_id.values() if t.get("wip"))

    # Open-ticket age buckets, with created-date windows for /filter links.
    aging = []
    for label, lo, hi in (("≤7d", 0, 7), ("8–30d", 8, 30),
                          ("31–90d", 31, 90), (">90d", 91, None)):
        aging.append({
            "label": label,
            "n": sum(1 for a in open_ages if a >= lo and (hi is None or a <= hi)),
            "from": _iso(today - datetime.timedelta(days=hi)) if hi is not None else "",
            "to": _iso(today - datetime.timedelta(days=lo)) if lo else "",
        })

    # Open->open dependency edges for the dashboard's chain graph (deduped).
    open_edges = sorted({(b, t) for b, t in dep_edges
                         if b in open_by_id and t in open_by_id})
    # Every dependency edge, any status, for /deps -- that graph has to show
    # closed history upstream, not just what is still open. Edges pointing at an
    # id with no ticket file are dropped rather than drawn as ghost nodes.
    all_edges = sorted({(b, t) for b, t in dep_edges
                        if b in node_meta and t in node_meta})
    dep_nodes = {i: node_meta[i] for e in all_edges for i in e}

    # Like-for-like comparison windows: [prev, cur] over equal elapsed spans.
    def _window(a, b):
        return {
            "closed": sum(1 for d in all_closed if a <= d <= b),
            "created": sum(1 for d in all_created if a <= d <= b),
            "effortH": round(sum(h for d, h in closed_effort if a <= d <= b), 1),
        }

    def _pair(a0, b0, a1, b1):
        prev, cur = _window(a0, b0), _window(a1, b1)
        return {k: [prev[k], cur[k]] for k in prev}

    one = datetime.timedelta(days=1)
    wk_start = _week_start(today)
    elapsed = (today - wk_start).days
    mo_start = _month_start(today)
    prev_mo_start = _month_start(mo_start - one)
    prev_mo_same = min(prev_mo_start + datetime.timedelta(days=today.day - 1),
                       mo_start - one)
    def _with_window(pair, a1, b1):
        pair["curFrom"], pair["curTo"] = _iso(a1), _iso(b1)
        return pair

    compare = {
        "wtdDays": elapsed + 1,
        "mtdDays": today.day,
        "wtd": _with_window(_pair(wk_start - datetime.timedelta(days=7),
                                  wk_start - datetime.timedelta(days=7 - elapsed),
                                  wk_start, today), wk_start, today),
        "mtd": _with_window(_pair(prev_mo_start, prev_mo_same, mo_start, today),
                            mo_start, today),
        "r7": _with_window(_pair(today - datetime.timedelta(days=13),
                                 today - datetime.timedelta(days=7),
                                 today - datetime.timedelta(days=6), today),
                           today - datetime.timedelta(days=6), today),
    }

    span = len(points) or 1
    total_created = sum(p["created"] for p in points)
    total_closed = sum(p["closed"] for p in points)
    last4 = points[-4:]
    resolved = totals["closed"] + totals["wf"]
    health = {
        "completionRate": (resolved / totals["total"] * 100) if totals["total"] else 0,
        "avgCreatedPerWeek": total_created / span,
        "avgClosedPerWeek": total_closed / span,
        "last4Created": sum(p["created"] for p in last4),
        "last4Closed": sum(p["closed"] for p in last4),
        "weeks": span,
        "peakClosed": max((p["closed"] for p in points), default=0),
        "effortOpenH": effort_open_h,
        "effortDoneH": effort_done_h,
        "effortLast4H": effort_last4_h,
        "unestimatedOpen": unestimated_open,
        "openAgeMedianDays": median_age,
        "staleOpen": stale_open,
        "quickWins": quick_wins,
        "blockedOpen": blocked_open,
        "p1Open": p1_open,
        "highRiskOpen": high_risk_open,
        "wipOpen": wip_open,
        "aging": aging,
        "compare": compare,
    }

    # Doc pins: a pin key containing a slash is a repo-relative markdown path,
    # so any doc (ADR, blog draft, spec) can sit in the pinned panel too.
    for key, stamp in pins.items():
        if "/" not in key:
            continue
        full = os.path.realpath(os.path.join(REPO_ROOT, key))
        if not (full.startswith(os.path.realpath(REPO_ROOT) + os.sep)
                and full.endswith(".md") and os.path.isfile(full)):
            continue
        tm = re.search(r"^#\s+(.*)$", read_text(full)[:2000], re.M)
        name = os.path.basename(key)[:-3]
        pinned_list.append((str(stamp), {
            "id": name, "title": (tm.group(1).strip() if tm else name),
            "status": "DOC", "doc": key, "slug": name}))

    return {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": os.path.relpath(TICKETS_DIR, REPO_ROOT),
        "totals": totals,
        "health": health,
        "series": series,
        "recentClosed": recent_closed,
        "recentCreated": recent_created,
        "pinned": [r for _, r in sorted(pinned_list, key=lambda x: x[0],
                                        reverse=True)],
        "wip": [r for _, r in sorted(wip_list, key=lambda x: x[0],
                                     reverse=True)],
        "depEdges": [list(e) for e in open_edges],
        "depGraph": {"nodes": dep_nodes, "edges": [list(e) for e in all_edges]},
        "lastWorked": ({"ticket": last_worked["ticket"],
                        "title": last_worked["title"],
                        "epic": last_worked["epic"],
                        "status": last_worked["status"],
                        "wip": last_worked["ticket"] in wip_ids,
                        "when": datetime.datetime.fromtimestamp(
                            last_worked["mtime"]).isoformat()}
                       if last_worked["mtime"] else None),
        "git": _git_info(),
        # id -> status/title for every ticket. Captured notes (to-do items,
        # scratchpad blocks) store an id and nothing else, so whatever shows
        # them resolves the status here, live, and can never go stale.
        "index": {t["id"]: {"status": t["status"], "title": t["title"]}
                  for t in all_tix},
        "pocket": pocket_counts(all_tix),
        # Every document series' records (ADRs plus configured ones) — the
        # key predates the generic series and the dashboard JS reads it.
        "adrs": [r for s in doc_series()
                 for r in series_index(s["prefix"],
                                       os.path.join(REPO_ROOT, s["dir"]))[0]],
        "epics": sorted(epics.values(), key=lambda e: e["id"]),
    }, id_index


# --------------------------------------------------------------------------- #
# Minimal Markdown renderer (for the ticket detail view)
# --------------------------------------------------------------------------- #
def _inline(text):
    t = html.escape(text)
    t = re.sub(r"`([^`]+)`", lambda m: "<code>" + m.group(1) + "</code>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               lambda m: '<a href="%s" target="_blank" rel="noopener">%s</a>' % (m.group(2), m.group(1)),
               t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*\s][^*]*?)\*(?!\*)", r"<em>\1</em>", t)
    return t


def _split_row(line):
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def md_to_html(md):
    lines = md.split("\n")
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(html.escape(lines[i]))
                i += 1
            i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            continue

        if (line.lstrip().startswith("|") and i + 1 < n
                and "-" in lines[i + 1]
                and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1])):
            header = _split_row(line)
            i += 2
            body_rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                body_rows.append(_split_row(lines[i]))
                i += 1
            th = "".join("<th>" + _inline(c) + "</th>" for c in header)
            trs = ""
            for row in body_rows:
                tds = "".join("<td>" + _inline(c) + "</td>" for c in row)
                trs += "<tr>" + tds + "</tr>"
            out.append('<div class="tbl"><table><thead><tr>' + th
                       + "</tr></thead><tbody>" + trs + "</tbody></table></div>")
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, _inline(m.group(2)), lvl))
            i += 1
            continue

        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(_inline(re.sub(r"^\s*>\s?", "", lines[i])))
                i += 1
            out.append("<blockquote>" + "<br>".join(buf) + "</blockquote>")
            continue

        if re.match(r"^\s*[-*+]\s+", line):
            buf = []
            while i < n and re.match(r"^\s*[-*+]\s+", lines[i]):
                buf.append("<li>" + _inline(re.sub(r"^\s*[-*+]\s+", "", lines[i])) + "</li>")
                i += 1
            out.append("<ul>" + "".join(buf) + "</ul>")
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            buf = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                buf.append("<li>" + _inline(re.sub(r"^\s*\d+\.\s+", "", lines[i])) + "</li>")
                i += 1
            out.append("<ol>" + "".join(buf) + "</ol>")
            continue

        if stripped == "":
            i += 1
            continue

        buf = [line]
        i += 1
        while (i < n and lines[i].strip() != ""
               and not re.match(r"^\s*([-*+]\s+|\d+\.\s+|#{1,6}\s+|>|\|)", lines[i])
               and not lines[i].strip().startswith("```")):
            buf.append(lines[i])
            i += 1
        out.append("<p>" + _inline(" ".join(buf)) + "</p>")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Ticket / epic id autolinking (applied to already-rendered HTML)
# --------------------------------------------------------------------------- #
TICKET_LINK_RE = _IDRE["ticket_link"]
EPIC_LINK_RE = _IDRE["epic_link"]
_SKIP_SEGMENT_RE = re.compile(r"(<a\b.*?</a>|<pre>.*?</pre>)", re.S)


def doc_rule_index():
    """{prefix: {number: /doc path or "" on numbering collision}} for every
    configured document rule — the generic cousin of adr_index(). Scans live,
    like everything else, so a new PR-002.md links on the next request."""
    out = {}
    for rule in DOC_RULES:
        by_num = {}
        pattern = os.path.join(REPO_ROOT, rule["dir"], "**",
                               rule["prefix"] + "-*.md")
        for path in sorted(glob.glob(pattern, recursive=True)):
            m = re.match(r"^%s-(\d+)\b" % rule["prefix"], os.path.basename(path))
            if not m:
                continue
            n = int(m.group(1))
            rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
            by_num[n] = "" if n in by_num else rel
        out[rule["prefix"]] = by_num
    return out


def autolink_ids(html_text, known_ids=None, known_epics=None, adrs=None):
    """Hyperlink ticket ids, epic codes, ADR-### numbers, and any configured
    document rules (PR-###, RFC-### …) in rendered HTML.

    Skips existing anchors and <pre> blocks. When known sets are supplied,
    only ids that resolve to a real page are linked, so links never 404."""
    def _sub_epic(m):
        code = "E" + m.group(1)
        if known_epics is not None and code not in known_epics:
            return m.group(0)
        return '<a href="/epic/%s">%s</a>' % (code, m.group(0))

    def _sub_ticket(m):
        tid = m.group(0)
        if known_ids is not None and tid not in known_ids:
            return tid
        return '<a href="/ticket/%s">%s</a>' % (tid, tid)

    def _sub_adr(m):
        target = adrs.get(int(m.group(1)))
        if target is None:
            return m.group(0)
        if target == "":  # number shared by several files — send to the index
            return '<a href="/adrs">%s</a>' % m.group(0)
        return '<a href="/doc/%s">%s</a>' % (target, m.group(0))

    rules = doc_rule_index() if DOC_RULES else {}

    def _sub_doc(m, pfx, by_num):
        target = by_num.get(int(m.group(1)))
        if target is None:   # unknown number: leave as plain text
            return m.group(0)
        if target == "":     # number shared by several files — send to the index
            return '<a href="/docs/%s">%s</a>' % (pfx, m.group(0))
        return '<a href="/doc/%s">%s</a>' % (target, m.group(0))

    out = []
    for part in _SKIP_SEGMENT_RE.split(html_text):
        if part.startswith("<a") or part.startswith("<pre"):
            out.append(part)
        else:
            part = EPIC_LINK_RE.sub(_sub_epic, part)
            if adrs:
                part = ADR_LINK_RE.sub(_sub_adr, part)
            for pfx, by_num in rules.items():
                part = re.sub(r"\b%s-(\d+)\b" % pfx,
                              lambda m, p=pfx, b=by_num: _sub_doc(m, p, b), part)
            out.append(TICKET_LINK_RE.sub(_sub_ticket, part))
    return "".join(out)


def _effort_bucket(h):
    """Effort-hours -> filter bucket, mirroring the dashboard's effBucket()."""
    if h is None:
        return "none"
    return "s" if h <= 4 else ("m" if h <= 16 else "l")


# Relative .md links in ticket bodies (e.g. ../EM-E005-rust-production-port.md)
# would otherwise resolve against the server root and 404. Rewrite them to the
# canonical /epic/ or /ticket/ page, or to /doc/<repo-path> for other markdown.
_MD_ANCHOR_RE = re.compile(r'<a href="([^"]+)" target="_blank" rel="noopener">')
_MD_EPIC_FILE_RE = _IDRE["md_epic"]
_MD_TICKET_FILE_RE = _IDRE["md_ticket"]


def rewrite_md_links(html_text, src_dir, known_ids=None, known_epics=None):
    """Point relative .md hrefs in rendered HTML at pages this server serves."""
    def _target(href):
        if href.startswith(("http://", "https://", "mailto:", "#", "/")):
            return None
        path = href.partition("#")[0]
        if not path.endswith(".md"):
            return None
        base = os.path.basename(path)
        m = _MD_EPIC_FILE_RE.match(base)
        if m and (known_epics is None or "E" + m.group(1) in known_epics):
            return "/epic/E" + m.group(1)
        m = _MD_TICKET_FILE_RE.match(base)
        if m and (known_ids is None or m.group(1) in known_ids):
            return "/ticket/" + m.group(1)
        full = os.path.realpath(os.path.join(src_dir, path))
        if full.startswith(os.path.realpath(REPO_ROOT) + os.sep) and os.path.isfile(full):
            return "/doc/" + os.path.relpath(full, REPO_ROOT)
        return None

    def _sub(m):
        new = _target(m.group(1))
        # internal pages open in the same tab; unresolved links stay as-is
        return '<a href="%s">' % html.escape(new, quote=True) if new else m.group(0)

    return _MD_ANCHOR_RE.sub(_sub, html_text)


# --------------------------------------------------------------------------- #
# Ticket detail page
# --------------------------------------------------------------------------- #
TICKET_CSS = """
:root{--bg:#e9edf1;--surface:#fff;--surface2:#f3f6f9;--ink:#101720;--ink2:#3a4652;
--mut:#5d6a77;--line:#d6dde5;--accent:#1c3bb3;--done:#2f9e5b;--wf:#8a94a0;--warn:#b3781a;
--font:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
--mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;}
@media (prefers-color-scheme:dark){:root{--bg:#0b0f15;--surface:#141b25;--surface2:#0f1620;
--ink:#e8edf3;--ink2:#c2ccd6;--mut:#8b98a6;--line:#26313e;--accent:#a9bdff;--done:#48c483;
--wf:#69747f;--warn:#d9a441;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font);line-height:1.6;
-webkit-font-smoothing:antialiased;padding:32px 20px 80px}
.wrap{max-width:820px;margin:0 auto}
a{color:var(--accent)}
.back{font-family:var(--mono);font-size:.78rem;text-decoration:none;display:inline-block;margin-bottom:20px}
.back:hover{text-decoration:underline}
.tid{font-family:var(--mono);font-size:.82rem;color:var(--accent);font-weight:600}
h1.title{font-size:1.7rem;line-height:1.2;letter-spacing:-.02em;margin:.2em 0 .5em;text-wrap:balance}
.badge{font-family:var(--mono);font-size:.66rem;letter-spacing:.05em;text-transform:uppercase;
padding:3px 9px;border-radius:5px;vertical-align:middle}
.b-open{background:#dbe2fb;color:#1c3bb3}.b-closed{background:#d3ecdc;color:#2f9e5b}
.b-wf{background:#e2e6ea;color:#5d6a77}
@media (prefers-color-scheme:dark){.b-open{background:#1c2740;color:#a9bdff}
.b-closed{background:#16311f;color:#48c483}.b-wf{background:#222c37;color:#8b98a6}}
.fm{background:var(--surface);border:1px solid var(--line);border-radius:9px;overflow:hidden;margin:0 0 28px}
.fm .fm-h{font-family:var(--mono);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;
color:var(--mut);padding:11px 16px;border-bottom:1px solid var(--line);background:var(--surface2)}
.fm .fm-h.fam-h{display:flex;align-items:center;gap:10px}
.fm table{border-collapse:collapse;width:100%;font-size:.86rem}
.fm td{padding:8px 16px;border-bottom:1px solid var(--line);vertical-align:top}
.fm tr:last-child td{border-bottom:0}
.fm td.k{font-family:var(--mono);color:var(--mut);width:150px;white-space:nowrap}
.fm td.v{font-family:var(--mono);color:var(--ink2);word-break:break-word}
.fam td.k a{font-weight:600;text-decoration:none}
.fam td.k a:hover{text-decoration:underline}
.fam td.v{font-family:var(--font);color:var(--ink);display:flex;gap:9px;align-items:baseline}
.fam .badge{flex:none}
.fam .subn{font-family:var(--mono);font-size:.68rem;color:var(--mut);white-space:nowrap;margin-left:auto}
.body{background:var(--surface);border:1px solid var(--line);border-radius:9px;padding:8px 28px 24px}
.body h1,.body h2,.body h3,.body h4{letter-spacing:-.01em;line-height:1.25;margin:1.4em 0 .5em}
.body h1{font-size:1.4rem}.body h2{font-size:1.18rem;padding-bottom:.25em;border-bottom:1px solid var(--line)}
.body h3{font-size:1.02rem}.body h4{font-size:.92rem;color:var(--ink2)}
.body p{margin:.7em 0}.body ul,.body ol{margin:.6em 0;padding-left:1.4em}.body li{margin:.25em 0}
.body code{font-family:var(--mono);font-size:.85em;background:var(--surface2);padding:1px 5px;border-radius:4px}
.body pre{background:var(--surface2);border:1px solid var(--line);border-radius:7px;padding:12px 14px;overflow-x:auto}
.body pre code{background:none;padding:0}
.body blockquote{border-left:3px solid var(--line);margin:.8em 0;padding:.2em 0 .2em 14px;color:var(--ink2)}
.body hr{border:0;border-top:1px solid var(--line);margin:1.6em 0}
.body .tbl{overflow-x:auto;margin:1em 0}
.body table{border-collapse:collapse;font-size:.85rem;width:100%}
.body th,.body td{border:1px solid var(--line);padding:6px 10px;text-align:left}
.body th{background:var(--surface2)}
.path{font-family:var(--mono);font-size:.72rem;color:var(--mut);margin-top:22px}
.tbar{display:flex;gap:10px;align-items:center;margin:0 0 18px;flex-wrap:wrap}
.tb{font-family:var(--mono);font-size:.76rem;border:1px solid var(--line);background:var(--surface);
color:var(--ink2);border-radius:7px;padding:7px 14px;cursor:pointer}
.tb:hover{border-color:var(--accent);color:var(--accent)}
.tb.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.tb.primary:hover{filter:brightness(1.08);color:#fff}
.tb:disabled{opacity:.55;cursor:progress}
.tb.pin{margin-left:auto}
.tb.pin.pinned{border-color:var(--warn);color:var(--warn);font-weight:600}
a.tb.dep{text-decoration:none;color:var(--accent);border-color:var(--accent)}
a.tb.dep:hover{background:var(--surface2);filter:brightness(1.05)}
.editmsg{font-family:var(--mono);font-size:.74rem;color:var(--mut)}
.editmsg.ok{color:var(--done)}.editmsg.err{color:#d64545}
.editor{width:100%;min-height:62vh;font-family:var(--mono);font-size:.82rem;line-height:1.55;color:var(--ink);
background:var(--surface);border:1px solid var(--line);border-radius:9px;padding:16px 18px;resize:vertical;tab-size:2}
.editor:focus{outline:2px solid var(--accent);outline-offset:1px}
[hidden]{display:none !important}
"""

EDIT_SCRIPT = """<script>
(function(){
  var root=document.getElementById('ticketRoot');
  var tid=root.getAttribute('data-id');
  var view=document.getElementById('view');
  var ed=document.getElementById('editor');
  var editBtn=document.getElementById('editBtn');
  var saveBtn=document.getElementById('saveBtn');
  var cancelBtn=document.getElementById('cancelBtn');
  var msg=document.getElementById('editMsg');
  var original=ed.value;
  function mode(editing){
    view.hidden=editing; ed.hidden=!editing;
    editBtn.hidden=editing; saveBtn.hidden=!editing; cancelBtn.hidden=!editing;
    if(editing){msg.textContent='editing '+tid+' — \\u2318/Ctrl+S to save';msg.className='editmsg';ed.focus();}
  }
  editBtn.addEventListener('click',function(){mode(true);});
  cancelBtn.addEventListener('click',function(){ed.value=original;mode(false);msg.textContent='';});
  saveBtn.addEventListener('click',function(){
    saveBtn.disabled=true; msg.className='editmsg'; msg.textContent='saving\\u2026';
    fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:tid,content:ed.value})})
      .then(function(r){return r.json();})
      .then(function(res){
        if(res.ok){msg.className='editmsg ok';msg.textContent='saved \\u2192 '+res.path+' \\u00b7 reloading\\u2026';
          setTimeout(function(){location.reload();},650);}
        else{msg.className='editmsg err';msg.textContent='error: '+(res.error||'failed');saveBtn.disabled=false;}
      })
      .catch(function(e){msg.className='editmsg err';msg.textContent='error: '+e;saveBtn.disabled=false;});
  });
  document.addEventListener('keydown',function(e){
    if((e.metaKey||e.ctrlKey)&&(e.key==='s'||e.key==='S')&&!ed.hidden){e.preventDefault();saveBtn.click();}
  });
  var pinBtn=document.getElementById('pinBtn');
  function setPin(p){
    pinBtn.classList.toggle('pinned',p);
    pinBtn.setAttribute('data-pinned',p?'1':'0');
    pinBtn.innerHTML=p?'\\uD83D\\uDD16 Pinned':'\\uD83D\\uDD16 Pin';
    pinBtn.title=p?'Pinned to the dashboard \\u2014 click to unpin':'Pin this ticket to the dashboard';
  }
  setPin(pinBtn.getAttribute('data-pinned')==='1');
  pinBtn.addEventListener('click',function(){
    var want=pinBtn.getAttribute('data-pinned')!=='1';
    pinBtn.disabled=true;
    fetch('/api/pin',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:tid,pinned:want})})
      .then(function(r){return r.json();})
      .then(function(res){
        if(res.ok)setPin(res.pinned);
        else{msg.className='editmsg err';msg.textContent='pin error: '+(res.error||'failed');}
      })
      .catch(function(e){msg.className='editmsg err';msg.textContent='pin error: '+e;})
      .finally(function(){pinBtn.disabled=false;});
  });
})();
</script>"""

STATUS_BADGE = {"OPEN": "b-open", "CLOSED": "b-closed", "WONT_FIX": "b-wf", "STANDING": "b-wf"}


def _id_parts(tid):
    """('EM-2165-A-iii') -> ('EM-2165', ('A', 'iii')). None when not a ticket id."""
    m = TICKET_PARTS_RE.match(tid or "")
    if not m:
        return None
    return m.group(1), tuple(s for s in TICKET_SEG_RE.split(m.group(2)) if s)


def _roman(s):
    """Roman numeral value, or None when s is not one."""
    vals = [ROMAN_DIGITS.get(c) for c in s]
    if not vals or None in vals:
        return None
    return sum(-v if i + 1 < len(vals) and v < vals[i + 1] else v
               for i, v in enumerate(vals))


def _seg_key(seg):
    """Order siblings the way the ids read: A < B, 1 < 2, i < ii < iii < iv."""
    if seg.isdigit():
        return (0, int(seg), "")
    if seg.islower():
        r = _roman(seg)
        if r is not None:
            return (1, r, "")
    return (2, 0, seg)


def _family(tid, id_index):
    """(parent_id, children) for tid, derived from id shape.

    children is [(id, status, title, grandchild_count)] in sibling order. A
    ticket hangs off its nearest *existing* ancestor, since intermediate ids are
    not always filed as tickets of their own (EM-0123-CP-1 exists, EM-0123-CP
    does not) — that keeps every sub-ticket reachable from some parent page."""
    parts = _id_parts(tid)
    if not parts:
        return "", []
    base, segs = parts
    present = {}                       # segments -> id, within this base family
    for other in id_index:
        op = _id_parts(other)
        if op and op[0] == base:
            present[op[1]] = other

    def nearest(s):
        """Longest proper prefix of s that is a real ticket."""
        for i in range(len(s) - 1, -1, -1):
            if s[:i] in present:
                return present[s[:i]]
        return ""

    kids_of = collections.defaultdict(list)
    for osegs in present:
        if osegs:
            kids_of[nearest(osegs)].append(osegs)

    kids = []
    # Sort on the whole segment path: siblings adopted from a missing
    # intermediate (EM-0123-CP-1) differ from EM-0123-A at the *first* segment.
    for osegs in sorted(kids_of.get(tid, []),
                        key=lambda s: tuple(_seg_key(x) for x in s)):
        kid = present[osegs]
        kpath = id_index[kid]
        fm = {k: v for k, v in
              frontmatter_scalars(split_frontmatter(read_text(kpath))[0])}
        clean = lambda k: fm.get(k, "").strip().strip('"')
        kids.append({
            "id": kid,
            "slug": os.path.basename(kpath)[:-3],
            "status": fm.get("status", "").upper(),
            "title": clean("title") or kid,
            "epic": epic_code(fm.get("epic", ""), kpath),
            "priority": clean("priority"),
            "risk": clean("risk").upper(),
            "effort": clean("effort"),
            "n_sub": len(kids_of.get(kid, [])),
        })
    return nearest(segs), kids


def _family_block(kids):
    """Sub-ticket table for the ticket page. Empty string when there are none."""
    if not kids:
        return ""
    rows = ""
    for k in kids:
        sub = (" <span class='subn'>%d sub</span>" % k["n_sub"]) if k["n_sub"] else ""
        rows += (
            "<tr><td class='k'><a href='/ticket/%s'%s>%s</a></td>"
            "<td class='v'><span class='badge %s'>%s</span> %s%s</td></tr>" % (
                urllib.parse.quote(k["id"]), _a_data(k), html.escape(k["id"]),
                STATUS_BADGE.get(k["status"], "b-wf"), html.escape(k["status"] or "?"),
                html.escape(k["title"]), sub))
    return ("<div class='fm fam'><div class='fm-h fam-h'>sub-tickets (%d)%s</div>"
            "<table id='famList'><tbody>%s</tbody></table></div>"
            % (len(kids), _list_tools("#famList", "sub-tickets"), rows))


def _dep_button(tid, dep_graph):
    """Link to the dependency explorer, when this ticket is in the graph."""
    if not dep_graph or tid not in dep_graph["nodes"]:
        return ""
    blocked_by = sum(1 for b, t in dep_graph["edges"] if t == tid)
    blocks = sum(1 for b, t in dep_graph["edges"] if b == tid)
    bits = []
    if blocked_by:
        bits.append("blocked by %d" % blocked_by)
    if blocks:
        bits.append("blocks %d" % blocks)
    sub = (" &middot; " + " &middot; ".join(bits)) if bits else ""
    return ('<a class="tb dep" href="/deps?id=%s" title="Trace this ticket&#39;s'
            ' blockers and what it unlocks">&#9741; Dependency graph%s</a>'
            % (urllib.parse.quote(tid), sub))


def render_ticket_page(path, known_ids=None, known_epics=None, adrs=None,
                       pinned=False, id_index=None, dep_graph=None):
    text = read_text(path)
    fm_lines, body = split_frontmatter(text)
    pairs = frontmatter_scalars(fm_lines)
    fm = {k: v for k, v in pairs}
    tid = fm.get("id", os.path.basename(path))
    title = fm.get("title", "").strip().strip('"') or tid
    status = fm.get("status", "").upper()
    badge_cls = STATUS_BADGE.get(status, "b-wf")

    rows = ""
    for k, v in pairs:
        if not v:
            val = "&mdash;"
        elif k == "id":
            val = html.escape(v)  # no self-link
        else:
            val = autolink_ids(html.escape(v), known_ids, known_epics, adrs)
        rows += '<tr><td class="k">%s</td><td class="v">%s</td></tr>' % (
            html.escape(k), val)

    em = EPIC_CODE_RE.search(fm.get("epic", "") or "") or EPIC_CODE_RE.search(path)
    epic_crumb = ""
    if em:
        code = "E" + em.group(1)
        epic_crumb = ('&nbsp;&middot;&nbsp; <a class="back" href="/epic/' + code
                      + '">epic __PFX__-' + code + ' &rarr;</a>')

    parent, kids = _family(tid, id_index or {})
    parent_crumb = ""
    if parent:
        parent_crumb = ('&nbsp;&middot;&nbsp; <a class="back" href="/ticket/'
                        + urllib.parse.quote(parent) + '">&uarr; parent '
                        + html.escape(parent) + '</a>')

    rel = html.escape(os.path.relpath(path, REPO_ROOT))
    raw = html.escape(text)
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>" + html.escape(tid) + " &middot; ticket</title>"
        "<link rel=\"icon\" href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>__FAVICON__</text></svg>\">"
        "<style>" + TICKET_CSS + "</style></head><body>"
        "<div class='wrap' id='ticketRoot' data-id='" + html.escape(tid) + "'>"
        "<a class='back' href='/'>&larr; back to dashboard</a>" + epic_crumb
        + parent_crumb +
        "<div class='tid'>" + html.escape(tid) + "</div>"
        "<h1 class='title'>" + html.escape(title)
        + " <span class='badge " + badge_cls + "'>" + html.escape(status or "?") + "</span></h1>"
        "<div class='tbar'>"
        "<button class='tb' id='editBtn' type='button'>&#9998; Edit file</button>"
        "<button class='tb primary' id='saveBtn' type='button' hidden>Save to disk</button>"
        "<button class='tb' id='cancelBtn' type='button' hidden>Cancel</button>"
        + _dep_button(tid, dep_graph) +
        "<span class='editmsg' id='editMsg'></span>"
        "<button class='tb pin' id='pinBtn' type='button' data-pinned='"
        + ("1" if pinned else "0") + "'>&#128278;</button>"
        "<button class='tb copy-one' type='button' data-copy='"
        + html.escape(os.path.basename(path)[:-3], quote=True)
        + "' title='Copy this ticket&#39;s id'>&#128203; Copy</button></div>"
        "<div id='view'>"
        "<div class='fm'><div class='fm-h'>frontmatter (yaml)</div>"
        "<table><tbody>" + rows + "</tbody></table></div>"
        + _family_block(kids) +
        "<div class='body'>"
        + autolink_ids(rewrite_md_links(md_to_html(body), os.path.dirname(path),
                                        known_ids, known_epics),
                       known_ids, known_epics, adrs) + "</div>"
        "<div class='path'>" + rel + "</div>"
        "</div>"
        "<textarea class='editor' id='editor' spellcheck='false' hidden>" + raw + "</textarea>"
        + EDIT_SCRIPT +
        "</div></body></html>"
    )
    return page


_DOC_PIN_SCRIPT = """<script>(function(){
  var b=document.getElementById('pinBtn');if(!b)return;
  function set(p){b.classList.toggle('pinned',p);b.setAttribute('data-pinned',p?'1':'0');
    b.innerHTML=p?'\\uD83D\\uDD16 Pinned':'\\uD83D\\uDD16 Pin';
    b.title=p?'Pinned to the dashboard \\u2014 click to unpin':'Pin this doc to the dashboard';}
  set(b.getAttribute('data-pinned')==='1');
  b.addEventListener('click',function(){
    var want=b.getAttribute('data-pinned')!=='1';b.disabled=true;
    fetch('/api/pin',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:b.getAttribute('data-doc'),pinned:want})})
      .then(function(r){return r.json();})
      .then(function(res){if(res.ok)set(res.pinned);})
      .finally(function(){b.disabled=false;});
  });
})();</script>"""


def render_doc_page(path, known_ids=None, known_epics=None, adrs=None,
                    pinned=False):
    """Rendered view of any markdown file in the repo (ADRs, epic charters…)."""
    text = read_text(path)
    fm_lines, body = split_frontmatter(text)
    pairs = frontmatter_scalars(fm_lines)
    title = ""
    for line in body.split("\n"):
        m = re.match(r"^#\s+(.*)$", line)
        if m:
            title = m.group(1).strip()
            break
    title = title or os.path.basename(path)

    rows = ""
    for k, v in pairs:
        val = (autolink_ids(html.escape(v), known_ids, known_epics, adrs)
               if v else "&mdash;")
        rows += '<tr><td class="k">%s</td><td class="v">%s</td></tr>' % (
            html.escape(k), val)
    fm_html = ('<div class="fm"><div class="fm-h">frontmatter (yaml)</div>'
               "<table><tbody>" + rows + "</tbody></table></div>") if rows else ""

    body_html = autolink_ids(
        rewrite_md_links(md_to_html(body), os.path.dirname(path),
                         known_ids, known_epics),
        known_ids, known_epics, adrs)
    # Pin keys always use forward slashes so they're portable across platforms.
    rel_key = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
    rel = html.escape(rel_key)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>" + html.escape(os.path.basename(path)) + " &middot; doc</title>"
        "<link rel=\"icon\" href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>__FAVICON__</text></svg>\">"
        "<style>" + TICKET_CSS + "</style></head><body><div class='wrap'>"
        "<a class='back' href='/'>&larr; back to dashboard</a>"
        "<div class='tid'>" + rel + "</div>"
        "<h1 class='title'>" + html.escape(title) + "</h1>"
        "<div class='tbar'><button class='tb pin' id='pinBtn' type='button' "
        "data-pinned='" + ("1" if pinned else "0") + "' data-doc='"
        + html.escape(rel_key, quote=True) + "'>&#128278;</button></div>"
        + fm_html +
        "<div class='body'>" + body_html + "</div>"
        "<div class='path'>" + rel + "</div>"
        "</div>" + _DOC_PIN_SCRIPT + "</body></html>"
    )


def render_series_page(series, recs, by_num):
    """Index of one reserved document series (ADRs, PRs, RFCs…), newest first."""
    plural = series["prefix"] + "s"
    lis = ""
    for a in recs:
        s = a["status"].lower()
        cls = ("b-closed" if s.startswith("accept")
               else "b-wf" if ("supersed" in s or "reject" in s or "deprecat" in s)
               else "b-open")
        badge = ('<span class="badge %s">%s</span>' % (cls, html.escape(a["status"]))
                 if a["status"] else "")
        if by_num.get(a["n"]) == "":
            badge += ' <span class="mchip blocked">duplicate №</span>'
        lis += ('<li data-id="%s" data-search="%s"><a href="/doc/%s">'
                '<span class="tk-id">%s</span>'
                '<span class="tk-ttl">%s %s</span><span class="tk-meta">%s</span>'
                "</a></li>"
                % (html.escape(a["num"]),
                   html.escape((a["num"] + " " + a["title"]).lower()),
                   html.escape(a["path"]), html.escape(a["num"]),
                   html.escape(a["title"]), badge,
                   ('<span class="tk-date">%s</span>' % html.escape(a["date"]))
                   if a["date"] else ""))
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>" + html.escape(plural) + " &middot; __BRAND__</title>"
        "<link rel=\"icon\" href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>__FAVICON__</text></svg>\">"
        "<style>" + EPIC_CSS + "</style></head><body><div class='wrap'>"
        "<a class='back' href='/'>&larr; back to dashboard</a>"
        "<div class='ehead'>"
        "<div class='tid'>" + html.escape(series["title"].upper()) + "</div>"
        "<h1 class='title'>" + html.escape(plural) + "</h1>"
        "<div class='estats'><span><b>" + str(len(recs)) + "</b> records</span>"
        "<span>" + html.escape(series["dir"]) + "/</span></div></div>"
        "<div class='cols' style='grid-template-columns:1fr'>"
        "<div class='col'><div class='col-h'><span>Newest first</span>"
        "<span class='cnt'>" + str(len(recs)) + "</span></div>"
        "<ul>" + (lis or '<div class="empty">no ' + html.escape(plural)
                  + ' found</div>') + "</ul></div>"
        "</div></div></body></html>"
    )


# --------------------------------------------------------------------------- #
# Epic detail page
# --------------------------------------------------------------------------- #
EPIC_CSS = TICKET_CSS + """
.wrap{max-width:1160px}
.ehead{background:var(--surface);border:1px solid var(--line);border-radius:11px;padding:22px 26px;margin-bottom:22px}
.ehead .bar{height:14px;border-radius:5px;overflow:hidden;display:flex;background:var(--surface2);border:1px solid var(--line);margin:14px 0 10px}
.ehead .bar span{height:100%}
.ehead .tid{display:flex;align-items:center;gap:10px}
.ecopy{font-family:var(--mono);font-size:.7rem;font-weight:500;color:var(--mut);background:var(--surface2);
border:1px solid var(--line);border-radius:100px;padding:3px 10px;cursor:pointer}
.ecopy:hover{border-color:var(--accent);color:var(--accent)}
.sg-done{background:var(--done)}.sg-open{background:var(--accent)}.sg-wf{background:var(--wf)}
.estats{display:flex;flex-wrap:wrap;gap:6px 26px;font-family:var(--mono);font-size:.78rem;color:var(--mut)}
.estats b{color:var(--ink)}
.cols{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;align-items:start}
@media (max-width:900px){.cols{grid-template-columns:1fr !important}}
.col{background:var(--surface);border:1px solid var(--line);border-radius:9px;overflow:hidden}
.col-h{font-family:var(--mono);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;
padding:10px 14px;border-bottom:1px solid var(--line);background:var(--surface2);display:flex;justify-content:space-between}
.col-h .cnt{color:var(--ink)}
.col-h .ch-r{display:flex;align-items:center;gap:12px}
.col ul{list-style:none;margin:0;padding:6px}
.col li a{display:flex;flex-wrap:wrap;gap:2px 10px;align-items:baseline;padding:7px 9px;border-radius:6px;text-decoration:none;color:inherit}
.col li a:hover{background:var(--surface2)}
.col li.child a{margin-left:18px;border-left:2px solid var(--line);border-radius:0 6px 6px 0}
.tk-id{font-family:var(--mono);font-size:.72rem;font-weight:600;color:var(--accent);flex:none;min-width:86px}
.col-closed .tk-id{color:var(--done)}.col-wf .tk-id{color:var(--wf)}
.tk-ttl{font-size:.84rem;color:var(--ink2);line-height:1.35;flex:1 1 45%;min-width:220px}
.col-closed .tk-ttl,.col-wf .tk-ttl{color:var(--mut)}
.tk-meta{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin-left:auto}
.mchip{font-family:var(--mono);font-size:.6rem;border:1px solid var(--line);border-radius:4px;padding:0 5px;color:var(--mut);white-space:nowrap}
.mchip.r-high{color:#c23b3b;border-color:#c23b3b}.mchip.r-medium{color:var(--warn);border-color:var(--warn)}
.mchip.r-low{color:var(--done);border-color:var(--done)}
.mchip.blocked{color:var(--warn);border-color:var(--warn)}
.mchip.unblocks{color:var(--accent);border-color:var(--accent)}
.tk-date{font-family:var(--mono);font-size:.64rem;color:var(--mut);margin-left:auto}
.empty{font-family:var(--mono);font-size:.74rem;color:var(--mut);padding:16px 14px}
@media (prefers-color-scheme:dark){.mchip.r-high{color:#e06c6c;border-color:#e06c6c}}
.fbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:0 0 14px}
.searchbox{font-family:var(--mono);font-size:.76rem;color:var(--ink);background:var(--surface);
border:1px solid var(--line);border-radius:100px;padding:7px 13px;width:210px}
.searchbox::placeholder{color:var(--mut)}
.searchbox:focus{outline:2px solid var(--accent);outline-offset:1px}
.sortlab{font-family:var(--mono);font-size:.72rem;color:var(--mut);display:flex;align-items:center;gap:6px}
.sortsel{font-family:var(--mono);font-size:.72rem;color:var(--ink);background:var(--surface);
border:1px solid var(--line);border-radius:7px;padding:5px 9px;cursor:pointer}
.lnk{font-family:var(--mono);font-size:.72rem;color:var(--accent);background:none;border:0;cursor:pointer;padding:0}
.lnk:hover{text-decoration:underline}
.mchip.epic-link{cursor:pointer}
.mchip.epic-link:hover,.mchip.epic-link:focus-visible{color:var(--accent);border-color:var(--accent);text-decoration:underline}
.mchip.wip{color:var(--accent);border-color:var(--accent);font-weight:600}
.col-standing .tk-id{color:var(--warn)}
.col li[hidden]{display:none}
.e-emoji{font-size:1.25em;margin-right:6px;vertical-align:-2px}
.mchip.pinned{color:var(--warn);border-color:var(--warn)}
.mchip.pinned[data-unpin]{cursor:pointer}
.mchip.pinned[data-unpin]:hover,.mchip.pinned[data-unpin]:focus-visible{color:#c23b3b;border-color:#c23b3b;text-decoration:line-through}
/* the unpinned pin: invisible until you're on the row, so a list of 40 tickets
   doesn't read as a list of 40 buttons */
.mchip.pin-add{cursor:pointer;opacity:0;border-color:transparent;transition:opacity .12s}
li:hover .mchip.pin-add,.card:hover .mchip.pin-add{opacity:.5}
.mchip.pin-add:hover,.mchip.pin-add:focus-visible{opacity:1;color:var(--warn);border-color:var(--warn)}
@media (hover:none){.mchip.pin-add{opacity:.5}}
"""


# Shared client-side filter bar for the epic + filtered-list pages. Mirrors the
# dashboard backlog controls (search / risk / priority / effort / blocked).
LIST_FILTER_BAR = """
<div class="fbar">
  <input id="lfSearch" class="searchbox" type="search" placeholder="search __PFX__-#### or title"
         autocomplete="off" aria-label="Filter tickets by id or title">
  <label class="sortlab">risk <select id="lfRisk" class="sortsel">
    <option value="">all</option><option value="HIGH">high</option>
    <option value="MEDIUM">medium</option><option value="LOW">low</option></select></label>
  <label class="sortlab">priority <select id="lfPrio" class="sortsel">
    <option value="">all</option><option value="1">P1</option>
    <option value="2">P2</option><option value="3">P3</option></select></label>
  <label class="sortlab">effort <select id="lfEff" class="sortsel">
    <option value="">all</option><option value="s">&le;4h</option>
    <option value="m">&gt;4h&ndash;2d</option><option value="l">&gt;2d</option>
    <option value="none">unestimated</option></select></label>
  <label class="sortlab">blocked <select id="lfBlk" class="sortsel">
    <option value="">all</option><option value="y">blocked</option>
    <option value="n">unblocked</option></select></label>
  __SORT__
  <button type="button" class="lnk" id="lfClear">clear</button>
</div>"""

# The grid sorts from its column headers, so it doesn't carry this — two controls
# for one action is how they end up disagreeing. Epic pages are lists, with no
# headers to click, and keep it.
_SORT_SELECT = """<label class="sortlab">sort <select id="lfSort" class="sortsel">
    <option value="">default</option><option value="newest">newest first</option>
    <option value="oldest">oldest first</option><option value="risk">by risk</option>
    <option value="priority">by priority</option>
    <option value="effort">by effort</option></select></label>"""


def list_filter_bar(sort_select=True):
    return LIST_FILTER_BAR.replace("__SORT__", _SORT_SELECT if sort_select else "")

LIST_FILTER_SCRIPT = """<script>
(function(){
  var q=document.getElementById('lfSearch'),risk=document.getElementById('lfRisk'),
      prio=document.getElementById('lfPrio'),eff=document.getElementById('lfEff'),
      blk=document.getElementById('lfBlk'),srt=document.getElementById('lfSort');
  function apply(){
    var qs=q.value.trim().toLowerCase();
    document.querySelectorAll('.col').forEach(function(col){
      var shown=0,total=0;
      col.querySelectorAll('li[data-id]').forEach(function(li){
        total++;
        var ok=(!risk.value||li.getAttribute('data-risk')===risk.value)
          &&(!prio.value||li.getAttribute('data-prio')===prio.value)
          &&(!eff.value||li.getAttribute('data-eff')===eff.value)
          &&(!blk.value||li.getAttribute('data-blocked')===blk.value)
          &&(!qs||li.getAttribute('data-search').indexOf(qs)!==-1);
        li.hidden=!ok; if(ok)shown++;
      });
      var cnt=col.querySelector('.col-h .cnt');
      if(cnt)cnt.textContent=(shown===total)?String(total):shown+' / '+total;
    });
  }
  // sort: parent + sub-tickets move as one group, keyed by the group's best
  // value. Only top-level lis move — card pages nest families in a .fam li.
  var RISK_ORD={HIGH:0,MEDIUM:1,LOW:2};
  var uls=[];
  document.querySelectorAll('.col > ul').forEach(function(ul){
    uls.push({ul:ul,orig:Array.prototype.slice.call(ul.children).filter(
      function(n){return n.tagName==='LI';})});
  });
  var STATUS_ORD={open:0,closed:1,wf:2};
  /* One key per sortable column. A key is a value plus whether it is missing:
     an unestimated ticket has no effort, and it should sink to the bottom in
     BOTH directions rather than lead the list when you reverse it. Values are
     numbers where the column is a quantity (id, priority, effort, date) and
     strings where it is words (title, epic) — the comparator handles either,
     which the old subtract-everything one could not. A date carries the ticket
     number in its low digits, because a dozen tickets share a busy day and the
     id is the only thing that says which of them came last. */
  function key(li,col){
    var v;
    if(col==='risk')          v=RISK_ORD[li.getAttribute('data-risk')];
    else if(col==='status')   v=STATUS_ORD[li.getAttribute('data-status')];
    else if(col==='prio')     v=parseInt(li.getAttribute('data-prio'),10);
    else if(col==='num')      v=parseInt(li.getAttribute('data-num'),10);
    else if(col==='effh'){    var h=li.getAttribute('data-effh');
                              v=(h===''||h==null)?null:parseFloat(h);}
    else if(col==='date'){    var d=(li.getAttribute('data-date')||'').replace(/-/g,'');
                              v=d?parseInt(d,10)*1e6+(parseInt(li.getAttribute('data-num'),10)||0):null;}
    else                      v=li.getAttribute('data-'+col)||'';   /* title, epic */
    var missing=(v==null||v===''||(typeof v==='number'&&isNaN(v)));
    return {v:v,missing:missing};
  }
  function cmp(a,b){
    if(a.missing||b.missing)return a.missing&&b.missing?0:(a.missing?1:-1);
    if(typeof a.v==='number'&&typeof b.v==='number')return a.v-b.v;
    return String(a.v).localeCompare(String(b.v));
  }
  /* A group is a parent plus its sub-tickets, and it moves as one — keyed by the
     parent (which leads it), so a family lands where its parent belongs. */
  function groupKey(g,col){
    for(var i=0;i<g.length;i++)if(g[i].hasAttribute('data-id'))return key(g[i],col);
    return {v:'',missing:true};
  }
  var sortCol='',sortDir=1;
  function applySort(){
    uls.forEach(function(u){
      var groups=[],cur=null;
      u.orig.forEach(function(li){
        if(!li.classList.contains('child')||!cur){cur=[];groups.push(cur);}
        cur.push(li);
      });
      if(sortCol){
        groups=groups.slice().sort(function(a,b){
          var d=cmp(groupKey(a,sortCol),groupKey(b,sortCol));
          /* missing always sinks: never flip it with the direction */
          if(groupKey(a,sortCol).missing!==groupKey(b,sortCol).missing)return d;
          return d*sortDir;
        });
      }
      groups.forEach(function(g){g.forEach(function(li){u.ul.appendChild(li);});});
    });
  }
  /* Column headers ARE the sort control on the grid. First click orders the way
     the column is usually read — newest/highest/most-urgent first, text A→Z —
     and a second click reverses it. */
  var DESC_FIRST={num:1,date:1};
  var heads=[].slice.call(document.querySelectorAll('.g-head [data-sort]'));
  function markHeads(){
    heads.forEach(function(h){
      var col=h.getAttribute('data-sort'),on=col===sortCol;
      h.classList.toggle('sorted',on);
      /* inactive headers carry the direction their FIRST click would apply, so
         the arrow that ghosts in on hover isn't lying about what you'd get */
      h.setAttribute('data-dir',String(on?sortDir:(DESC_FIRST[col]?-1:1)));
      h.setAttribute('aria-sort',on?(sortDir<0?'descending':'ascending'):'none');
    });
  }
  function sortBy(col){
    if(col===sortCol)sortDir=-sortDir;
    else{sortCol=col;sortDir=DESC_FIRST[col]?-1:1;}
    markHeads();applySort();
  }
  heads.forEach(function(h){
    h.addEventListener('click',function(){sortBy(h.getAttribute('data-sort'));});
    h.addEventListener('keydown',function(ev){
      if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();sortBy(h.getAttribute('data-sort'));}
    });
  });
  if(heads.length){sortCol='date';sortDir=-1;markHeads();}  /* as the server sent it */
  /* Epic pages keep the dropdown — they are lists, and have no headers to click. */
  if(srt)srt.addEventListener('change',function(){
    var v=srt.value;
    if(!v){sortCol='';}
    else if(v==='newest'){sortCol='date';sortDir=-1;}
    else if(v==='oldest'){sortCol='date';sortDir=1;}
    else{sortCol=({risk:'risk',priority:'prio',effort:'effh'})[v];sortDir=1;}
    markHeads();applySort();
  });
  [q,risk,prio,eff,blk].forEach(function(el){
    el.addEventListener('input',apply);el.addEventListener('change',apply);});
  q.addEventListener('keydown',function(e){if(e.key==='Escape'){q.value='';apply();}});
  document.getElementById('lfClear').addEventListener('click',function(){
    q.value='';risk.value='';prio.value='';eff.value='';blk.value='';
    if(srt)srt.value='';
    sortCol=heads.length?'date':'';sortDir=-1;markHeads();applySort();apply();});
  // epic chips sit inside ticket links; intercept and route to the epic page
  document.addEventListener('click',function(ev){
    var ch=ev.target.closest('.mchip[data-epic]');if(!ch)return;
    ev.preventDefault();ev.stopPropagation();
    location.href='/epic/'+ch.getAttribute('data-epic');},true);
  document.addEventListener('keydown',function(ev){
    if(ev.key!=='Enter')return;
    var ch=ev.target&&ev.target.closest?ev.target.closest('.mchip[data-epic]'):null;
    if(!ch)return;ev.preventDefault();
    location.href='/epic/'+ch.getAttribute('data-epic');});
  // pin chips toggle in place (they sit inside ticket links, so swallow the nav)
  function setPin(ch,tid,pinned){
    ch.style.pointerEvents='none';
    fetch('/api/pin',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:tid,pinned:pinned})})
      .then(function(){location.reload();})
      .catch(function(){ch.style.pointerEvents='';ch.title='pin failed — server down?';});
  }
  function pinFromEvent(ev){
    var ch=ev.target.closest&&ev.target.closest('.mchip[data-pin],.mchip[data-unpin]');
    if(!ch)return;
    ev.preventDefault();ev.stopPropagation();
    var add=ch.hasAttribute('data-pin');
    setPin(ch,ch.getAttribute(add?'data-pin':'data-unpin'),add);
  }
  document.addEventListener('click',pinFromEvent,true);
  document.addEventListener('keydown',function(ev){
    if(ev.key==='Enter'||ev.key===' ')pinFromEvent(ev);},true);
})();
</script>"""


def _epic_group(items, datekey, reverse):
    """Order tickets, grouping compound sub-tickets (EM-####-A) under their root.

    Returns [(ticket, is_child)] — groups sorted by the group's best date,
    members sorted by id so the parent (if present) leads."""
    groups = {}
    for t in items:
        m = SUB_ID_RE.match(t["id"])
        groups.setdefault(m.group(1) if m else t["id"], []).append(t)

    def gdate(root):
        ds = [t.get(datekey) or "" for t in groups[root] if t.get(datekey)]
        if not ds:
            return "" if reverse else "9999-99-99"
        return max(ds) if reverse else min(ds)

    def gkey(root):
        # Dates are coarse — a busy day gives a dozen tickets the same date, and
        # then the id is the only thing that says which came last. Tie-break on
        # the number, in whichever direction the date is already going.
        m = _IDRE["ticket_split"].match(root)
        return (gdate(root), int(m.group(1)) if m else 0)

    out = []
    for root in sorted(groups, key=gkey, reverse=reverse):
        g = sorted(groups[root], key=lambda t: t["id"])
        for t in g:
            out.append((t, len(g) > 1 and bool(SUB_ID_RE.match(t["id"]))))
    return out


def _epic_ticket_li(t, is_child, datekey, today, show_epic=False):
    chips = ""
    if show_epic and t.get("epic"):
        ec = html.escape(t["epic"])
        chips += ('<span class="mchip epic-link" role="link" tabindex="0" data-epic="%s"'
                  ' title="open the __PFX__-%s epic page">__PFX__-%s</span>' % (ec, ec, ec))
    if t.get("risk"):
        chips += '<span class="mchip r-%s">%s</span>' % (t["risk"].lower(), t["risk"][0])
    if t.get("priority"):
        chips += '<span class="mchip">P%s</span>' % html.escape(str(t["priority"]))
    if t.get("effort"):
        chips += '<span class="mchip">%s</span>' % html.escape(t["effort"])
    if t.get("blocked"):
        chips += '<span class="mchip blocked">blocked</span>'
    if t.get("unblocks"):
        chips += '<span class="mchip unblocks">unblocks %d</span>' % t["unblocks"]
    if t.get("wip"):
        chips += ('<span class="mchip wip" title="file modified in the git'
                  ' working tree">WIP</span>')
    chips += _pin_chip(t["id"], bool(t.get("pinned")))
    date = t.get(datekey) or ""
    dlab = date
    if datekey == "created" and date:
        d = parse_date(date)
        if d:
            dlab = "%s · %dd old" % (date, (today - d).days)
    return ('<li class="%s"%s><a href="/ticket/%s"%s>'
            '<span class="tk-id">%s</span><span class="tk-ttl">%s</span>'
            '<span class="tk-meta">%s%s</span></a></li>' % (
                "child" if is_child else "", _li_attrs(t, date), html.escape(t["id"]), _a_data(t),
                html.escape(t["id"]), html.escape(t["title"]), chips,
                ('<span class="tk-date">%s</span>' % html.escape(dlab)) if dlab else ""))


def pocket_html():
    """The created/closed badge. Rendered hidden; whoever has the counts calls
    ifcPocket() to fill it — the dashboard from the board it already fetched,
    every other page from /api/pocket."""
    return (
        "<span class='today-pocket' id='todayPocket' hidden>"
        "<button class='tp-lab' id='tp-win' type='button'"
        " title='Switch window: today / 7 days / 30 days'>today</button>"
        "<a class='tp-item tp-new' id='tp-new-a' href='/filter?created=today'>"
        "<b id='tp-new'>0</b> created</a>"
        "<a class='tp-item tp-done' id='tp-done-a'"
        " href='/filter?closed=today&status=closed'><b id='tp-done'>0</b> closed</a>"
        "</span>")


# One pocket, one behaviour, wherever it is rendered: fill the counts, cycle the
# window on click (remembered across pages), and keep both links pointing at the
# view the numbers came from. The windows come from the server, so the badge and
# the page behind it can't disagree about what "7 days" means.
_POCKET_JS = """
window.ifcPocket=function(counts){
  var pocket=document.getElementById('todayPocket');
  if(!pocket||!counts)return;
  var WINS=__POCKET_WINDOWS__,
      lab=document.getElementById('tp-win'),
      LS_KEY='ifcTodayWin';
  function show(key){
    var w=WINS.filter(function(x){return x.key===key;})[0]||WINS[0],
        c=counts[w.key]||{created:0,closed:0};
    lab.textContent=w.lab;
    document.getElementById('tp-new').textContent=c.created;
    document.getElementById('tp-done').textContent=c.closed;
    document.getElementById('tp-new-a').href='/filter?created='+w.key
      +'&label='+encodeURIComponent('Created — '+w.lab);
    document.getElementById('tp-done-a').href='/filter?closed='+w.key
      +'&status=closed&label='+encodeURIComponent('Closed — '+w.lab);
    try{localStorage.setItem(LS_KEY,w.key);}catch(e){}
  }
  if(!lab.getAttribute('data-wired')){
    lab.setAttribute('data-wired','1');
    lab.addEventListener('click',function(){
      var cur='today';
      try{cur=localStorage.getItem(LS_KEY)||'today';}catch(e){}
      var i=WINS.map(function(w){return w.key;}).indexOf(cur);
      show(WINS[(i+1)%WINS.length].key);
    });
  }
  var saved='today';
  try{saved=localStorage.getItem(LS_KEY)||'today';}catch(e){}
  show(saved);
  pocket.hidden=false;
};
"""


def pocket_js():
    wins = [{"key": k, "lab": lab} for k, _d, lab in POCKET_WINDOWS]
    return _POCKET_JS.replace("__POCKET_WINDOWS__", json.dumps(wins))


def _pin_chip(tid, pinned):
    """Pin/unpin from any list. Pinned reads at a glance (🔖, always visible);
    unpinned is a quiet 📌 that surfaces on hover — pinning is the action you
    want while scanning, and it shouldn't shout at you from every row."""
    if pinned:
        return ('<span class="mchip pinned" role="button" tabindex="0"'
                ' data-unpin="%s" title="pinned — click to unpin">&#128278;</span>'
                % html.escape(tid))
    return ('<span class="mchip pin-add" role="button" tabindex="0" data-pin="%s"'
            ' title="pin this ticket">&#128204;</span>' % html.escape(tid))


def _li_attrs(t, date=""):
    """data-* attributes the shared client-side filter/sort script reads.

    One per sortable column, so a header click has something to order by:
    `date` is whichever date the column is about (created for open, closed for
    closed — "newest first" then means what the heading says), `num` breaks the
    ties a same-day date can't, and title/epic/status carry the text columns."""
    m = _IDRE["ticket_split"].match(t["id"])
    return (' data-id="%s" data-num="%s" data-title="%s" data-epic="%s"'
            ' data-status="%s" data-risk="%s" data-prio="%s" data-eff="%s"'
            ' data-effh="%s" data-blocked="%s" data-date="%s" data-search="%s"' % (
                html.escape(t["id"]), m.group(1) if m else "",
                html.escape(t["title"].lower()), html.escape(t.get("epic") or ""),
                html.escape(t.get("status") or ""),
                html.escape(t.get("risk") or ""),
                html.escape(str(t.get("priority") or "")),
                _effort_bucket(t.get("effortH")),
                "" if t.get("effortH") is None else ("%g" % t["effortH"]),
                "y" if t.get("blocked") else "n", html.escape(date),
                html.escape((t["id"] + " " + t["title"]).lower())))


def _a_data(t):
    """data-* on the ticket <a> that the shared copy-ids / CSV toolbar reads."""
    e = lambda v: html.escape("" if v is None else str(v), quote=True)
    return (' data-slug="%s" data-title="%s" data-status="%s" data-epic="%s"'
            ' data-priority="%s" data-risk="%s" data-effort="%s"' % (
                e(t.get("slug") or t.get("id")), e(t.get("title")),
                e(t.get("status")), e(t.get("epic")),
                e(t.get("priority")), e(t.get("risk")), e(t.get("effort"))))


_STATUS_LABEL = {"open": "OPEN", "closed": "CLOSED", "wf": "WON'T FIX"}


def _grid_row(t, is_child, datekey, today, show_status):
    """One ticket as a row of cells. Same `data-*` contract and same
    `li > a` shape as the list rows, so the shared toolbar (search, filters,
    sort, copy-ids, CSV) and the pin handler all keep working as they are."""
    date = t.get(datekey) or ""
    age = ""
    if datekey == "created" and date:
        d = parse_date(date)
        if d:
            age = '<span class="g-age">%dd</span>' % (today - d).days
    cells = [
        '<span class="g-id">%s%s</span>' % (
            '<span class="g-sub" title="sub-ticket">&#8627;</span>' if is_child else "",
            html.escape(t["id"])),
        '<span class="g-ttl">%s</span>' % html.escape(t["title"]),
        ('<span class="g-epic"><span class="mchip epic-link" role="link"'
         ' tabindex="0" data-epic="%s" title="open the __PFX__-%s epic page">'
         '%s</span></span>' % (html.escape(t.get("epic", "")),
                               html.escape(t.get("epic", "")),
                               html.escape(t.get("epic", "") or "—"))),
    ]
    if show_status:
        st = t.get("status", "")
        cells.append('<span class="g-st st-%s">%s</span>'
                     % (st, _STATUS_LABEL.get(st, st)))
    cells += [
        '<span class="g-p">%s</span>' % (
            "P%s" % html.escape(str(t["priority"])) if t.get("priority") else "—"),
        '<span class="g-risk r-%s">%s</span>' % (
            (t.get("risk") or "none").lower(), html.escape(t.get("risk") or "—")),
        '<span class="g-eff">%s</span>' % html.escape(t.get("effort") or "—"),
        '<span class="g-date">%s%s</span>' % (html.escape(date or "—"), age),
        '<span class="g-flags">%s%s%s</span>' % (
            '<span class="mchip blocked" title="blocked">&#9940;</span>'
            if t.get("blocked") else "",
            '<span class="mchip wip" title="modified in the working tree">WIP</span>'
            if t.get("wip") else "",
            _pin_chip(t["id"], bool(t.get("pinned")))),
    ]
    return ('<li%s><a class="g-row" href="/ticket/%s"%s>%s</a></li>'
            % (_li_attrs(t, date), html.escape(t["id"]), _a_data(t),
               "".join(cells)))


def _ticket_grid(items, datekey, today, show_status):
    """The one filtered view: a row per ticket, cells in real columns.

    The column template lives in a CSS variable on the <ul>, and the header row
    and every ticket row inherit it — so they cannot drift out of alignment. Each
    heading is the column's sort control (`data-sort` names the `data-*` the
    script orders by), which is the only thing a column header has ever meant."""
    # (css class, width, heading, sort key) — the header cells carry the same
    # classes as the row cells, so a column that steps aside on a narrow screen
    # takes its heading with it.
    cells = [("g-id", "96px", "ID", "num"),
             ("g-ttl", "minmax(0,1fr)", "TITLE", "title"),
             ("g-epic", "78px", "EPIC", "epic")]
    if show_status:
        cells.append(("g-st", "82px", "STATUS", "status"))
    cells += [("g-p", "38px", "P", "prio"),
              ("g-risk", "58px", "RISK", "risk"),
              ("g-eff", "58px", "EFFORT", "effh"),
              ("g-date", "116px", "CLOSED" if datekey == "closed" else "CREATED",
               "date"),
              ("g-flags", "62px", "", "")]

    rows = "".join(_grid_row(t, child, datekey, today, show_status)
                   for t, child in _epic_group(items, datekey, True))
    if not rows:
        return '<div class="col"><div class="empty">no tickets match</div></div>'
    head = ""
    for cls, _w, label, key in cells:
        if not key:
            head += '<span class="%s"></span>' % cls
            continue
        # The server sorted by date, descending — say so, so the grid arrives
        # already telling you how it is ordered.
        active = ' data-dir="-1"' if key == "date" else ""
        head += ('<span class="%s gh-sort" role="button" tabindex="0"'
                 ' data-sort="%s"%s title="Sort by %s">%s'
                 '<span class="gh-arrow" aria-hidden="true"></span></span>'
                 % (cls, key, active, label.lower() or key, label))
    tpl = "--gcols:%s" % " ".join(w for _c, w, _h, _k in cells)
    return ('<div class="col col-grid"><div class="col-h"><span>%d ticket%s</span>'
            '<span class="ch-r"><span class="cnt">%d</span>%s</span></div>'
            '<div class="g-head" style="%s">%s</div>'
            '<ul id="lst-grid" class="grid" style="%s">%s</ul></div>'
            % (len(items), "" if len(items) == 1 else "s", len(items),
               _list_tools("#lst-grid", "grid"), tpl, head, tpl, rows))


def _status_col(kind, label, items, datekey, reverse, today, show_epic=False):
    """A status column on the epic page — where you browse one epic's tickets.
    The filtered view has its own shape (_ticket_grid); this stays a list."""
    lis = "".join(_epic_ticket_li(t, c, datekey, today, show_epic)
                  for t, c in _epic_group(items, datekey, reverse))
    lid = "lst-" + kind
    body = ('<ul id="%s">' % lid) + lis + "</ul>" if lis else '<div class="empty">none</div>'
    tools = _list_tools("#" + lid, kind) if lis else ""
    return ('<div class="col col-%s"><div class="col-h"><span>%s</span>'
            '<span class="ch-r"><span class="cnt">%d</span>%s</span></div>%s</div>'
            % (kind, label, len(items), tools, body))


def render_epic_page(ep):
    today = datetime.date.today()
    total = ep["open"] + ep["closed"] + ep["wf"]

    def col(kind, label, items, datekey, reverse):
        return _status_col(kind, label, items, datekey, reverse, today)

    def w(n):
        return (n / total * 100) if total else 0

    done_d = ep["effortDoneH"] / 8
    open_d = ep["effortOpenH"] / 8
    cols = [
        col("open", "Open — oldest first", ep["openTickets"], "created", False),
        col("closed", "Closed — newest first", ep["closedTickets"], "closed", True),
        col("wf", "Won't fix", ep["wfTickets"], "created", False),
    ]
    if ep.get("standingTickets"):
        cols.append(col("standing", "Standing — recurring",
                        ep["standingTickets"], "created", False))
    stale_open = sum(1 for t in ep["openTickets"]
                     if t.get("created") and parse_date(t["created"])
                     and (today - parse_date(t["created"])).days > 30)
    # ep["id"] already carries the E (e.g. "E007"), and the prefix is per-repo.
    charter = sorted(glob.glob(os.path.join(
        TICKETS_DIR, "%s-%s-*" % (PFX, ep["id"]), "%s-%s-*.md" % (PFX, ep["id"]))))
    charter_link = ""
    if charter:
        charter_link = ('&nbsp;&middot;&nbsp; <a class="back" href="/doc/%s">'
                        'epic charter &rarr;</a>'
                        % html.escape(os.path.relpath(charter[0], REPO_ROOT)))
    # The epic's on-disk id-slug (e.g. IF-E007-dashboard-ux), for the copy button.
    edirs = sorted(glob.glob(os.path.join(TICKETS_DIR, "%s-%s-*" % (PFX, ep["id"]))))
    epic_slug = (os.path.basename(edirs[0]) if edirs
                 else "%s-%s" % (PFX, ep["id"]))
    epic_copy = ("<button class='ecopy copy-one' type='button' data-copy='%s'"
                 " title='Copy this epic&#39;s id'>&#128203; Copy</button>"
                 % html.escape(epic_slug, quote=True))
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>" + html.escape("__PFX__-" + ep["id"]) + " &middot; epic</title>"
        "<link rel=\"icon\" href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>__FAVICON__</text></svg>\">"
        "<style>" + EPIC_CSS + "</style></head><body><div class='wrap'>"
        "<a class='back' href='/'>&larr; back to dashboard</a>" + charter_link +
        "<div class='ehead'>"
        "<div class='tid'>__PFX__-" + html.escape(ep["id"]) + epic_copy + "</div>"
        "<h1 class='title'><span class='e-emoji'>" + ep.get("emoji", "__FAVICON__")
        + "</span>" + html.escape(ep["title"]) + "</h1>"
        "<div class='bar' role='img' aria-label='" + str(ep["closed"]) + " closed, "
        + str(ep["open"]) + " open, " + str(ep["wf"]) + " won&#39;t-fix'>"
        "<span class='sg-done' style='width:" + "%.2f" % w(ep["closed"]) + "%'></span>"
        "<span class='sg-open' style='width:" + "%.2f" % w(ep["open"]) + "%'></span>"
        "<span class='sg-wf' style='width:" + "%.2f" % w(ep["wf"]) + "%'></span></div>"
        "<div class='estats'>"
        "<span><b>" + str(ep["closed"]) + "</b> closed</span>"
        "<span><b>" + str(ep["open"]) + "</b> open</span>"
        "<span><b>" + str(ep["wf"]) + "</b> won&#39;t-fix</span>"
        "<span>effort done <b>" + ("%g" % round(done_d, 1)) + "d</b></span>"
        "<span>effort remaining <b>" + ("%g" % round(open_d, 1)) + "d</b></span>"
        + (("<span>stale &gt;30d <b>" + str(stale_open) + "</b></span>")
           if stale_open else "")
        + (("<span><b>" + str(len(ep.get("standingTickets", []))) + "</b> standing</span>")
           if ep.get("standingTickets") else "")
        + (("<span>last closed <b>" + html.escape(ep["lastClosed"]) + "</b></span>")
           if ep["lastClosed"] else "")
        + "</div></div>"
        + list_filter_bar()
        # status groups stack full-width: open first, then closed, then won't-fix
        + "<div class='cols' style='grid-template-columns:1fr'>"
        + "".join(cols)
        + "</div></div>" + LIST_FILTER_SCRIPT + "</body></html>"
    )
    return page


# --------------------------------------------------------------------------- #
# Filtered ticket-list page (/filter?...)
# --------------------------------------------------------------------------- #
# The grid: one row per ticket, cells in real columns. The template lives in
# --gcols on the <ul> and is inherited by the header and every row, so the two
# can't drift apart. Rows stay <li><a> so the shared toolbar and the pin handler
# work here unchanged.
GRID_CSS = """
.wrap{max-width:1400px}
/* Every row rule is scoped to .col-grid — the epic page's `.col li a` (one class,
   two elements) out-specifies a bare `.g-row`, and it says display:flex. Without
   this the cells lay out as a wrapping flex line and nothing sits under its
   header, which is the whole point of the grid. Same reason the child indent has
   to be undone: a shifted row is a row whose cells no longer line up. */
.col-grid .g-head,.col-grid .g-row{display:grid;grid-template-columns:var(--gcols);
gap:10px;align-items:center;padding:9px 14px}
.col-grid .g-head{border-bottom:1px solid var(--line);font-family:var(--mono);
font-size:.58rem;letter-spacing:.1em;color:var(--mut);text-transform:uppercase}
.col-grid ul{list-style:none;margin:0;padding:0}
.col-grid li{border-bottom:1px solid var(--line-2,var(--line))}
.col-grid li:last-child{border-bottom:0}
.col-grid li.child a.g-row{margin-left:0;border-left:0;border-radius:0}
.col-grid .g-row{text-decoration:none;color:inherit;border-radius:0}
.col-grid .g-row:hover{background:var(--surface2)}
.col-grid .g-id{font-family:var(--mono);font-size:.74rem;color:var(--accent);white-space:nowrap}
.col-grid .g-sub{color:var(--mut);margin-right:3px}
.col-grid .g-ttl{font-size:.86rem;color:var(--ink);overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.col-grid .g-epic{overflow:hidden}
.col-grid .g-epic .mchip{cursor:pointer}
.col-grid .g-st{font-family:var(--mono);font-size:.56rem;letter-spacing:.06em;
text-align:center;border-radius:4px;padding:3px 0;white-space:nowrap}
/* the soft tints are mixed from the theme's own colours — this page has no
   --*-soft tokens (those are the dashboard's), and hardcoding them would break
   the moment you switch palette */
.col-grid .g-st.st-open{color:var(--accent);
background:color-mix(in srgb,var(--accent) 12%,transparent)}
.col-grid .g-st.st-closed{color:var(--done);
background:color-mix(in srgb,var(--done) 14%,transparent)}
.col-grid .g-st.st-wf{color:var(--wf);
background:color-mix(in srgb,var(--wf) 16%,transparent)}
.col-grid .g-p,.col-grid .g-risk,.col-grid .g-eff,.col-grid .g-date{
font-family:var(--mono);font-size:.68rem;color:var(--mut);white-space:nowrap}
.col-grid .g-risk.r-high{color:#c23b3b;font-weight:600}
.col-grid .g-risk.r-medium{color:var(--warn)}
.col-grid .g-age{opacity:.65;margin-left:6px}
.col-grid .g-flags{display:flex;gap:4px;justify-content:flex-end;align-items:center}
/* headings borrow the row's classes so they hide together — but not its looks */
.col-grid .g-head span{color:var(--mut);background:none;font:inherit;text-align:left;
padding:0;opacity:1}
/* a header is a sort control: it says what the column is, and orders by it */
.col-grid .gh-sort{cursor:pointer;user-select:none;display:inline-flex;align-items:center;gap:3px}
.col-grid .gh-sort:hover,.col-grid .gh-sort:focus-visible{color:var(--accent)}
.col-grid .gh-sort.sorted{color:var(--accent);font-weight:700}
.col-grid .gh-arrow{font-size:.8em;opacity:0}
.col-grid .gh-sort:hover .gh-arrow{opacity:.45}
.col-grid .gh-sort.sorted .gh-arrow{opacity:1}
.col-grid .gh-sort .gh-arrow::before{content:"\\2191"}                 /* ascending */
.col-grid .gh-sort[data-dir="-1"] .gh-arrow::before{content:"\\2193"}  /* descending */
/* narrow: the nice-to-have columns step aside; id, title and the date never do */
@media (max-width:1000px){
  .col-grid .g-head,.col-grid .g-row{grid-template-columns:92px minmax(0,1fr) 96px 52px}
  .col-grid .g-epic,.col-grid .g-st,.col-grid .g-p,.col-grid .g-risk,.col-grid .g-eff{display:none}
}
"""
def render_filter_page(data, q):
    """Cross-epic ticket list for a metric click-through.

    Query params (all optional): status=open,closed,wf · risk · priority ·
    quick=1 · blocked=y|n · stale=<days> · closed_from/closed_to ·
    created_from/created_to · created=today · closed=today · label=<heading>."""
    today = datetime.date.today()

    def qv(key, default=""):
        return (q.get(key, [default]) or [default])[0].strip()

    statuses = {s for s in qv("status", "open,closed,wf").lower().split(",") if s}
    risk = qv("risk").upper()
    priority = qv("priority")
    quick = qv("quick") == "1"
    wip = qv("wip") == "1"
    pinned = qv("pinned") == "1"
    blocked = qv("blocked").lower()
    stale = int(qv("stale")) if qv("stale").isdigit() else None
    c_from, c_to = parse_date(qv("closed_from")), parse_date(qv("closed_to"))
    n_from, n_to = parse_date(qv("created_from")), parse_date(qv("created_to"))
    # Shorthands for static links (the top bar's pocket): the server knows the
    # date, and POCKET_WINDOWS is the same table the badge counted with.
    windows = {k: d for k, d, _ in POCKET_WINDOWS}
    if qv("created") in windows:
        n_from, n_to = today - datetime.timedelta(days=windows[qv("created")]), today
    if qv("closed") in windows:
        c_from, c_to = today - datetime.timedelta(days=windows[qv("closed")]), today
    label = qv("label") or "Filtered tickets"

    def keep(st, t):
        if st not in statuses:
            return False
        if risk and t.get("risk") != risk:
            return False
        if priority and str(t.get("priority")) != priority:
            return False
        if quick and not (st == "open" and not t.get("blocked")
                          and t.get("effortH") is not None and t["effortH"] <= 4):
            return False
        if wip and not t.get("wip"):
            return False
        if pinned and not t.get("pinned"):
            return False
        if blocked == "y" and not t.get("blocked"):
            return False
        if blocked == "n" and t.get("blocked"):
            return False
        if stale is not None:
            c = parse_date(t.get("created"))
            if not (st == "open" and c and (today - c).days > stale):
                return False
        if c_from or c_to:
            x = parse_date(t.get("closed"))
            if not x or (c_from and x < c_from) or (c_to and x > c_to):
                return False
        if n_from or n_to:
            c = parse_date(t.get("created"))
            if not c or (n_from and c < n_from) or (n_to and c > n_to):
                return False
        return True

    # One list, whatever the query. Every arrival here asks the same thing —
    # which tickets, and what are they — so there is one answer: a grid you read
    # down. Status rides along as a cell, and only earns a column when the result
    # actually mixes statuses.
    rows = []
    for ep in data["epics"]:
        for st, key in (("open", "openTickets"), ("closed", "closedTickets"),
                        ("wf", "wfTickets")):
            rows.extend(dict(t, status=st) for t in ep[key] if keep(st, t))

    # Show the date the query is about: a closed window means you came here to
    # see what got closed, so that is the date on the row.
    datekey = "closed" if (c_from or c_to or statuses == {"closed"}) else "created"
    show_status = len({t["status"] for t in rows}) > 1
    grid = _ticket_grid(rows, datekey, today, show_status)

    n = len(rows)
    eff_d = sum(t["effortH"] for t in rows
                if t.get("effortH") is not None) / 8
    crit = []
    if statuses != {"open", "closed", "wf"}:
        crit.append("status: " + ", ".join(sorted(statuses)))
    if risk:
        crit.append("risk " + risk)
    if priority:
        crit.append("P" + priority)
    if quick:
        crit.append("&le;4h &middot; unblocked")
    if wip:
        crit.append("working-tree WIP")
    if pinned:
        crit.append("pinned")
    if blocked == "y":
        crit.append("blocked")
    if blocked == "n":
        crit.append("unblocked")
    if stale is not None:
        crit.append("open &gt;%dd" % stale)
    if c_from or c_to:
        crit.append("closed %s &rarr; %s" % (_iso(c_from) or "…", _iso(c_to) or "…"))
    if n_from or n_to:
        crit.append("created %s &rarr; %s" % (_iso(n_from) or "…", _iso(n_to) or "…"))

    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>" + html.escape(label) + " &middot; __BRAND__</title>"
        "<link rel=\"icon\" href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>__FAVICON__</text></svg>\">"
        "<style>" + EPIC_CSS + GRID_CSS + "</style></head><body><div class='wrap'>"
        "<a class='back' href='/'>&larr; back to dashboard</a>"
        "<div class='ehead'>"
        "<div class='tid'>FILTERED VIEW</div>"
        "<h1 class='title'>" + html.escape(label) + "</h1>"
        "<div class='estats'>"
        "<span><b>" + str(n) + "</b> tickets</span>"
        "<span>effort <b>" + ("%g" % round(eff_d, 1)) + "d</b></span>"
        + "".join("<span>" + c + "</span>" for c in crit)
        + "</div></div>"
        + list_filter_bar(sort_select=False)      # the grid's headers sort it
        + "<div class='cols' style='grid-template-columns:1fr'>" + grid + "</div>"
        + "</div>" + LIST_FILTER_SCRIPT + "</body></html>"
    )
    return page


# --------------------------------------------------------------------------- #
# Dashboard shell (static; data comes from /api/data)
# --------------------------------------------------------------------------- #
DASHBOARD_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__BRAND__ &middot; Ticket Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>__FAVICON__</text></svg>">
<style>
:root{--ground:#e9edf1;--surface:#fff;--surface-2:#f3f6f9;--ink:#101720;--ink-2:#3a4652;
--ink-mut:#5d6a77;--line:#d6dde5;--line-2:#e6ebf0;--accent:#2b53e6;--accent-ink:#1c3bb3;
--accent-soft:#dbe2fb;--done:#2f9e5b;--done-soft:#d3ecdc;--wf:#98a2ad;--wf-soft:#e2e6ea;
--warn:#b3781a;--shadow:0 1px 2px rgba(16,23,32,.06),0 8px 24px -12px rgba(16,23,32,.18);
--font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,Roboto,sans-serif;
--font-mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;--r:7px;}
@media (prefers-color-scheme:dark){:root{--ground:#0b0f15;--surface:#141b25;--surface-2:#0f1620;
--ink:#e8edf3;--ink-2:#c2ccd6;--ink-mut:#8b98a6;--line:#26313e;--line-2:#1d2732;--accent:#6a8dff;
--accent-ink:#a9bdff;--accent-soft:#1c2740;--done:#48c483;--done-soft:#16311f;--wf:#69747f;
--wf-soft:#222c37;--warn:#d9a441;--shadow:0 1px 2px rgba(0,0,0,.4),0 12px 30px -14px rgba(0,0,0,.6);}}
*{box-sizing:border-box}body{margin:0}
.page{background:var(--ground);color:var(--ink);font-family:var(--font-sans);line-height:1.5;
-webkit-font-smoothing:antialiased;padding:clamp(16px,4vw,44px) clamp(14px,4vw,32px) 64px;min-height:100vh}
.wrap{max-width:1160px;margin:0 auto}
.mono{font-family:var(--font-mono);font-variant-numeric:tabular-nums}
.eyebrow{font-family:var(--font-mono);font-size:.7rem;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-mut)}
.head{background:var(--surface);border:1px solid var(--line);border-radius:calc(var(--r) + 3px);
box-shadow:var(--shadow);padding:clamp(20px,3.5vw,34px);position:relative;overflow:hidden}
.head-top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:10px 20px;margin-top:6px}
/* the "today" pocket: created/closed in the window, riding in the header's
   button cluster. Same pill geometry as .ghbtn beside it — one row of controls,
   one centre line, and nothing that drifts when the eyebrow changes length. */
.today-pocket{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);
border-radius:100px;padding:7px 14px;background:var(--surface-2);line-height:1;min-height:15px}
.today-pocket .tp-lab{font-family:var(--font-mono);font-size:.62rem;letter-spacing:.09em;
text-transform:uppercase;color:var(--ink-mut);border:0;background:none;padding:0;
cursor:pointer}
.today-pocket .tp-lab:hover{color:var(--accent-ink)}
.today-pocket .tp-item{font-family:var(--font-mono);font-size:.72rem;color:var(--ink-mut);
text-decoration:none}
.today-pocket .tp-item b{font-weight:750;font-size:.82rem}
.today-pocket .tp-new b{color:var(--accent-ink)}
.today-pocket .tp-done b{color:var(--done)}
.today-pocket .tp-item:hover{color:var(--accent-ink)}
h1{font-size:clamp(1.9rem,4.4vw,2.9rem);line-height:1.02;letter-spacing:-.03em;font-weight:800;margin:.28em 0 0;text-wrap:balance}
.tagline{font-size:clamp(1rem,2vw,1.16rem);color:var(--ink-2);margin:.7em 0 0;max-width:62ch}
.tagline b{color:var(--ink);font-weight:600}
.stamp{font-family:var(--font-mono);font-size:.72rem;color:var(--ink-mut);border:1px solid var(--line);
border-radius:100px;padding:5px 11px;white-space:nowrap;display:inline-flex;gap:7px;align-items:center}
.stamp-row{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-top:16px}
.head-actions{display:inline-flex;gap:8px;flex-wrap:wrap}
.hbtn{font-family:var(--font-mono);font-size:.74rem;color:var(--accent-ink);text-decoration:none;
background:var(--surface-2);border:1px solid var(--line);border-radius:100px;padding:6px 14px;white-space:nowrap}
.hbtn:hover{border-color:var(--accent);background:var(--accent-soft)}
.stamp .dot{width:7px;height:7px;border-radius:50%;background:var(--done)}
.regen{font-family:var(--font-mono);font-size:.76rem;letter-spacing:.03em;background:var(--accent);
color:#fff;border:0;border-radius:100px;padding:8px 16px;cursor:pointer;display:inline-flex;gap:7px;align-items:center}
.regen:hover{filter:brightness(1.06)}.regen:disabled{opacity:.6;cursor:progress}
.regen .ic{display:inline-block}.regen.spin .ic{animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.meter-block{margin-top:22px}
.meter{height:34px;border-radius:6px;overflow:hidden;display:flex;background:var(--surface-2);border:1px solid var(--line)}
.meter span{display:block;height:100%;width:0;transition:width 700ms cubic-bezier(.2,.7,.2,1)}
.seg-done{background:var(--done)}.seg-open{background:var(--accent)}.seg-wf{background:var(--wf)}
.legend{display:flex;flex-wrap:wrap;gap:6px 20px;margin-top:12px}
.legend .li{display:flex;align-items:center;gap:8px;font-size:.86rem;color:var(--ink-2)}
.legend .sw{width:11px;height:11px;border-radius:3px;flex:none}
.legend .n{font-family:var(--font-mono);color:var(--ink);font-weight:600}
.kpis{display:grid;gap:12px;margin-top:16px;grid-template-columns:repeat(4,1fr)}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;
display:flex;flex-direction:column;gap:3px;position:relative}
.kpi .k-val{font-family:var(--font-sans);font-size:clamp(1.5rem,3.2vw,2rem);font-weight:750;letter-spacing:-.02em;line-height:1;display:flex;align-items:baseline;gap:8px}
.k-delta{font-family:var(--font-mono);font-size:.72rem;font-weight:600;letter-spacing:0}
.k-delta.up{color:var(--done)}.k-delta.down{color:#c23b3b}.k-delta.flat,.k-delta.neutral{color:var(--ink-mut)}
@media (prefers-color-scheme:dark){.k-delta.down{color:#e06c6c}}
.kpi .k-lab{font-size:.78rem;color:var(--ink-mut)}
.kpi .k-sub{font-family:var(--font-mono);font-size:.72rem;color:var(--ink-mut);margin-top:3px}
.kpi.is-open .k-val{color:var(--accent-ink)}.kpi.is-done .k-val{color:var(--done)}
.kpi::after{content:"";position:absolute;left:0;top:12px;bottom:12px;width:3px;border-radius:3px;background:var(--line-2)}
.kpi.is-open::after{background:var(--accent)}.kpi.is-done::after{background:var(--done)}.kpi.is-wf::after{background:var(--wf)}
section{margin-top:40px;scroll-margin-top:64px}
@media (prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
.qnav{position:sticky;top:10px;z-index:20;display:flex;flex-wrap:wrap;gap:2px;margin-top:14px;padding:5px;
background:var(--surface);border:1px solid var(--line);border-radius:100px;box-shadow:var(--shadow);width:fit-content}
.qnav a{font-family:var(--font-mono);font-size:.72rem;color:var(--ink-2);text-decoration:none;padding:5px 12px;border-radius:100px;white-space:nowrap}
.qnav a:hover{background:var(--surface-2);color:var(--accent-ink)}
.qnav a b{color:var(--accent-ink);font-weight:600;margin-right:6px}
.sec-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:14px}
h2{font-size:1.28rem;font-weight:700;letter-spacing:-.02em;margin:0;display:flex;align-items:baseline;gap:11px}
h2 .h-num{font-family:var(--font-mono);font-size:.82rem;color:var(--accent-ink);font-weight:600}
.sec-note{font-size:.9rem;color:var(--ink-mut);max-width:60ch;margin:0 0 14px}
/* notes that caption a full-width figure read better at the figure's width */
.sec-note.wide{max-width:none}
h2 a.sec-link{color:inherit;text-decoration:none}
h2 a.sec-link::after{content:" \2197";font-size:.62em;color:var(--ink-mut);vertical-align:super}
h2 a.sec-link:hover{color:var(--accent-ink)}
.controls{display:flex;gap:6px;background:var(--surface-2);border:1px solid var(--line);border-radius:100px;padding:4px}
.controls button{font-family:var(--font-mono);font-size:.72rem;letter-spacing:.03em;border:0;background:transparent;
color:var(--ink-mut);padding:5px 12px;border-radius:100px;cursor:pointer}
.controls button[aria-pressed="true"]{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.12)}
.epics{background:var(--surface);border:1px solid var(--line);border-radius:calc(var(--r) + 2px);box-shadow:var(--shadow);padding:8px clamp(10px,2vw,18px)}
.erow{display:grid;grid-template-columns:minmax(0,1fr) 132px;gap:14px;align-items:center;padding:13px 4px;border-bottom:1px solid var(--line-2)}
.erow:last-child{border-bottom:0}.e-main{min-width:0}
.e-title{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.e-id{font-family:var(--font-mono);font-size:.74rem;color:var(--accent-ink);font-weight:600;flex:none}
.e-name{font-size:.95rem;font-weight:600;color:var(--ink);letter-spacing:-.01em}
.e-chip{font-family:var(--font-mono);font-size:.63rem;letter-spacing:.04em;text-transform:uppercase;padding:2px 7px;border-radius:4px;flex:none}
.chip-complete{background:var(--done-soft);color:var(--done)}.chip-active{background:var(--accent-soft);color:var(--accent-ink)}
.chip-progress{background:var(--surface-2);color:var(--ink-mut);border:1px solid var(--line)}
.chip-notstarted{background:var(--wf-soft);color:var(--ink-mut)}.chip-finish{background:var(--accent-soft);color:var(--accent-ink)}
.ebar{height:12px;border-radius:4px;overflow:hidden;display:flex;background:var(--surface-2);margin-top:9px;border:1px solid var(--line-2)}
.ebar span{height:100%}
.e-counts{font-family:var(--font-mono);font-size:.8rem;text-align:right;color:var(--ink-mut);display:flex;flex-direction:column;gap:1px;align-items:flex-end}
.e-counts .cc{display:flex;gap:6px;justify-content:flex-end}.e-counts b{font-weight:700}
.cc-open b{color:var(--accent-ink)}.cc-done b{color:var(--done)}.cc-wf b{color:var(--ink-mut)}
.cc.zero{opacity:.4}
.tracks{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.track{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:15px 16px}
.track .t-val{font-family:var(--font-mono);font-size:1.5rem;font-weight:700;line-height:1}
.track .t-lab{font-size:.82rem;color:var(--ink-2);margin-top:5px}
.track .t-list{font-family:var(--font-mono);font-size:.7rem;color:var(--ink-mut);margin-top:7px;line-height:1.7}
.backlog{columns:3 310px;column-gap:14px} /* masonry: cards pack, no row gaps */
.bcard{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);overflow:hidden;
break-inside:avoid;margin:0 0 14px;display:block}
.bcard.span-all{column-span:all}
.bcard.hl{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),var(--shadow)}
.bcard>summary{list-style:none;cursor:pointer;padding:13px 15px;display:flex;align-items:center;gap:10px;border-bottom:1px solid transparent}
.bcard[open]>summary{border-bottom-color:var(--line-2)}.bcard>summary::-webkit-details-marker{display:none}
.b-caret{color:var(--ink-mut);font-family:var(--font-mono);font-size:.7rem;transition:transform .15s;flex:none}
.bcard[open] .b-caret{transform:rotate(90deg)}
.b-head-main{min-width:0;flex:1}.b-head-id{font-family:var(--font-mono);font-size:.72rem;color:var(--accent-ink);font-weight:600}
.b-head-name{font-size:.9rem;font-weight:600;letter-spacing:-.01em;display:block;margin-top:1px}
.b-count{font-family:var(--font-mono);font-size:.78rem;font-weight:700;color:var(--accent-ink);flex:none}
.b-count small{color:var(--ink-mut);font-weight:400}
.b-list{list-style:none;margin:0;padding:6px 6px 10px}
.b-list li{padding:0}
.b-list a{display:flex;flex-wrap:wrap;gap:4px 9px;padding:7px 9px;border-radius:5px;align-items:baseline;text-decoration:none;color:inherit}
.b-list a:hover{background:var(--surface-2)}
.b-list a:hover .b-ttl{color:var(--accent-ink)}
.b-tid{font-family:var(--font-mono);font-size:.72rem;color:var(--ink-mut);flex:none;min-width:80px}
.b-ttl{font-size:.84rem;color:var(--ink-2);line-height:1.35;flex:1 1 55%;min-width:0}
.b-dec{font-family:var(--font-mono);font-size:.58rem;letter-spacing:.05em;color:var(--warn);border:1px solid var(--warn);border-radius:3px;padding:0 4px;margin-left:6px;white-space:nowrap}
.b-status{font-family:var(--font-mono);font-size:.62rem;letter-spacing:.04em;text-transform:uppercase;padding:2px 7px;border-radius:4px;flex:none}
.backlog-tools{display:flex;gap:10px;align-items:center}
.lnk{font-family:var(--font-mono);font-size:.72rem;color:var(--accent-ink);background:none;border:0;cursor:pointer;padding:0}
.lnk:hover{text-decoration:underline}
footer{margin-top:44px;border-top:1px solid var(--line);padding-top:18px;color:var(--ink-mut);font-size:.8rem}
footer .mono{color:var(--ink-2)}footer p{margin:.4em 0}
a{color:var(--accent-ink)}:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
@media (max-width:720px){.kpis{grid-template-columns:repeat(2,1fr)}.tracks{grid-template-columns:1fr}
.erow{grid-template-columns:1fr;gap:8px}.e-counts{flex-direction:row;gap:14px;text-align:left}}
@media (prefers-reduced-motion:reduce){.meter span{transition:none}.regen.spin .ic{animation:none}}
/* chart series colours (validated: created cobalt / closed green, CVD-safe both modes) */
:root{--c-created:#2b53e6;--c-closed:#2f9e5b}
@media (prefers-color-scheme:dark){:root{--c-created:#3987e5;--c-closed:#199e70}}
:root[data-theme="light"]{--c-created:#2b53e6;--c-closed:#2f9e5b}
:root[data-theme="dark"]{--c-created:#3987e5;--c-closed:#199e70}
.charts{display:grid;gap:16px;margin-top:16px}
@media (min-width:880px){.charts{grid-template-columns:1fr 1fr}}
.chart{margin:0;background:var(--surface);border:1px solid var(--line);border-radius:calc(var(--r) + 2px);box-shadow:var(--shadow);padding:14px 16px 12px}
.chart-cap{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.chart-title{font-size:.86rem;font-weight:600;letter-spacing:-.01em}
a.chart-title{color:inherit;text-decoration:none}
a.chart-title::after{content:" \2197";color:var(--ink-mut);font-weight:400}
a.chart-title:hover{color:var(--accent-ink)}
a.chart-title:hover::after{color:var(--accent-ink)}
.chart-note{font-size:.73rem;color:var(--ink-mut);margin:8px 0 0}
.chart-plot{position:relative;width:100%}
.chart-plot svg{display:block;width:100%;height:auto;overflow:visible}
.legend2{display:flex;gap:14px}
.lg2{display:flex;align-items:center;gap:6px;font-size:.72rem;color:var(--ink-2);font-family:var(--font-mono)}
.sw2{width:18px;height:3px;border-radius:2px;display:inline-block}
.sw-created{background:var(--c-created)}.sw-closed{background:var(--c-closed)}
.grid-line{stroke:var(--line-2)}
.axis-txt{fill:var(--ink-mut);font-family:var(--font-mono)}
.cross{stroke:var(--ink-mut);stroke-dasharray:3 3}
.ln-created{fill:none;stroke:var(--c-created);stroke-width:2;stroke-linejoin:round}
.ln-closed{fill:none;stroke:var(--c-closed);stroke-width:2;stroke-linejoin:round}
.ar-created{fill:var(--c-created);opacity:.09}
.ar-closed{fill:var(--c-closed);opacity:.16}
.dot-created{fill:var(--c-created)}.dot-closed{fill:var(--c-closed)}
.bar-created{fill:var(--c-created)}.bar-closed{fill:var(--c-closed)}
.ctip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--line);border-radius:8px;
box-shadow:var(--shadow);padding:8px 10px;font-size:.72rem;font-family:var(--font-mono);color:var(--ink);opacity:0;
transition:opacity .1s;white-space:nowrap;z-index:6;transform:translate(-50%,-112%);min-width:120px}
.ctip b{color:var(--ink);display:block;margin-bottom:4px}
.ctip .row{display:flex;justify-content:space-between;gap:16px}
.ctip .k{color:var(--ink-mut)}
.chart-empty{color:var(--ink-mut);font-size:.82rem;padding:34px 12px;text-align:center}
.sortlab{font-family:var(--font-mono);font-size:.72rem;color:var(--ink-mut);display:flex;align-items:center;gap:6px}
.sortsel{font-family:var(--font-mono);font-size:.72rem;color:var(--ink);background:var(--surface-2);border:1px solid var(--line);border-radius:7px;padding:5px 9px;cursor:pointer}
.b-toggle{display:inline-flex;border:1px solid var(--line);border-radius:100px;overflow:hidden;flex:none}
.b-toggle button{font-family:var(--font-mono);font-size:.62rem;letter-spacing:.02em;border:0;background:var(--surface-2);color:var(--ink-mut);padding:3px 10px;cursor:pointer}
.b-toggle button[aria-pressed="true"]{background:var(--accent);color:#fff}
.b-date{font-family:var(--font-mono);font-size:.68rem;color:var(--ink-mut);flex:none;white-space:nowrap}
.b-list a.closed .b-tid{color:var(--done)}
.b-list a.closed .b-ttl{color:var(--ink-mut)}
.b-right{margin-left:auto;display:inline-flex;gap:8px;align-items:baseline;padding-left:10px;flex:none}
.b-metas{display:inline-flex;gap:4px}
.b-meta{font-family:var(--font-mono);font-size:.6rem;letter-spacing:.02em;border:1px solid var(--line);border-radius:4px;padding:1px 5px;color:var(--ink-mut);white-space:nowrap}
.b-meta.r-high{color:#c23b3b;border-color:#c23b3b}
.b-meta.r-medium{color:var(--warn);border-color:var(--warn)}
.b-meta.r-low{color:var(--done);border-color:var(--done)}
@media (prefers-color-scheme:dark){.b-meta.r-high{color:#e06c6c;border-color:#e06c6c}}
.searchbox{font-family:var(--font-mono);font-size:.74rem;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:100px;padding:6px 12px;width:158px}
.searchbox::placeholder{color:var(--ink-mut)}
.sortlab.chk{cursor:pointer;user-select:none}
.sortlab.chk input{accent-color:var(--accent);margin:0 2px 0 0;cursor:pointer}
.backlog-tools{flex-wrap:wrap;row-gap:8px}
.recent{background:var(--surface);border:1px solid var(--line);border-radius:calc(var(--r) + 2px);box-shadow:var(--shadow);padding:6px 10px}
.recent a{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;padding:9px 10px;border-radius:5px;text-decoration:none;color:inherit;border-bottom:1px solid var(--line-2)}
.recent .b-ttl{flex:1 1 45%}
.recent a:last-of-type{border-bottom:0}
.recent a:hover{background:var(--surface-2)}
.recent a:hover .b-ttl{color:var(--accent-ink)}
.recent .b-tid{color:var(--done)}
.meter-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px}
.kpi.clickable{cursor:pointer}
.kpi.clickable:hover{border-color:var(--accent)}
@media (max-width:640px){.b-right{display:none}}
/* priority × risk matrix — ordinal blue ramp, validated (light on #fff, dark on #141b25) */
:root{--mx-1:#86b6ef;--mx-2:#5598e7;--mx-3:#2a78d6;--mx-4:#1c5cab}
@media (prefers-color-scheme:dark){:root{--mx-1:#184f95;--mx-2:#256abf;--mx-3:#3987e5;--mx-4:#86b6ef}}
.matrix{display:grid;grid-template-columns:auto repeat(3,1fr);gap:2px;margin-top:6px}
.mx-lab{font-family:var(--font-mono);font-size:.66rem;color:var(--ink-mut);display:flex;align-items:center;justify-content:center;padding:4px 8px}
.mx-lab.row{justify-content:flex-end}
/* fill the card so the plot's bottom edge lines up with its neighbour's */
.chart.fill{display:flex;flex-direction:column}
.chart.fill>div{flex:1 1 auto;display:flex;flex-direction:column;justify-content:center;min-height:0}
.chart.fill .chart-note{margin-top:12px}
.chart.fill .matrix{flex:1 1 auto;min-height:216px}
.mx-cell{border-radius:5px;min-height:56px;display:flex;flex-direction:column;align-items:center;justify-content:center;
gap:1px;background:var(--surface-2);border:0;cursor:pointer;font-family:inherit;padding:6px 4px}
.mx-cell:hover{outline:2px solid var(--accent);outline-offset:1px}
.mx-cell .n{font-size:1.05rem;font-weight:750;line-height:1}
.mx-cell .d{font-family:var(--font-mono);font-size:.62rem;opacity:.85}
.mx-cell.empty{cursor:default;color:var(--ink-mut)}
.mx-cell.empty:hover{outline:0}
/* in-fill labels pick white/ink by the fill's luminance, per mode */
.mx-cell.s1{background:var(--mx-1);color:#101720}.mx-cell.s2{background:var(--mx-2);color:#101720}
.mx-cell.s3{background:var(--mx-3);color:#fff}.mx-cell.s4{background:var(--mx-4);color:#fff}
@media (prefers-color-scheme:dark){
.mx-cell.s1,.mx-cell.s2,.mx-cell.s3{color:#fff}
.mx-cell.s4{background:var(--mx-4);color:#0b0f15}}
/* the ramp inverts by mode (dark bg -> brighter = more), so the legend must too */
.mx-legend .dm{display:none}
@media (prefers-color-scheme:dark){.mx-legend .lm{display:none}.mx-legend .dm{display:inline}}
:root[data-theme="light"] .mx-legend .lm{display:inline}
:root[data-theme="light"] .mx-legend .dm{display:none}
:root[data-theme="dark"] .mx-legend .lm{display:none}
:root[data-theme="dark"] .mx-legend .dm{display:inline}
a.b-head-id{text-decoration:none}a.b-head-id:hover{text-decoration:underline}
.b-meta.epic-link{cursor:pointer}
.b-meta.epic-link:hover,.b-meta.epic-link:focus-visible{color:var(--accent-ink);border-color:var(--accent-ink);text-decoration:underline}
a.e-name{text-decoration:none;color:var(--ink)}a.e-name:hover{color:var(--accent-ink)}
/* effort-remaining-by-epic bars — single series, direct-labeled */
.ebars{margin-top:8px;display:flex;flex-direction:column;gap:7px}
.ebar-row{display:grid;grid-template-columns:170px minmax(0,1fr);gap:10px;align-items:center;text-decoration:none;color:inherit;border-radius:5px;padding:2px 4px;overflow:hidden}
a.ebar-row:hover{background:var(--surface-2)}
a.ebar-row:hover .ebr-name{color:var(--accent-ink)}
.ebr-name{font-size:.76rem;color:var(--ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:right}
.ebr-name b{font-family:var(--font-mono);font-size:.68rem;font-weight:600;color:var(--accent-ink)}
.ebr-track{display:flex;align-items:center;gap:8px;min-width:0}
.ebr-bar{height:14px;border-radius:0 4px 4px 0;background:var(--accent);flex:none}
.ebr-val{font-family:var(--font-mono);font-size:.68rem;color:var(--ink-mut);white-space:nowrap}
/* up-next planning list + two-panel recent activity */
.duo{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:880px){.duo{grid-template-columns:1fr}}
.panel-h{font-family:var(--font-mono);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-mut);
padding:10px 14px;border-bottom:1px solid var(--line-2);background:var(--surface-2)}
.recent{padding:0 10px 6px}
.recent .panel-h{margin:0 -10px 4px;border-radius:calc(var(--r) + 2px) calc(var(--r) + 2px) 0 0}
.panel-h.ph-tools{display:flex;align-items:center;gap:10px}
.upnext .b-meta.why{color:var(--accent-ink);border-color:var(--accent-ink)}
.b-meta.blocked{color:var(--warn);border-color:var(--warn)}
.b-meta.unblocks{color:var(--accent-ink);border-color:var(--accent-ink)}
/* dependency chip: present but quiet, until you go looking for it */
.b-meta.dep{border-color:transparent;color:var(--ink-mut);opacity:.42;cursor:pointer;padding:1px 3px}
.b-list a:hover .b-meta.dep{opacity:.85}
.b-meta.dep:hover,.b-meta.dep:focus-visible{opacity:1;color:var(--accent-ink);border-color:var(--accent-ink)}
/* chart table view */
.ctable{max-height:260px;overflow:auto;border:1px solid var(--line-2);border-radius:6px}
.ctable table{border-collapse:collapse;width:100%;font-size:.74rem;font-family:var(--font-mono);font-variant-numeric:tabular-nums}
.ctable th,.ctable td{padding:5px 10px;text-align:right;border-bottom:1px solid var(--line-2)}
.ctable th:first-child,.ctable td:first-child{text-align:left}
.ctable th{position:sticky;top:0;background:var(--surface-2);color:var(--ink-mut);font-weight:600}
.chart .tv{margin-left:auto}
@media (max-width:720px){.ebar-row{grid-template-columns:120px minmax(0,1fr)}}
/* global jump-to search */
.gsearch-row{position:relative;margin-top:18px}
.gsearch{width:100%;font-family:var(--font-mono);font-size:.88rem;color:var(--ink);background:var(--surface-2);
border:1px solid var(--line);border-radius:10px;padding:11px 44px 11px 14px}
.gsearch:focus{outline:2px solid var(--accent);outline-offset:1px;background:var(--surface)}
.gsearch::placeholder{color:var(--ink-mut)}
.g-hint{font-family:var(--font-mono);font-size:.66rem;color:var(--ink-mut);position:absolute;right:12px;top:50%;
transform:translateY(-50%);pointer-events:none;border:1px solid var(--line);border-radius:5px;padding:1px 7px;background:var(--surface)}
.gdrop{position:absolute;left:0;right:0;top:calc(100% + 6px);z-index:40;background:var(--surface);
border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow);max-height:420px;overflow:auto;padding:6px}
.gdrop a{display:flex;gap:10px;align-items:baseline;padding:8px 10px;border-radius:7px;text-decoration:none;color:inherit}
.gdrop a.active,.gdrop a:hover{background:var(--accent-soft)}
.g-kind{font-family:var(--font-mono);font-size:.6rem;letter-spacing:.05em;text-transform:uppercase;border-radius:4px;
padding:2px 0;flex:none;width:58px;text-align:center}
.g-kind.k-epic{background:var(--accent);color:#fff}
.g-kind.k-open{background:var(--accent-soft);color:var(--accent-ink)}
.g-kind.k-closed{background:var(--done-soft);color:var(--done)}
.g-kind.k-wf,.g-kind.k-standing{background:var(--wf-soft);color:var(--ink-mut)}
.g-id{font-family:var(--font-mono);font-size:.74rem;font-weight:600;color:var(--accent-ink);flex:none;min-width:92px}
.g-ttl{font-size:.85rem;color:var(--ink-2);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.g-sub{font-family:var(--font-mono);font-size:.66rem;color:var(--ink-mut);flex:none}
.g-empty{padding:10px 12px;font-size:.78rem;color:var(--ink-mut);font-family:var(--font-mono)}
.track .t-list a{color:var(--accent-ink);text-decoration:none}
.track .t-list a:hover{text-decoration:underline}
a.e-id{text-decoration:none}a.e-id:hover{text-decoration:underline}
/* open-ticket age buckets — ordinal ramp, darker = older */
.ages{display:grid;grid-template-columns:repeat(4,1fr);gap:2px;margin-top:6px}
.age-cell{border-radius:5px;min-height:64px;display:flex;flex-direction:column;align-items:center;
justify-content:center;gap:2px;text-decoration:none;padding:8px 4px}
.age-cell:hover{outline:2px solid var(--accent);outline-offset:1px}
.age-cell .n{font-size:1.15rem;font-weight:750;line-height:1}
.age-cell .d{font-family:var(--font-mono);font-size:.64rem;opacity:.85}
.age-cell.s1{background:var(--mx-1);color:#101720}.age-cell.s2{background:var(--mx-2);color:#101720}
.age-cell.s3{background:var(--mx-3);color:#fff}.age-cell.s4{background:var(--mx-4);color:#fff}
@media (prefers-color-scheme:dark){.age-cell.s1,.age-cell.s2,.age-cell.s3{color:#fff}.age-cell.s4{color:#0b0f15}}
.age-cell.zero{background:var(--surface-2);color:var(--ink-mut)}
.b-meta.wip{color:var(--accent-ink);border-color:var(--accent-ink);font-weight:600}
.e-emo{flex:none}
.b-emo{flex:none;font-size:1rem}
.g-emo{flex:none}
/* github button + latest activity + adr search + dep graph + epic tiles */
.head-btns{display:inline-flex;gap:8px;align-items:center;flex-wrap:wrap}
/* notes / to-do pop-out */
.tag-row{display:flex;align-items:center;justify-content:space-between;gap:10px 14px;flex-wrap:wrap;margin-top:.7em}
.tag-row .tagline{margin:0}
.note-btns{display:inline-flex;gap:8px;flex:none}
.notebtn{position:relative;font-size:1rem;border:1px solid var(--line);border-radius:100px;
width:38px;height:32px;cursor:pointer;line-height:1;padding:0;display:inline-flex;align-items:center;justify-content:center}
.nb-notes{background:rgba(234,179,8,.16)}
.nb-notes:hover,.nb-notes.active{border-color:#b45309;background:rgba(234,179,8,.32)}
.nb-todo{background:rgba(220,38,38,.12)}
.nb-todo:hover,.nb-todo.active{border-color:#c23b3b;background:rgba(220,38,38,.26)}
/* per-project quick links — fully custom, unlimited, defined in the config */
.proj-links{display:inline-flex;gap:8px;flex:none;align-items:center}
.proj-links:empty{display:none}
.proj-link{position:relative;font-size:1rem;text-decoration:none;border:1px solid var(--line);border-radius:100px;
width:38px;height:32px;line-height:1;display:inline-flex;align-items:center;justify-content:center;background:var(--surface-2)}
.proj-link:hover,.proj-link:focus-visible{border-color:var(--accent);background:var(--accent-soft)}
.proj-link .pl-emo{display:inline-flex;pointer-events:none}
/* title overlay on hover/focus so you know where a link goes */
.pl-tip{position:absolute;top:calc(100% + 9px);left:50%;transform:translate(-50%,-4px);
white-space:nowrap;font-family:var(--font-mono);font-size:.72rem;letter-spacing:.01em;color:var(--ink);
background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:5px 10px;
box-shadow:var(--shadow);opacity:0;pointer-events:none;transition:opacity .12s ease,transform .12s ease;z-index:400}
.pl-tip::before{content:"";position:absolute;bottom:100%;left:50%;width:8px;height:8px;
background:var(--surface);border-left:1px solid var(--line);border-top:1px solid var(--line);
transform:translate(-50%,50%) rotate(45deg)}
.proj-link:hover .pl-tip,.proj-link:focus-visible .pl-tip,
.notebtn:hover .pl-tip,.notebtn:focus-visible .pl-tip{opacity:1;transform:translate(-50%,0)}
/* standard mark shown after the dashboard title */
.h-emo{font-size:.72em;vertical-align:.06em;margin-left:.06em}
.notepanel{position:fixed;top:0;right:0;bottom:0;width:min(460px,92vw);z-index:500;display:flex;flex-direction:column;
background:var(--surface);border-left:1px solid var(--line);box-shadow:-18px 0 44px rgba(0,0,0,.22)}
.notepanel[hidden]{display:none}
.np-head{display:flex;align-items:center;gap:9px;padding:12px 14px;border-bottom:1px solid var(--line);flex:none}
.np-emo{flex:none}
.np-title{font-weight:700;font-size:.9rem;letter-spacing:-.01em}
.np-file{font-family:var(--font-mono);font-size:.66rem;color:var(--ink-mut);background:var(--surface-2);
border:1px solid var(--line-2);border-radius:5px;padding:2px 7px;white-space:nowrap}
.np-status{font-family:var(--font-mono);font-size:.66rem;color:var(--ink-mut);margin-left:auto;white-space:nowrap}
.np-close{font-size:1.05rem;border:1px solid var(--line);background:var(--surface-2);color:var(--ink-mut);
border-radius:6px;width:28px;height:28px;cursor:pointer;line-height:1;padding:0;flex:none}
.np-close:hover{color:#c23b3b;border-color:#c23b3b}
#npText{flex:1;width:100%;resize:none;border:0;outline:0;background:transparent;color:var(--ink);
padding:14px 16px;font-family:var(--font-mono);font-size:.82rem;line-height:1.6}
.np-todo{flex:1;display:flex;flex-direction:column;min-height:0}
.np-todo[hidden]{display:none}
.np-add{padding:12px 14px;border-bottom:1px solid var(--line-2);flex:none}
.np-add input{width:100%;font-family:var(--font-mono);font-size:.8rem;padding:8px 11px;outline:none;
border:1px solid var(--line);border-radius:7px;background:var(--surface-2);color:var(--ink)}
.np-add input:focus{border-color:var(--accent)}
.np-items{flex:1;overflow-y:auto;padding:6px 8px}
.np-it{display:flex;align-items:flex-start;gap:9px;padding:7px 9px;border-radius:7px}
.np-it:hover{background:var(--surface-2)}
.np-it input[type=checkbox]{margin-top:2px;accent-color:var(--accent);width:15px;height:15px;flex:none;cursor:pointer}
.np-it .t{flex:1;font-size:.84rem;line-height:1.45;word-break:break-word}
.np-it.done .t{color:var(--ink-mut);text-decoration:line-through}
.np-tid{font-family:var(--font-mono,ui-monospace,monospace);font-size:.78rem;color:var(--accent);
  text-decoration:none;border-bottom:1px dotted currentColor;white-space:nowrap}
.np-tid:hover{opacity:.72}
.np-tid.done{color:var(--done)}
.np-tid.done::after{content:" ✓"}
.np-it .del{opacity:0}
.np-it:hover .del,.np-it .del:focus-visible{opacity:1}
.np-empty{font-size:.82rem;color:var(--ink-mut);padding:10px 9px;margin:0}
.np-done-h{font-family:var(--font-mono);font-size:.62rem;letter-spacing:.08em;text-transform:uppercase;
color:var(--ink-mut);padding:12px 9px 4px;border-top:1px solid var(--line-2);margin-top:8px}
.np-foot{flex:none;padding:9px 14px;border-top:1px solid var(--line-2)}
.np-foot button{font-family:var(--font-mono);font-size:.68rem;color:var(--ink-mut);background:none;border:0;cursor:pointer;padding:0}
.np-foot button:hover{color:var(--accent-ink)}
.np-ico{font-size:.9rem;border:1px solid var(--line);background:var(--surface-2);color:var(--ink-mut);
border-radius:6px;width:28px;height:28px;cursor:pointer;line-height:1;padding:0;flex:none}
.np-ico:hover{border-color:var(--accent);color:var(--accent-ink)}
.np-ico.on{border-color:var(--accent);color:var(--accent-ink);background:var(--accent-soft)}
.np-ico[hidden]{display:none}
.np-prev{flex:1;overflow-y:auto;padding:14px 16px;font-size:.86rem;line-height:1.6;color:var(--ink)}
.np-prev[hidden]{display:none}
.np-prev>*:first-child{margin-top:0}
.np-prev h1,.np-prev h2,.np-prev h3,.np-prev h4{margin:1.1em 0 .4em;line-height:1.25}
.np-prev h1{font-size:1.14rem}.np-prev h2{font-size:1.02rem}.np-prev h3{font-size:.93rem}
.np-prev p{margin:.6em 0}
.np-prev ul,.np-prev ol{margin:.6em 0;padding-left:1.35em}
.np-prev li{margin:.25em 0}
.np-prev a{color:var(--accent-ink)}
.np-prev code{font-family:var(--font-mono);font-size:.82em;background:var(--surface-2);
border:1px solid var(--line);border-radius:4px;padding:1px 5px}
.np-prev pre{background:var(--surface-2);border:1px solid var(--line);border-radius:7px;padding:10px 12px;overflow-x:auto}
.np-prev pre code{border:0;background:none;padding:0}
.np-prev blockquote{margin:.6em 0;padding-left:12px;border-left:3px solid var(--line);color:var(--ink-mut)}
.np-prev table{border-collapse:collapse;width:100%;font-size:.8rem;margin:.6em 0}
.np-prev td,.np-prev th{border:1px solid var(--line);padding:5px 8px;text-align:left}
.np-prev img{max-width:100%}
.np-prev hr{border:0;border-top:1px solid var(--line);margin:1em 0}
.ghbtn{display:inline-flex;align-items:center;gap:8px;font-family:var(--font-mono);font-size:.74rem;
color:var(--ink);text-decoration:none;background:var(--surface-2);border:1px solid var(--line);
border-radius:100px;padding:7px 14px;white-space:nowrap}
.ghbtn:hover{border-color:var(--accent);background:var(--accent-soft);color:var(--accent-ink)}
.ghbtn svg{width:15px;height:15px;fill:currentColor;flex:none}
.ghbtn .gh-sub{color:var(--ink-mut);border-left:1px solid var(--line);padding-left:8px}
.lw{display:flex;flex-direction:column}
.lw-row{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;padding:12px 10px;border-radius:7px;
text-decoration:none;color:inherit;border-bottom:1px solid var(--line-2)}
.lw-row:last-child{border-bottom:0}
.lw-row:hover{background:var(--surface-2)}
.lw-row:hover .b-ttl{color:var(--accent-ink)}
.lw-k{font-family:var(--font-mono);font-size:.62rem;letter-spacing:.08em;text-transform:uppercase;
color:var(--ink-mut);flex:none;min-width:46px}
.lw .b-ttl{flex:1;min-width:0}
.g-kind.k-adr{background:var(--warn);color:#fff}
.g-kind.k-doc{background:var(--surface-2);color:var(--ink-mut);border:1px solid var(--line)}
.dg-wrap{position:relative;overflow-x:auto;border:1px solid var(--line);border-radius:9px;
background:var(--surface);box-shadow:var(--shadow);padding:6px}
.dg-edge{fill:none;stroke:var(--line);stroke-width:1.5;transition:opacity .12s,stroke .12s}
.dg-node{fill:var(--surface-2);stroke:var(--line);stroke-width:1}
.dg-node.root{fill:var(--accent-soft);stroke:var(--accent)}
.dg-wrap svg a{transition:opacity .12s}
.dg-wrap svg a:hover .dg-node{stroke:var(--accent);stroke-width:1.5}
.dg-txt{font-family:var(--font-mono);font-size:10px;font-weight:600;fill:var(--accent-ink)}
.dg-ttl{font-family:var(--font-sans);font-size:10px;fill:var(--ink-mut)}
svg.dg-dim>a{opacity:.14}
svg.dg-dim .dg-edge{opacity:.08}
svg.dg-dim>a.hl{opacity:1}
svg.dg-dim .dg-edge.hl{opacity:1;stroke:var(--accent);stroke-width:2}
#dgTip{white-space:normal;max-width:300px;transform:translate(-50%,-108%)}
.etiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(172px,1fr));gap:10px;padding:8px 0}
.etile{display:flex;flex-direction:column;gap:5px;background:var(--surface-2);border:1px solid var(--line-2);
border-radius:9px;padding:12px 12px 11px;text-decoration:none;color:inherit;position:relative}
.etile:hover{border-color:var(--accent);background:var(--surface)}
.et-emo{font-size:1.35rem;line-height:1}
.et-id{font-family:var(--font-mono);font-size:.68rem;font-weight:600;color:var(--accent-ink)}
.et-name{font-size:.8rem;font-weight:600;letter-spacing:-.01em;color:var(--ink);line-height:1.25;min-height:2.5em}
.etile .ebar{margin-top:0;height:8px}
.et-sub{font-family:var(--font-mono);font-size:.64rem;color:var(--ink-mut)}
.etile .e-chip{position:absolute;top:10px;right:10px}
/* pinned + wip strip at the top */
.topstrip{margin-top:16px}
.topstrip .b-ttl{flex:1 1 40%}
.topstrip .g-kind{align-self:center}
.topstrip.solo{grid-template-columns:1fr}
.pin-x{font-family:var(--font-mono);font-size:.66rem;border:1px solid var(--line);background:var(--surface-2);
color:var(--ink-mut);border-radius:5px;padding:2px 7px;cursor:pointer;flex:none}
.pin-x:hover{border-color:#c23b3b;color:#c23b3b}
.b-meta.pinned{color:var(--warn);border-color:var(--warn)}
.b-meta.pinned[data-unpin]{cursor:pointer}
.b-meta.pinned[data-unpin]:hover,.b-meta.pinned[data-unpin]:focus-visible{color:#c23b3b;border-color:#c23b3b;text-decoration:line-through}
/* collapsible sections */
.sec-tgl{font-family:var(--font-mono);font-size:.8rem;border:1px solid var(--line);background:var(--surface);
color:var(--ink-mut);border-radius:6px;width:26px;height:26px;cursor:pointer;line-height:1;padding:0;flex:none;align-self:center}
.sec-tgl:hover{color:var(--accent-ink);border-color:var(--accent)}
section.collapsed>*:not(.sec-head){display:none}
section.collapsed .controls,section.collapsed .backlog-tools{display:none}
section.collapsed{margin-top:26px}
section.collapsed .sec-head{margin-bottom:0}
</style></head><body>
<div class="page"><div class="wrap">
  <header class="head">
    <div class="head-top">
      <span class="eyebrow">__EYEBROW__</span>
      <span class="head-btns">
        __POCKET__
        <a id="ghBtn" class="ghbtn" hidden target="_blank" rel="noopener">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
          GitHub <span class="gh-sub" id="ghSub"></span></a>
        <span class="proj-links" id="projLinks">__PROJECT_LINKS__</span>
        <span class="note-btns">
          <button class="notebtn nb-notes" type="button" data-which="scratch" aria-label="Open scratchpad"><span class="pl-emo">&#128221;</span><span class="pl-tip" role="tooltip">Scratchpad</span></button>
          <button class="notebtn nb-todo" type="button" data-which="todo" aria-label="Open to-do list"><span class="pl-emo">&#128204;</span><span class="pl-tip" role="tooltip">To-do list</span></button>
        </span>
        <button class="regen" id="regen" type="button"><span class="ic">&#8635;</span> Regenerate</button>
      </span>
    </div>
    <h1>__BRAND__ <span class="h-emo" aria-hidden="true">__HDR_ICON__</span></h1>
    <div class="tag-row">
      <p class="tagline" id="tagline">Scanning tickets&hellip;</p>
    </div>
    <div class="gsearch-row">
      <input id="gsearch" class="gsearch" type="search" autocomplete="off" spellcheck="false"
             placeholder="Jump to a ticket or epic &mdash; __PFX__-1234, E001, or title&hellip;"
             aria-label="Search tickets and epics" role="combobox" aria-expanded="false" aria-controls="gdrop">
      <span class="g-hint">/</span>
      <div class="gdrop" id="gdrop" hidden></div>
    </div>
    <div class="stamp-row">
      <span class="stamp"><span class="dot"></span><span id="stamp">live scan</span></span>
      <span class="head-actions">
        <a class="hbtn" href="#sec-prioritize">&darr; Prioritize</a>
        <a class="hbtn" href="#sec-backlog">&darr; Backlog</a>
        <a class="hbtn" href="#sec-epics">&darr; Epics</a>
        __DOC_SERIES__
      </span>
    </div>
    <div class="meter-block">
      <div class="meter-head">
        <span class="eyebrow" id="meterLab">by ticket count</span>
        <span class="b-toggle" id="meterMode" role="group" aria-label="Meter units">
          <button type="button" data-mode="tickets" aria-pressed="true">tickets</button>
          <button type="button" data-mode="effort" aria-pressed="false">effort</button>
        </span>
      </div>
      <div class="meter" id="meter" role="img" aria-label="ticket status meter">
        <span class="seg-done" id="m-done"></span><span class="seg-open" id="m-open"></span><span class="seg-wf" id="m-wf"></span>
      </div>
      <div class="legend">
        <span class="li"><span class="sw" style="background:var(--done)"></span><span id="lg-done-lab">Closed</span>&nbsp;<span class="n" id="lg-done">&mdash;</span></span>
        <span class="li"><span class="sw" style="background:var(--accent)"></span><span id="lg-open-lab">Open</span>&nbsp;<span class="n" id="lg-open">&mdash;</span></span>
        <span class="li" id="lg-wf-li"><span class="sw" style="background:var(--wf)"></span>Won&#39;t&#8209;fix&nbsp;<span class="n" id="lg-wf">&mdash;</span></span>
      </div>
    </div>
  </header>
  <!--NOTES_PANEL-->
  <nav class="qnav" aria-label="Jump to section">
    <a href="#sec-health"><b>01</b>Health</a>
    <a href="#sec-prioritize"><b>02</b>Prioritize</a>
    <a href="#sec-activity"><b>03</b>Activity</a>
    <a href="#sec-backlog"><b>04</b>Backlog</a>
    <a href="#sec-chains"><b>05</b>Dependencies</a>
    <a href="#sec-tracks"><b>06</b>Standing</a>
    <a href="#sec-epics"><b>07</b>Epics</a>
  </nav>
  <div class="kpis">
    <div class="kpi clickable" data-href="/filter?label=All%20tickets" title="Click for the full ticket list"><span class="k-val" id="k-total">&mdash;</span><span class="k-lab">Tickets tracked</span><span class="k-sub" id="k-total-sub">&nbsp;</span></div>
    <div class="kpi is-done clickable" data-href="/filter?status=closed&label=Closed%20and%20shipped" title="Click for all closed tickets"><span class="k-val" id="k-closed">&mdash;</span><span class="k-lab">Closed &amp; shipped</span><span class="k-sub" id="k-closed-sub">&nbsp;</span></div>
    <div class="kpi is-open clickable" data-href="/filter?status=open&label=Open%20tickets" title="Click for all open tickets"><span class="k-val" id="k-open">&mdash;</span><span class="k-lab">Open</span><span class="k-sub" id="k-open-sub">&nbsp;</span></div>
    <div class="kpi clickable" data-href="#sec-epics" title="Click to jump to the epic breakdown"><span class="k-val" id="k-complete">&mdash;</span><span class="k-lab">Epics fully closed</span><span class="k-sub" id="k-complete-sub">&nbsp;</span></div>
    <div class="kpi is-done clickable" data-href="/filter?status=closed&label=Effort%20completed%20(closed%20tickets)" title="Sum of effort estimates on closed tickets (1d = 8h). Click for the ticket list."><span class="k-val" id="k-edone">&mdash;</span><span class="k-lab">Effort completed</span><span class="k-sub" id="k-edone-sub">&nbsp;</span></div>
    <div class="kpi is-open clickable" data-href="/filter?status=open&label=Effort%20remaining%20(open%20tickets)" title="Sum of effort estimates on open tickets (1d = 8h). Click for the ticket list."><span class="k-val" id="k-eopen">&mdash;</span><span class="k-lab">Effort remaining</span><span class="k-sub" id="k-eopen-sub">&nbsp;</span></div>
    <div class="kpi clickable" data-href="/filter?status=open&stale=30&label=Stale%20open%20tickets%20(30d%2B)" title="Median age of open tickets. Click for the stale list (open longer than 30 days)."><span class="k-val" id="k-age">&mdash;</span><span class="k-lab">Median open age</span><span class="k-sub" id="k-age-sub">&nbsp;</span></div>
    <div class="kpi clickable" data-href="/filter?status=open&quick=1&label=Ready%20quick%20wins" title="Open, unblocked, and estimated at four hours or less. Click for the list."><span class="k-val" id="k-quick">&mdash;</span><span class="k-lab">Ready quick wins</span><span class="k-sub" id="k-quick-sub">&nbsp;</span></div>
  </div>
  <div class="duo topstrip" id="topstrip">
    <div class="recent" id="pinnedPanel"><div class="panel-h ph-tools">&#128278; Pinned &middot; watching<span class='list-tools' data-src='#pinnedList' data-name='pinned'><button type='button' class='lt-copy'>copy ids</button><button type='button' class='lt-csv'>CSV</button></span></div><div id="pinnedList"></div></div>
    <div class="recent" id="wipPanel"><div class="panel-h ph-tools">&#128295; Work in progress &middot; git working tree<span class='list-tools' data-src='#wipList' data-name='wip'><button type='button' class='lt-copy'>copy ids</button><button type='button' class='lt-csv'>CSV</button></span></div><div id="wipList"></div></div>
  </div>
  <section id="sec-health">
    <div class="sec-head"><h2><span class="h-num">01</span>Program health</h2>
      <div class="controls" role="group" aria-label="Time bucket for the charts" id="bucketSel">
        <button type="button" data-bucket="day" aria-pressed="false">day</button>
        <button type="button" data-bucket="week" aria-pressed="true">week</button>
        <button type="button" data-bucket="month" aria-pressed="false">month</button>
      </div>
    </div>
    <p class="sec-note">Throughput &amp; completion over time. The bucket toggle scopes both charts; comparison tiles pit the current period against the previous one.</p>
    <div class="kpis" id="healthTiles"></div>
    <div class="charts">
      <figure class="chart">
        <figcaption class="chart-cap">
          <a class="chart-title" href="/burnup" title="Open the full burn-up view">Burn-up &mdash; cumulative created vs closed</a>
          <span class="legend2"><span class="lg2"><i class="sw2 sw-created"></i>Created</span><span class="lg2"><i class="sw2 sw-closed"></i>Closed</span></span>
          <button type="button" class="lnk tv" id="tvBurnup">table</button>
        </figcaption>
        <div class="chart-plot" id="chartBurnup"></div>
        <p class="chart-note">The gap between the lines is work not yet closed (open + won&#39;t-fix).</p>
      </figure>
      <figure class="chart">
        <figcaption class="chart-cap">
          <a class="chart-title" id="tpTitle" href="/throughput" title="Open the full throughput view">Throughput &mdash; created vs closed</a>
          <span class="legend2"><span class="lg2"><i class="sw2 sw-created"></i>Created</span><span class="lg2"><i class="sw2 sw-closed"></i>Closed</span></span>
          <button type="button" class="lnk tv" id="tvThroughput">table</button>
        </figcaption>
        <div class="chart-plot" id="chartThroughput"></div>
        <p class="chart-note" id="tpNote">Bars are tickets opened and closed per bucket.</p>
      </figure>
      <figure class="chart">
        <figcaption class="chart-cap">
          <span class="chart-title">Open-ticket age</span>
          <span class="legend2"><span class="lg2">darker = older</span></span>
        </figcaption>
        <div id="chartAging"></div>
        <p class="chart-note">Click a bucket for its ticket list. The old buckets are quiet-rot candidates.</p>
      </figure>
      <figure class="chart">
        <figcaption class="chart-cap">
          <span class="chart-title">Latest activity</span>
        </figcaption>
        <div id="lastWorked"></div>
        <p class="chart-note">The most recently touched ticket file on disk (uncommitted edits count), and its epic.</p>
      </figure>
    </div>
  </section>
  <section id="sec-prioritize">
    <div class="sec-head"><h2><span class="h-num">02</span><a class="sec-link" href="/prioritize" title="Open the prioritisation matrix">What to prioritize</a></h2></div>
    <p class="sec-note wide">Open work only. Click a matrix cell to open the prioritisation matrix filtered to it; click an epic bar to open the epic page.</p>
    <div class="charts">
      <figure class="chart fill">
        <figcaption class="chart-cap">
          <a class="chart-title" href="/prioritize" title="Open the prioritisation matrix">Open tickets &mdash; priority &times; risk</a>
          <span class="legend2"><span class="lg2 mx-legend"><span class="lm">darker</span><span class="dm">brighter</span> = more tickets</span></span>
        </figcaption>
        <div id="chartMatrix"></div>
        <p class="chart-note">Count per cell, effort-days beneath. P1&thinsp;&times;&thinsp;HIGH is the fire drawer.</p>
      </figure>
      <figure class="chart fill">
        <figcaption class="chart-cap">
          <span class="chart-title">Effort remaining by epic</span>
        </figcaption>
        <div id="chartEffort"></div>
        <p class="chart-note">Sum of open-ticket estimates, in days (1d = 8h). Unestimated tickets not included.</p>
      </figure>
    </div>
    <div class="recent upnext" style="margin-top:14px">
      <div class="panel-h ph-tools">Up next &mdash; unblocked, ranked by priority &middot; unblocks &middot; effort<span class='list-tools' data-src='#upnextList' data-name='up-next'><button type='button' class='lt-copy'>copy ids</button><button type='button' class='lt-csv'>CSV</button></span></div>
      <div id="upnextList"></div>
    </div>
  </section>
  <section id="sec-activity">
    <div class="sec-head"><h2><span class="h-num">03</span>Recent activity</h2>
      <div class="controls" role="group" aria-label="How many recent tickets to show" id="recentN">
        <button type="button" data-n="3" aria-pressed="true">3</button>
        <button type="button" data-n="6" aria-pressed="false">6</button>
        <button type="button" data-n="12" aria-pressed="false">12</button>
      </div>
    </div>
    <p class="sec-note">The latest closes and the newest tickets, across every epic.</p>
    <div class="duo">
      <div class="recent"><div class="panel-h ph-tools">Recently closed<span class='list-tools' data-src='#recentList' data-name='recently-closed'><button type='button' class='lt-copy'>copy ids</button><button type='button' class='lt-csv'>CSV</button></span></div><div id="recentList"></div></div>
      <div class="recent"><div class="panel-h ph-tools">Recently created &middot; still open<span class='list-tools' data-src='#createdList' data-name='recently-created'><button type='button' class='lt-copy'>copy ids</button><button type='button' class='lt-csv'>CSV</button></span></div><div id="createdList"></div></div>
    </div>
  </section>
  <section id="sec-backlog">
    <div class="sec-head"><h2><span class="h-num">04</span>Backlog</h2>
      <div class="backlog-tools">
        <input id="tsearch" class="searchbox" type="search" placeholder="search __PFX__-#### or title" autocomplete="off" aria-label="Search tickets by number or title">
        <label class="sortlab">risk
          <select id="fRisk" class="sortsel">
            <option value="">all</option>
            <option value="HIGH">high</option>
            <option value="MEDIUM">medium</option>
            <option value="LOW">low</option>
          </select>
        </label>
        <label class="sortlab">priority
          <select id="fPrio" class="sortsel">
            <option value="">all</option>
            <option value="1">P1</option>
            <option value="2">P2</option>
            <option value="3">P3</option>
          </select>
        </label>
        <label class="sortlab">effort
          <select id="fEff" class="sortsel">
            <option value="">all</option>
            <option value="s">&le;4h</option>
            <option value="m">&gt;4h&ndash;2d</option>
            <option value="l">&gt;2d</option>
            <option value="none">unestimated</option>
          </select>
        </label>
        <label class="sortlab">blocked
          <select id="fBlk" class="sortsel">
            <option value="">all</option>
            <option value="y">blocked</option>
            <option value="n">unblocked</option>
          </select>
        </label>
        <label class="sortlab">tickets
          <select id="tSort" class="sortsel">
            <option value="date">by date</option>
            <option value="risk">by risk</option>
            <option value="priority">by priority</option>
            <option value="effort">by effort</option>
          </select>
        </label>
        <label class="sortlab">epics
          <select id="epicSort" class="sortsel">
            <option value="recentClosed" selected>recently closed</option>
            <option value="open">most open</option>
            <option value="closed">most closed</option>
            <option value="recentCreated">recently created</option>
            <option value="id">epic id</option>
          </select>
        </label>
        <label class="sortlab chk" title="Epics with no open tickets left">
          <input type="checkbox" id="showEmpty"> empty epics
        </label>
        <button type="button" class="lnk" id="expandAll">expand all</button>
        <button type="button" class="lnk" id="collapseAll">collapse all</button>
        <span class='list-tools' data-src='#backlog' data-name='backlog'><button type='button' class='lt-copy'>copy ids</button><button type='button' class='lt-csv'>CSV</button></span>
      </div>
    </div>
    <p class="sec-note">Every epic with tickets. Toggle each card between <b>open</b> and <b>closed</b>; click a ticket to open its YAML + body. Open tickets list oldest first, closed list newest first. <span style="color:var(--warn)">DECISION</span> tickets are flagged; active working-tree work is highlighted.</p>
    <div class="backlog" id="backlog"></div>
  </section>
  <section id="sec-chains">
    <div class="sec-head"><h2><span class="h-num">05</span><a class="sec-link" href="/deps" title="Open the dependency explorer">Dependency chains</a></h2></div>
    <p class="sec-note wide">Each box is an open ticket; edges flow left&rarr;right from blocker to blocked. Blue boxes are unblocked roots. Hover a box to trace what blocks it and what it unlocks, with a detail preview; click to open the ticket. For closed history and step-by-step traversal, open the <a href="/deps">dependency explorer</a>.</p>
    <div id="chainList"></div>
  </section>
  <section id="sec-tracks">
    <div class="sec-head"><h2><span class="h-num">06</span>Where the program stands</h2></div>
    <div class="tracks" id="tracks"></div>
  </section>
  <section id="sec-epics">
    <div class="sec-head"><h2><span class="h-num">07</span>Epic breakdown</h2>
      <div class="controls" id="sortCtls" role="group" aria-label="Sort epics">
        <button type="button" data-sort="open" aria-pressed="true">by remaining</button>
        <button type="button" data-sort="total" aria-pressed="false">by size</button>
        <button type="button" data-sort="progress" aria-pressed="false">by progress</button>
      </div>
    </div>
    <p class="sec-note">Each bar is one epic &mdash; <span style="color:var(--done);font-weight:600">closed</span> &middot; <span style="color:var(--accent-ink);font-weight:600">open</span> &middot; <span style="color:var(--ink-mut);font-weight:600">won&#39;t-fix</span>.</p>
    <div class="epics" id="epicList"></div>
  </section>
  <footer>
    <p><span class="mono">Live scan</span> of <span class="mono" id="foot-src">tickets/</span> frontmatter &mdash; counts every ticket file (<span class="mono">id: __PFX__-####</span>), including compound sub-tickets, so totals reflect real files on disk rather than the generated index.</p>
    <p><span class="mono" id="foot-time">&nbsp;</span></p>
    __THEME_PICKER__
  </footer>
</div></div>
<script>
(function(){
  "use strict";
  var STATE={data:null,sort:"open",backlogSort:"recentClosed",tSort:"date",
             fRisk:"",fPrio:"",fEff:"",fBlk:"",q:"",recentN:3,meterMode:"tickets",
             bucket:"week",tvBu:false,tvTp:false,showEmpty:false};
  var cardViews={};
  var cardOpen={};
  var DEPIDS={};   // ids that appear in the dependency graph, filled on load
  var RISK_ORD={HIGH:0,MEDIUM:1,LOW:2};
  var esc=function(s){var d=document.createElement("div");d.textContent=s;return d.innerHTML;};
  function pct(p,t){return t?(p/t*100):0;}
  function days(h){return Math.round(h/8*10)/10;}
  function effBucket(t){var h=t.effortH;if(h==null)return "none";if(h<=4)return "s";if(h<=16)return "m";return "l";}
  function passFilters(t){
    if(STATE.fRisk&&(t.risk||"")!==STATE.fRisk)return false;
    if(STATE.fPrio&&String(t.priority||"")!==STATE.fPrio)return false;
    if(STATE.fEff&&effBucket(t)!==STATE.fEff)return false;
    if(STATE.fBlk==="y"&&!t.blocked)return false;
    if(STATE.fBlk==="n"&&t.blocked)return false;
    return true;
  }
  function sortTickets(arr){
    var s=STATE.tSort;
    if(s==="date")return arr; // server order: open oldest->newest, closed newest->oldest
    arr=arr.slice();
    if(s==="risk")arr.sort(function(a,b){return (RISK_ORD[a.risk]!=null?RISK_ORD[a.risk]:9)-(RISK_ORD[b.risk]!=null?RISK_ORD[b.risk]:9);});
    else if(s==="priority")arr.sort(function(a,b){return (parseInt(a.priority,10)||9)-(parseInt(b.priority,10)||9);});
    else if(s==="effort")arr.sort(function(a,b){return (a.effortH==null?1e9:a.effortH)-(b.effortH==null?1e9:b.effortH);});
    return arr;
  }
  function metaBadges(t,withEpic){
    var out="";
    if(withEpic&&t.epic)out+='<span class="b-meta epic-link" role="link" tabindex="0" data-epic="'+esc(t.epic)+
      '" title="open the '+esc(t.epic)+' epic page">'+esc(t.epic)+'</span>';
    if(t.pinned)out+='<span class="b-meta pinned" role="button" tabindex="0" data-unpin="'+esc(t.id)+
      '" title="pinned — click to unpin">&#128278;</span>';
    if(t.wip)out+='<span class="b-meta wip" title="file modified in the git working tree">WIP</span>';
    if(t.risk)out+='<span class="b-meta r-'+t.risk.toLowerCase()+'" title="risk '+esc(t.risk)+'">'+esc(t.risk[0])+'</span>';
    if(t.priority)out+='<span class="b-meta" title="priority '+esc(String(t.priority))+'">P'+esc(String(t.priority))+'</span>';
    if(t.effort)out+='<span class="b-meta" title="effort estimate">'+esc(t.effort)+'</span>';
    if(t.blocked)out+='<span class="b-meta blocked" title="depends on an open ticket">blocked</span>';
    if(t.unblocks)out+='<span class="b-meta unblocks" title="open tickets this one is holding up">unblocks '+t.unblocks+'</span>';
    if(DEPIDS[t.id])out+='<span class="b-meta dep" role="link" tabindex="0" data-dep="'+esc(t.id)+
      '" title="trace this ticket\'s dependencies">&#9741;</span>';
    return out?'<span class="b-metas">'+out+'</span>':"";
  }
  function matchQ(t,q){
    var idl=t.id.toLowerCase();
    return idl.indexOf(q)!==-1
      || idl.replace("em-","").indexOf(q.replace(/^em-?/,""))!==-1
      || t.title.toLowerCase().indexOf(q)!==-1;
  }
  function epTotal(e){return e.open+e.closed+e.wf+(e.standing||0);}
  function statusOf(e){
    if(epTotal(e)===0) return {cls:"chip-notstarted",txt:"Empty"};
    if(e.open===0) return {cls:"chip-complete",txt:"Complete"};
    if(e.closed===0) return {cls:"chip-notstarted",txt:"Not started"};
    if(e.open<=2) return {cls:"chip-finish",txt:"Finishing"};
    return {cls:"chip-progress",txt:"In progress"};
  }
  // not-started epics sink to the bottom of every epic ordering — if it
  // hasn't been started, it's usually parked for a reason
  function notStarted(e){return e.open>0&&e.closed===0;}
  function nsKey(a,b){return (notStarted(a)?1:0)-(notStarted(b)?1:0);}
  function fmt1(x){return (Math.round(x*10)/10).toString();}
  function monthShort(iso){var m=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];return m[(parseInt(iso.slice(5,7),10)||1)-1];}
  function fmtPeriod(unit,iso){
    if(unit==="day")return iso.slice(8,10).replace(/^0/,"")+" "+monthShort(iso);
    if(unit==="month")return monthShort(iso)+" "+iso.slice(0,4);
    return "wk of "+iso;
  }
  function relTime(iso){
    if(!iso)return "";
    var t=new Date(iso).getTime();if(isNaN(t))return iso;
    var s=Math.max(0,(Date.now()-t)/1000);
    if(s<60)return "just now";
    if(s<3600)return Math.floor(s/60)+"m ago";
    if(s<86400)return Math.floor(s/3600)+"h ago";
    return Math.floor(s/86400)+"d ago";
  }
  function renderLastWorked(){
    var d=STATE.data,host=document.getElementById("lastWorked");
    var lw=d.lastWorked;
    if(!lw||!lw.ticket){host.innerHTML='<div class="chart-empty">No ticket files found.</div>';return;}
    var ep=null;d.epics.forEach(function(e){if(e.id===lw.epic)ep=e;});
    host.innerHTML='<div class="lw">'+
      (ep?'<a class="lw-row" href="/epic/'+ep.id+'"><span class="lw-k">epic</span>'+
        '<span class="g-emo">'+(ep.emoji||"")+'</span><span class="b-tid">__PFX__-'+ep.id+'</span>'+
        '<span class="b-ttl">'+esc(ep.title)+'</span>'+
        '<span class="b-date">'+(ep.open||0)+' open</span></a>':'')+
      '<a class="lw-row" href="/ticket/'+encodeURIComponent(lw.ticket)+'">'+
        '<span class="lw-k">ticket</span>'+
        '<span class="g-emo">__FAVICON__</span>'+
        '<span class="b-tid">'+esc(lw.ticket)+'</span>'+
        '<span class="b-ttl">'+esc(lw.title||"")+
        (lw.wip?'<span class="b-dec">WIP</span>':'')+'</span>'+
        '<span class="b-date">'+relTime(lw.when)+'</span></a>'+
      '</div>';
  }
  function statusChipHtml(st){
    var s=(st||"OPEN").toUpperCase();
    var k=s==="CLOSED"?"closed":s==="OPEN"?"open":"wf";
    var lab=s==="WONT_FIX"?"won't fix":s.toLowerCase();
    return '<span class="g-kind k-'+k+'">'+lab+'</span>';
  }
  function renderTopStrip(){
    var d=STATE.data,strip=document.getElementById("topstrip");
    var pins=d.pinned||[],wip=d.wip||[];
    function row(it,extra){
      /* doc pins link to their /doc/ page and skip the ticket-only badges */
      var href=it.doc?'/doc/'+encodeURI(it.doc):'/ticket/'+encodeURIComponent(it.id);
      return '<a href="'+href+'"'+(it.doc?'':dataAttrs(it))+'>'+
        (it.doc?'<span class="g-kind k-doc">doc</span>':statusChipHtml(it.status))+
        '<span class="b-tid">'+esc(it.id)+'</span>'+
        '<span class="b-ttl">'+esc(it.title)+'</span>'+
        '<span class="b-right">'+(it.doc?'':metaBadges(it,true))+extra+'</span></a>';
    }
    document.getElementById("pinnedList").innerHTML=pins.map(function(it){
      return row(it,'<button type="button" class="pin-x" data-id="'+esc(it.doc||it.id)+
        '" title="unpin '+esc(it.id)+'">✕ unpin</button>');
    }).join("")||'<div class="chart-empty">Nothing pinned — use the 🔖 button at the top of any ticket or doc page.</div>';
    var wPanel=document.getElementById("wipPanel");
    var shown=wip.slice(0,8),more=wip.length-shown.length;
    document.getElementById("wipList").innerHTML=shown.map(function(it){
      return row(it,'<span class="b-date">'+relTime(it.when)+'</span>');
    }).join("")+(more>0?'<a href="/filter?wip=1&label=Work%20in%20progress%20(working%20tree)">'+
      '<span class="b-ttl" style="color:var(--accent-ink)">+'+more+' more →</span></a>':'');
    wPanel.hidden=!wip.length;
    strip.classList.toggle("solo",!wip.length);
  }
  function deltaChip(cur,prev){
    var d=cur-prev;
    if(d>0)return '<span class="k-delta up" title="vs previous period">▲'+d+'</span>';
    if(d<0)return '<span class="k-delta down" title="vs previous period">▼'+(-d)+'</span>';
    return '<span class="k-delta flat" title="vs previous period">=</span>';
  }
  function xLabels(points,unit){
    var out=[],n=points.length,i;
    if(unit==="month"){for(i=0;i<n;i++)out.push({i:i,txt:monthShort(points[i].period)});return out;}
    if(unit==="day"){var st=Math.max(1,Math.ceil(n/8));
      for(i=0;i<n;i+=st)out.push({i:i,txt:fmtPeriod("day",points[i].period)});return out;}
    var lastMonth="";
    for(i=0;i<n;i++){var mo=points[i].period.slice(0,7);
      if(mo!==lastMonth){lastMonth=mo;out.push({i:i,txt:monthShort(points[i].period)});}}
    return out;
  }
  function chartTable(points,unit,cols){
    // Newest first: the scroll box shows ~8 rows, and the recent buckets are
    // the ones worth reading.
    var rows=points.slice().reverse().map(function(p){
      var tds=cols.map(function(c){
        var v=c[0]==="gap"?(p.cumCreated-p.cumClosed):
              c[0]==="effD"?days(p.effortClosedH||0)+"d":p[c[0]];
        return "<td>"+v+"</td>";}).join("");
      return "<tr><td>"+fmtPeriod(unit,p.period)+"</td>"+tds+"</tr>";
    }).join("");
    return '<div class="ctable"><table><thead><tr><th>'+unit+' &darr;</th>'+
      cols.map(function(c){return "<th>"+c[1]+"</th>";}).join("")+
      '</tr></thead><tbody>'+rows+'</tbody></table></div>';
  }
  function renderHealth(d){
    var h=d.health||{},cp=h.compare||{};
    function winHref(c,label){
      return "/filter?status=closed&closed_from="+(c.curFrom||"")+"&closed_to="+(c.curTo||"")+
        "&label="+encodeURIComponent(label);
    }
    function cmpTile(c,label){
      if(!c)return {v:"—",dl:"",l:label,s:""};
      return {v:c.closed[1],dl:deltaChip(c.closed[1],c.closed[0]),l:label,
        href:winHref(c,label),
        s:"prev "+c.closed[0]+" · created "+c.created[1]+" vs "+c.created[0]+
          " · effort "+days(c.effortH[1])+"d vs "+days(c.effortH[0])+"d"};
    }
    var tiles=[
      {v:(h.completionRate!=null?h.completionRate.toFixed(1)+"%":"—"),dl:"",l:"Completion rate",
       s:"closed + won't-fix of "+d.totals.total,
       href:"/filter?status=closed,wf&label="+encodeURIComponent("Resolved — closed + won't-fix")},
      cmpTile(cp.wtd,"Closed · wk-to-date, "+(cp.wtdDays||0)+"d vs same "+(cp.wtdDays||0)+"d last wk"),
      cmpTile(cp.r7,"Closed · rolling 7d vs prior 7d"),
      cmpTile(cp.mtd,"Closed · month-to-date, "+(cp.mtdDays||0)+"d vs same "+(cp.mtdDays||0)+"d last mo")
    ];
    document.getElementById("healthTiles").innerHTML=tiles.map(function(x){
      return '<div class="kpi'+(x.href?' clickable" data-href="'+x.href:'')+'" title="Click for the ticket list">'+
        '<span class="k-val">'+x.v+x.dl+'</span><span class="k-lab">'+x.l+
        '</span><span class="k-sub">'+x.s+'</span></div>';
    }).join("");
    renderCharts();
    drawAging();
  }
  function drawAging(){
    var host=document.getElementById("chartAging");
    var b=(STATE.data.health||{}).aging||[];
    if(!b.length){host.innerHTML='<div class="chart-empty">No open tickets.</div>';return;}
    host.innerHTML='<div class="ages">'+b.map(function(x,i){
      var href="/filter?status=open"+(x.from?"&created_from="+x.from:"")+(x.to?"&created_to="+x.to:"")+
        "&label="+encodeURIComponent("Open tickets aged "+x.label);
      return '<a class="age-cell '+(x.n?("s"+(i+1)):"zero")+'" href="'+href+
        '" title="open for '+x.label+' — click for the list">'+
        '<span class="n">'+x.n+'</span><span class="d">'+x.label+'</span></a>';
    }).join("")+'</div>';
  }
  function renderChains(){
    var d=STATE.data,host=document.getElementById("chainList");
    var edges=d.depEdges||[];
    if(!edges.length){host.innerHTML='<div class="chart-empty">No open ticket currently blocks another.</div>';return;}
    var byId={};d.epics.forEach(function(e){(e.openTickets||[]).forEach(function(t){byId[t.id]=t;});});
    var kids={},pars={},nodes={};
    edges.forEach(function(ed){
      (kids[ed[0]]=kids[ed[0]]||[]).push(ed[1]);
      (pars[ed[1]]=pars[ed[1]]||[]).push(ed[0]);
      nodes[ed[0]]=1;nodes[ed[1]]=1;
    });
    var ids=Object.keys(nodes);
    // column = longest path from a root (cycle-safe)
    var depth={},onstack={};
    function dep(id){
      if(depth[id]!=null)return depth[id];
      if(onstack[id])return 0;
      onstack[id]=1;
      var dm=0;(pars[id]||[]).forEach(function(p){dm=Math.max(dm,dep(p)+1);});
      onstack[id]=0;depth[id]=dm;return dm;
    }
    ids.forEach(dep);
    // connected components, biggest first, each in its own horizontal band
    var comp={},cn=0;
    ids.forEach(function(id){
      if(comp[id]!=null)return;
      var stack=[id];comp[id]=cn;
      while(stack.length){
        var cur=stack.pop();
        (kids[cur]||[]).concat(pars[cur]||[]).forEach(function(nb){
          if(comp[nb]==null){comp[nb]=cn;stack.push(nb);}});
      }
      cn++;
    });
    var groups=[],i;for(i=0;i<cn;i++)groups.push([]);
    ids.forEach(function(id){groups[comp[id]].push(id);});
    groups.sort(function(a,b){return b.length-a.length;});
    var NW=200,NH=36,GX=46,GY=10,PAD=12,BANDGAP=22;
    var pos={},totalH=PAD,maxDepth=0;
    groups.forEach(function(g){
      g.sort(function(a,b){return depth[a]-depth[b]||a.localeCompare(b);});
      var colN={},bandRows=1;
      g.forEach(function(id){
        var dp=depth[id];maxDepth=Math.max(maxDepth,dp);
        var r=colN[dp]||0;colN[dp]=r+1;
        pos[id]={x:PAD+dp*(NW+GX),y:totalH+r*(NH+GY)};
        bandRows=Math.max(bandRows,r+1);
      });
      totalH+=bandRows*(NH+GY)+BANDGAP;
    });
    function trunc(s,n){s=s||"";return s.length>n?s.slice(0,n-1)+"…":s;}
    var W=PAD*2+(maxDepth+1)*(NW+GX)-GX,H=totalH;
    var svg='<svg width="'+W+'" height="'+H+'" viewBox="0 0 '+W+' '+H+
      '" role="img" aria-label="Dependency graph of open tickets">';
    edges.forEach(function(ed){
      var a=pos[ed[0]],b=pos[ed[1]];if(!a||!b)return;
      var x1=a.x+NW,y1=a.y+NH/2,x2=b.x,y2=b.y+NH/2,mx=(x1+x2)/2;
      svg+='<path class="dg-edge" data-a="'+esc(ed[0])+'" data-b="'+esc(ed[1])+
        '" d="M'+x1+','+y1+' C'+mx+','+y1+' '+mx+','+y2+' '+x2+','+y2+'"/>';
    });
    ids.forEach(function(id,ix){
      var p=pos[id],t=byId[id]||{title:""};
      var isRoot=!(pars[id]&&pars[id].length);
      // Clip the title to the node box: a proportional-font title can be wider
      // than its char budget, so truncation alone still let long ones bleed.
      svg+='<a data-id="'+esc(id)+'" href="/ticket/'+encodeURIComponent(id)+'">'+
        '<clipPath id="dgc'+ix+'"><rect x="'+p.x+'" y="'+p.y+'" width="'+(NW-9)+'" height="'+NH+'"/></clipPath>'+
        '<rect class="dg-node'+(isRoot?' root':'')+'" x="'+p.x+'" y="'+p.y+
        '" width="'+NW+'" height="'+NH+'" rx="7"/>'+
        '<text class="dg-txt" x="'+(p.x+9)+'" y="'+(p.y+14)+'">'+esc(id)+'</text>'+
        '<text class="dg-ttl" clip-path="url(#dgc'+ix+')" x="'+(p.x+9)+'" y="'+(p.y+27)+'">'+
          esc(trunc(t.title,30))+'</text></a>';
    });
    svg+='</svg>';
    host.innerHTML='<div class="dg-wrap">'+svg+'<div class="ctip" id="dgTip"></div></div>';
    // hover: dim everything except the hovered ticket's upstream blockers and
    // downstream unlocks, and show a detail preview
    var wrap=host.querySelector(".dg-wrap"),svgEl=wrap.querySelector("svg"),
        tip=wrap.querySelector("#dgTip");
    function walkMap(id,map,seen){(map[id]||[]).forEach(function(n){
      if(!seen[n]){seen[n]=1;walkMap(n,map,seen);}});}
    svgEl.addEventListener("mouseover",function(ev){
      var a=ev.target.closest("a[data-id]");if(!a)return;
      var id=a.getAttribute("data-id");
      var anc={},desc={};walkMap(id,pars,anc);walkMap(id,kids,desc);
      svgEl.classList.add("dg-dim");
      svgEl.querySelectorAll("a[data-id]").forEach(function(n){
        var nid=n.getAttribute("data-id");
        n.classList.toggle("hl",!!(nid===id||anc[nid]||desc[nid]));
      });
      svgEl.querySelectorAll(".dg-edge").forEach(function(e){
        var ea=e.getAttribute("data-a"),eb=e.getAttribute("data-b");
        var up=anc[ea]&&(anc[eb]||eb===id);
        var dn=(ea===id||desc[ea])&&desc[eb];
        e.classList.toggle("hl",!!(up||dn));
      });
      var t=byId[id]||{title:""};
      var chips=[t.priority?"P"+t.priority:"",t.risk||"",t.effort||"",
                 t.epic?"__PFX__-"+t.epic:"",t.wip?"WIP":""].filter(Boolean).join(" · ");
      tip.innerHTML='<b>'+esc(id)+'</b>'+esc(t.title||"")+
        (chips?'<div class="row"><span class="k">'+esc(chips)+'</span></div>':'')+
        '<div class="row"><span class="k">blocked by</span><span>'+
          Object.keys(anc).length+'</span></div>'+
        '<div class="row"><span class="k">unlocks</span><span>'+
          Object.keys(desc).length+'</span></div>';
      var r=wrap.getBoundingClientRect();
      var x=ev.clientX-r.left+wrap.scrollLeft,y=ev.clientY-r.top;
      tip.style.left=Math.max(110,x)+"px";tip.style.top=(y-8)+"px";tip.style.opacity="1";
    });
    svgEl.addEventListener("mouseout",function(ev){
      var a=ev.target.closest("a[data-id]");if(!a)return;
      var to=ev.relatedTarget;
      if(to&&to.closest&&to.closest("a[data-id]")===a)return;
      svgEl.classList.remove("dg-dim");
      svgEl.querySelectorAll(".hl").forEach(function(n){n.classList.remove("hl");});
      tip.style.opacity="0";
    });
  }
  function renderCharts(){
    var d=STATE.data;
    var s=(d.series&&d.series[STATE.bucket])||{points:[],unit:STATE.bucket};
    drawBurnup(s.points,s.unit);
    drawThroughput(s.points,s.unit);
    document.getElementById("tpTitle").textContent="Throughput — created vs closed per "+s.unit;
    document.getElementById("tpNote").textContent="Bars are tickets opened and closed per "+s.unit+".";
  }

  function drawBurnup(points,unit){
    var host=document.getElementById("chartBurnup");
    if(!points.length){host.innerHTML='<div class="chart-empty">No dated tickets to plot.</div>';return;}
    if(STATE.tvBu){host.innerHTML=chartTable(points,unit,[["cumCreated","created Σ"],["cumClosed","closed Σ"],["gap","unresolved"]]);return;}
    var W=720,H=250,padL=48,padR=16,padT=12,padB=28,n=points.length;
    var maxY=points[n-1].cumCreated||1;
    var niceMax=Math.max(100,Math.ceil(maxY/200)*200);
    var plotW=W-padL-padR,plotH=H-padT-padB;
    function X(i){return padL+plotW*(n===1?0.5:i/(n-1));}
    function Y(v){return padT+plotH*(1-v/niceMax);}
    var svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Cumulative created versus closed over time">';
    var step=niceMax/4,k,yy,yv,i;
    for(k=0;k<=4;k++){yv=step*k;yy=Y(yv);
      svg+='<line class="grid-line" x1="'+padL+'" y1="'+yy+'" x2="'+(W-padR)+'" y2="'+yy+'" stroke-width="1"/>';
      svg+='<text class="axis-txt" x="'+(padL-6)+'" y="'+(yy+3)+'" text-anchor="end" font-size="10">'+yv+'</text>';
    }
    xLabels(points,unit).forEach(function(t){
      svg+='<text class="axis-txt" x="'+X(t.i)+'" y="'+(H-8)+'" text-anchor="middle" font-size="10">'+t.txt+'</text>';
    });
    var cA="M"+X(0)+","+Y(0),xA="M"+X(0)+","+Y(0),cL="",xL="";
    for(i=0;i<n;i++){
      cA+=" L"+X(i)+","+Y(points[i].cumCreated);
      xA+=" L"+X(i)+","+Y(points[i].cumClosed);
      cL+=(i?"L":"M")+X(i)+","+Y(points[i].cumCreated)+" ";
      xL+=(i?"L":"M")+X(i)+","+Y(points[i].cumClosed)+" ";
    }
    cA+=" L"+X(n-1)+","+Y(0)+" Z"; xA+=" L"+X(n-1)+","+Y(0)+" Z";
    svg+='<path class="ar-created" d="'+cA+'"/><path class="ar-closed" d="'+xA+'"/>';
    svg+='<path class="ln-created" d="'+cL+'"/><path class="ln-closed" d="'+xL+'"/>';
    var lc=points[n-1].cumCreated,lx=points[n-1].cumClosed;
    svg+='<circle class="dot-created" cx="'+X(n-1)+'" cy="'+Y(lc)+'" r="4"/>';
    svg+='<circle class="dot-closed" cx="'+X(n-1)+'" cy="'+Y(lx)+'" r="4"/>';
    svg+='<text x="'+(X(n-1)-7)+'" y="'+(Y(lc)-6)+'" text-anchor="end" font-size="10" style="fill:var(--c-created);font-family:var(--font-mono);font-weight:700">'+lc+'</text>';
    svg+='<text x="'+(X(n-1)-7)+'" y="'+(Y(lx)+13)+'" text-anchor="end" font-size="10" style="fill:var(--c-closed);font-family:var(--font-mono);font-weight:700">'+lx+'</text>';
    svg+='<line id="bu-cross" class="cross" x1="0" y1="'+padT+'" x2="0" y2="'+(H-padB)+'" stroke-width="1" style="opacity:0"/>';
    svg+='<circle id="bu-dc" r="4" class="dot-created" style="opacity:0"/><circle id="bu-dx" r="4" class="dot-closed" style="opacity:0"/>';
    svg+='</svg>';
    host.innerHTML=svg+'<div class="ctip" id="bu-tip"></div>';
    var svgEl=host.querySelector("svg"),tip=host.querySelector("#bu-tip"),
        cross=host.querySelector("#bu-cross"),dc=host.querySelector("#bu-dc"),dx=host.querySelector("#bu-dx");
    svgEl.addEventListener("mousemove",function(ev){
      var r=svgEl.getBoundingClientRect();
      var vx=(ev.clientX-r.left)/r.width*W;
      var idx=Math.round((vx-padL)/plotW*(n-1)); if(idx<0)idx=0; if(idx>n-1)idx=n-1;
      var p=points[idx],px=X(idx);
      cross.setAttribute("x1",px);cross.setAttribute("x2",px);cross.style.opacity=".6";
      dc.setAttribute("cx",px);dc.setAttribute("cy",Y(p.cumCreated));dc.style.opacity="1";
      dx.setAttribute("cx",px);dx.setAttribute("cy",Y(p.cumClosed));dx.style.opacity="1";
      tip.style.opacity="1";
      tip.style.left=(px/W*r.width)+"px";
      tip.style.top=(Y(Math.max(p.cumCreated,p.cumClosed))/H*r.height)+"px";
      tip.innerHTML='<b>'+fmtPeriod(unit,p.period)+'</b><div class="row"><span class="k">created</span><span>'+p.cumCreated+
        '</span></div><div class="row"><span class="k">closed</span><span>'+p.cumClosed+
        '</span></div><div class="row"><span class="k">unresolved</span><span>'+(p.cumCreated-p.cumClosed)+'</span></div>';
    });
    svgEl.addEventListener("mouseleave",function(){tip.style.opacity="0";cross.style.opacity="0";dc.style.opacity="0";dx.style.opacity="0";});
  }

  function barPath(x,y,w,h,r){
    if(h<=0)return "";
    r=Math.min(r,w/2,h); // 4px rounded data-end, square at the baseline
    return "M"+x+","+(y+h)+" L"+x+","+(y+r)+" Q"+x+","+y+" "+(x+r)+","+y+
      " L"+(x+w-r)+","+y+" Q"+(x+w)+","+y+" "+(x+w)+","+(y+r)+" L"+(x+w)+","+(y+h)+" Z";
  }
  function drawThroughput(points,unit){
    var host=document.getElementById("chartThroughput");
    if(!points.length){host.innerHTML='<div class="chart-empty">No dated tickets to plot.</div>';return;}
    if(STATE.tvTp){host.innerHTML=chartTable(points,unit,[["created","created"],["closed","closed"],["effD","effort closed"]]);return;}
    var W=720,H=250,padL=40,padR=14,padT=12,padB=28,n=points.length,i;
    var maxY=1; for(i=0;i<n;i++){maxY=Math.max(maxY,points[i].created,points[i].closed);}
    var niceMax=Math.max(5,Math.ceil(maxY/10)*10);
    var plotW=W-padL-padR,plotH=H-padT-padB,groupW=plotW/n,gap=2;
    var barW=Math.min(24,Math.max(2,(groupW-gap*3)/2)); // ≤24px marks, air in the slot
    var off=(groupW-(barW*2+gap))/2;
    function Y(v){return padT+plotH*(1-v/niceMax);}
    var svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Tickets created and closed per '+unit+'">';
    var step=niceMax/4,k,yy,yv;
    for(k=0;k<=4;k++){yv=Math.round(step*k);yy=Y(yv);
      svg+='<line class="grid-line" x1="'+padL+'" y1="'+yy+'" x2="'+(W-padR)+'" y2="'+yy+'" stroke-width="1"/>';
      svg+='<text class="axis-txt" x="'+(padL-6)+'" y="'+(yy+3)+'" text-anchor="end" font-size="10">'+yv+'</text>';
    }
    for(i=0;i<n;i++){var gx=padL+groupW*i+off,p=points[i];
      svg+='<path class="bar-created" d="'+barPath(gx,Y(p.created),barW,plotH*(p.created/niceMax),4)+'"/>';
      svg+='<path class="bar-closed" d="'+barPath(gx+barW+gap,Y(p.closed),barW,plotH*(p.closed/niceMax),4)+'"/>';
    }
    xLabels(points,unit).forEach(function(t){
      svg+='<text class="axis-txt" x="'+(padL+groupW*t.i+groupW/2)+'" y="'+(H-8)+'" text-anchor="middle" font-size="10">'+t.txt+'</text>';
    });
    svg+='</svg>';
    host.innerHTML=svg+'<div class="ctip" id="tp-tip"></div>';
    var svgEl=host.querySelector("svg"),tip=host.querySelector("#tp-tip");
    svgEl.addEventListener("mousemove",function(ev){
      var r=svgEl.getBoundingClientRect();
      var vx=(ev.clientX-r.left)/r.width*W;
      var idx=Math.floor((vx-padL)/groupW); if(idx<0)idx=0; if(idx>n-1)idx=n-1;
      var p=points[idx],cx=padL+groupW*idx+groupW/2;
      tip.style.opacity="1"; tip.style.left=(cx/W*r.width)+"px";
      tip.style.top=(Y(Math.max(p.created,p.closed))/H*r.height)+"px";
      tip.innerHTML='<b>'+fmtPeriod(unit,p.period)+'</b><div class="row"><span class="k">created</span><span>'+p.created+
        '</span></div><div class="row"><span class="k">closed</span><span>'+p.closed+
        '</span></div><div class="row"><span class="k">effort</span><span>'+days(p.effortClosedH||0)+'d</span></div>';
    });
    svgEl.addEventListener("mouseleave",function(){tip.style.opacity="0";});
  }

  function renderMeter(){
    var d=STATE.data,t=d.totals,h=d.health||{};
    var effort=STATE.meterMode==="effort";
    var meter=document.getElementById("meter");
    if(effort){
      var doneD=days(h.effortDoneH||0),openD=days(h.effortOpenH||0),tot=(doneD+openD)||1;
      document.getElementById("m-done").style.width=(doneD/tot*100)+"%";
      document.getElementById("m-open").style.width=(openD/tot*100)+"%";
      document.getElementById("m-wf").style.width="0%";
      document.getElementById("lg-done-lab").textContent="Done";
      document.getElementById("lg-open-lab").textContent="Remaining";
      document.getElementById("lg-done").textContent=doneD.toLocaleString()+"d";
      document.getElementById("lg-open").textContent=openD.toLocaleString()+"d";
      document.getElementById("lg-wf-li").style.display="none";
      document.getElementById("meterLab").textContent="by effort estimate (1d = 8h)";
      meter.setAttribute("aria-label","effort meter: "+doneD+" days done, "+openD+" days remaining");
    }else{
      document.getElementById("m-done").style.width=pct(t.closed,t.total)+"%";
      document.getElementById("m-open").style.width=pct(t.open,t.total)+"%";
      document.getElementById("m-wf").style.width=pct(t.wf,t.total)+"%";
      document.getElementById("lg-done-lab").textContent="Closed";
      document.getElementById("lg-open-lab").textContent="Open";
      document.getElementById("lg-done").textContent=t.closed;
      document.getElementById("lg-open").textContent=t.open;
      document.getElementById("lg-wf").textContent=t.wf;
      document.getElementById("lg-wf-li").style.display="";
      document.getElementById("meterLab").textContent="by ticket count";
      meter.setAttribute("aria-label","ticket status meter");
    }
    document.querySelectorAll("#meterMode button").forEach(function(b){
      b.setAttribute("aria-pressed",String(b.getAttribute("data-mode")===STATE.meterMode));
    });
  }
  function renderHeader(d){
    var t=d.totals, resolved=t.total?((t.closed+t.wf)/t.total*100):0;
    document.getElementById("tagline").innerHTML=
      '__TAGLINE__ <b>'+t.total.toLocaleString()+' tickets</b> across '+d.epics.length+
      ' epics. <b>'+resolved.toFixed(1)+'%</b> resolved; <b>'+t.open+'</b> still open.';
    document.getElementById("stamp").textContent="live scan · "+d.generated;
    document.getElementById("foot-src").textContent=d.source+"/";
    document.getElementById("foot-time").textContent="Scanned "+d.generated;
    var g=d.git,gb=document.getElementById("ghBtn");
    if(g&&g.url){
      gb.hidden=false;gb.href=g.url;
      gb.title=(g.subject?"last commit: "+g.subject:"open the repository")+
        (g.when?" ("+relTime(g.when)+")":"");
      var bits=[];
      if(g.branch)bits.push(g.branch);
      if(g.ahead)bits.push("↑"+g.ahead+" ahead");
      if(g.behind)bits.push("↓"+g.behind+" behind");
      if(g.ahead===0&&g.behind===0)bits.push("synced");
      if(g.dirty)bits.push(g.dirty+" uncommitted");
      document.getElementById("ghSub").textContent=bits.join(" · ")||"open repo";
    }else gb.hidden=true;
    renderMeter();
    var h=d.health||{};
    var doneD=days(h.effortDoneH||0),openD=days(h.effortOpenH||0);
    document.getElementById("k-edone").textContent=doneD.toLocaleString()+"d";
    document.getElementById("k-edone-sub").textContent=days(h.effortLast4H||0).toLocaleString()+"d in last 4 wks";
    document.getElementById("k-eopen").textContent=openD.toLocaleString()+"d";
    var pace=(h.effortLast4H||0)/4; // hours per week, recent pace
    var eta=pace>0?Math.ceil((h.effortOpenH||0)/pace):null;
    document.getElementById("k-eopen-sub").textContent=
      (eta!=null?"~"+eta+" wks at recent pace":"no recent closes")+
      ((h.unestimatedOpen||0)?" · "+h.unestimatedOpen+" unestimated":"");
    document.getElementById("k-age").textContent=(h.openAgeMedianDays!=null?fmt1(h.openAgeMedianDays)+"d":"—");
    document.getElementById("k-age-sub").textContent=(h.staleOpen||0)+" stale (>30d)";
    document.getElementById("k-quick").textContent=(h.quickWins!=null?h.quickWins:"—");
    document.getElementById("k-quick-sub").textContent=(h.blockedOpen||0)+" blocked · "+(h.p1Open||0)+" P1 open";
    document.getElementById("k-total").textContent=t.total.toLocaleString();
    document.getElementById("k-total-sub").textContent=d.epics.length+" epics";
    document.getElementById("k-closed").textContent=t.closed;
    document.getElementById("k-closed-sub").textContent="+"+t.wf+" won't-fix";
    document.getElementById("k-open").textContent=t.open;
    var openEpics=d.epics.filter(function(e){return e.open>0;}).length;
    document.getElementById("k-open-sub").textContent="across "+openEpics+" epics"+
      ((h.wipOpen||0)?" · "+h.wipOpen+" WIP":"");
    var complete=d.epics.filter(function(e){return e.open===0&&epTotal(e)>0;}).length;
    var finishing=d.epics.filter(function(e){return e.open===1;}).length;
    document.getElementById("k-complete").textContent=complete;
    document.getElementById("k-complete-sub").textContent=finishing+" more at 1 open";
    /* the created/closed pocket — counted on the server (so it agrees with the
       view it links to), rendered by the shared module wherever it now lives */
    if(window.ifcPocket)window.ifcPocket(d.pocket);
  }
  function renderTracks(d){
    var complete=d.epics.filter(function(e){return e.open===0&&epTotal(e)>0;});
    var finishing=d.epics.filter(function(e){return e.open===1;});
    var notStarted=d.epics.filter(function(e){return e.open>0&&e.closed===0;});
    var nsOpen=notStarted.reduce(function(a,e){return a+e.open;},0);
    var host=document.getElementById("tracks");
    host.innerHTML=
      trackCard("var(--done)",complete.length,"Epics fully closed — no open tickets remain",complete)+
      trackCard("var(--accent-ink)",finishing.length,"Finishing — a single open ticket left",finishing)+
      trackCard("var(--ink-mut)",notStarted.length,"Not yet started — 0 closed, "+nsOpen+" open",notStarted);
  }
  function trackCard(color,val,lab,eps){
    var links=eps.map(function(e){
      return '<a href="/epic/'+e.id+'" title="open the '+e.id+' epic page">'+
        (e.emoji?e.emoji+"&#8202;":"")+e.id+'</a>';}).join(" · ");
    return '<div class="track"><div class="t-val" style="color:'+color+'">'+val+'</div>'+
      '<div class="t-lab">'+lab+'</div><div class="t-list">'+(links||"—")+'</div></div>';
  }
  function renderEpics(){
    var d=STATE.data, list=d.epics.slice(), s=STATE.sort;
    list.sort(function(a,b){
      var ns=nsKey(a,b); if(ns) return ns;
      var ta=a.open+a.closed+a.wf, tb=b.open+b.closed+b.wf;
      if(s==="open") return b.open-a.open||tb-ta;
      if(s==="total") return tb-ta;
      if(s==="progress") return pct(b.closed+b.wf,tb)-pct(a.closed+a.wf,ta)||tb-ta;
      return 0;
    });
    var host=document.getElementById("epicList");
    host.innerHTML='<div class="etiles">'+list.map(function(e){
      var t=e.open+e.closed+e.wf, st=statusOf(e);
      return '<a class="etile" href="/epic/'+e.id+'" title="'+esc(e.title)+' — '+
          e.closed+' closed · '+e.open+' open · '+e.wf+' won\'t-fix">'+
        '<span class="et-emo">'+(e.emoji||"")+'</span>'+
        '<span class="et-id">__PFX__-'+e.id+'</span>'+
        '<span class="et-name">'+esc(e.title)+'</span>'+
        '<span class="ebar" role="img" aria-label="'+e.closed+' closed, '+e.open+' open, '+e.wf+' won\'t-fix">'+
          '<span class="seg-done" style="width:'+pct(e.closed,t)+'%"></span>'+
          '<span class="seg-open" style="width:'+pct(e.open,t)+'%"></span>'+
          '<span class="seg-wf" style="width:'+pct(e.wf,t)+'%"></span></span>'+
        '<span class="et-sub">'+(t===0?'no tickets yet':
            ((e.open?('<b>'+e.open+'</b> open'):'complete')+' · '+t+' total'))+'</span>'+
        '<span class="e-chip '+st.cls+'">'+st.txt+'</span></a>';
    }).join("")+'</div>';
  }
  /* data-* the shared copy-ids / CSV toolbar reads off each ticket link */
  function dataAttrs(it){
    return ' data-slug="'+esc(it.slug||it.id)+'" data-title="'+esc(it.title||"")+'"'+
      ' data-status="'+esc(it.status||"")+'" data-epic="'+esc(it.epic||"")+'"'+
      ' data-priority="'+esc(it.priority||"")+'" data-risk="'+esc(it.risk||"")+'"'+
      ' data-effort="'+esc(it.effort||"")+'"';
  }
  function ticketRow(it,view,withEpic){
    var isDec=/DECISION|Decision:|CONCEPT/.test(it.title);
    var name=esc(it.title.replace(/^(DECISION\s*[—-]\s*|Decision:\s*|CONCEPT:\s*)/,""));
    var date=view==="closed"?(it.closed||""):(it.created||"");
    var chip=isDec?'<span class="b-dec">decision</span>':
      view==="wf"?'<span class="b-dec">won\'t-fix</span>':
      view==="standing"?'<span class="b-dec">standing</span>':'';
    return '<li><a class="'+(view==="closed"?"closed":"")+'" href="/ticket/'+encodeURIComponent(it.id)+
      '"'+dataAttrs(it)+'><span class="b-tid">'+esc(it.id)+'</span>'+
      '<span class="b-ttl">'+name+chip+'</span>'+
      '<span class="b-right">'+metaBadges(it,withEpic)+
      (date?'<span class="b-date">'+date+'</span>':'')+'</span></a></li>';
  }
  function recentRow(it,datefield,tidColor,extra){
    return '<a href="/ticket/'+encodeURIComponent(it.id)+'"'+dataAttrs(it)+'>'+
      '<span class="b-tid"'+(tidColor?' style="color:'+tidColor+'"':'')+'>'+esc(it.id)+'</span>'+
      '<span class="b-ttl">'+esc(it.title)+'</span>'+
      '<span class="b-right">'+metaBadges(it,true)+extra+
      '<span class="b-date">'+(it[datefield]||"")+'</span></span></a>';
  }
  function renderRecent(){
    var d=STATE.data;
    var cl=(d.recentClosed||[]).slice(0,STATE.recentN);
    var cr=(d.recentCreated||[]).slice(0,STATE.recentN);
    document.getElementById("recentList").innerHTML=
      cl.map(function(it){return recentRow(it,"closed","","");}).join("")||
      '<div class="chart-empty">No closed tickets yet.</div>';
    document.getElementById("createdList").innerHTML=
      cr.map(function(it){
        return recentRow(it,"created","var(--accent-ink)","");
      }).join("")||'<div class="chart-empty">No open tickets.</div>';
  }
  function drawMatrix(){
    var d=STATE.data,host=document.getElementById("chartMatrix");
    var risks=["HIGH","MEDIUM","LOW"],cells={},maxC=0,prios={};
    d.epics.forEach(function(e){(e.openTickets||[]).forEach(function(t){
      if(!t.risk||!t.priority)return;
      prios[t.priority]=1;
      var c=cells[t.priority+"|"+t.risk]||(cells[t.priority+"|"+t.risk]={n:0,h:0});
      c.n++;c.h+=(t.effortH||0);
      if(c.n>maxC)maxC=c.n;
    });});
    // Rows come from the data: hardcoding P1-P3 silently dropped P4 tickets.
    var rows=Object.keys(prios).sort(function(a,b){return (+a)-(+b);});
    if(!rows.length){
      host.innerHTML='<div class="chart-empty">No open ticket carries both a priority and a risk.</div>';
      return;
    }
    var out='<div class="matrix" style="grid-template-rows:auto repeat('+rows.length+',1fr)">'+
      '<span class="mx-lab"></span>'+
      risks.map(function(r){return '<span class="mx-lab">'+r.toLowerCase()+'</span>';}).join("");
    rows.forEach(function(p){
      out+='<span class="mx-lab row">P'+p+'</span>';
      risks.forEach(function(r){
        var c=cells[p+"|"+r]||{n:0,h:0};
        if(!c.n){out+='<button type="button" class="mx-cell empty" tabindex="-1"><span class="n">·</span></button>';return;}
        var step=Math.max(1,Math.ceil(c.n/maxC*4));
        out+='<button type="button" class="mx-cell s'+step+'" data-p="'+p+'" data-r="'+r+'"'+
          ' title="P'+p+' × '+r+' — '+c.n+' open, '+days(c.h)+'d. Click to open the prioritisation matrix.">'+
          '<span class="n">'+c.n+'</span><span class="d">'+days(c.h)+'d</span></button>';
      });
    });
    host.innerHTML=out+'</div>';
  }
  function drawEffortBars(){
    var d=STATE.data,host=document.getElementById("chartEffort");
    var rows=d.epics.filter(function(e){return e.effortOpenH>0;})
      .map(function(e){return {id:e.id,title:e.title,emo:e.emoji,dd:days(e.effortOpenH),ns:notStarted(e)};})
      .sort(function(a,b){return (a.ns?1:0)-(b.ns?1:0)||b.dd-a.dd;});
    if(!rows.length){host.innerHTML='<div class="chart-empty">No estimated open work.</div>';return;}
    var top=rows.slice(0,9),rest=rows.slice(9);
    if(rest.length)top.push({id:"",title:"Other · "+rest.length+" epics",
      dd:Math.round(rest.reduce(function(a,r){return a+r.dd;},0)*10)/10});
    var max=top[0].dd||1;
    host.innerHTML='<div class="ebars">'+top.map(function(r){
      var inner='<span class="ebr-name">'+(r.emo?r.emo+" ":"")+(r.id?'<b>'+r.id+'</b> ':'')+esc(r.title)+'</span>'+
        '<span class="ebr-track"><span class="ebr-bar" style="width:'+Math.max(1.5,r.dd/max*76)+'%"></span>'+
        '<span class="ebr-val">'+r.dd.toLocaleString()+'d</span></span>';
      return r.id?'<a class="ebar-row" href="/epic/'+r.id+'" title="open the '+r.id+' epic page">'+inner+'</a>'
                 :'<div class="ebar-row">'+inner+'</div>';
    }).join("")+'</div>';
  }
  function renderUpNext(){
    var d=STATE.data,host=document.getElementById("upnextList");
    var cand=[];
    d.epics.forEach(function(e){(e.openTickets||[]).forEach(function(t){if(!t.blocked)cand.push(t);});});
    cand.sort(function(a,b){
      return (parseInt(a.priority,10)||9)-(parseInt(b.priority,10)||9)
        || (b.unblocks||0)-(a.unblocks||0)
        || (RISK_ORD[a.risk]!=null?RISK_ORD[a.risk]:9)-(RISK_ORD[b.risk]!=null?RISK_ORD[b.risk]:9)
        || (a.effortH==null?1e9:a.effortH)-(b.effortH==null?1e9:b.effortH);
    });
    host.innerHTML=cand.slice(0,6).map(function(it){
      return recentRow(it,"created","var(--accent-ink)","");
    }).join("")||'<div class="chart-empty">Nothing open and unblocked.</div>';
  }
  function renderBacklog(){
    var d=STATE.data,s=STATE.backlogSort;
    var host=document.getElementById("backlog");host.innerHTML="";
    var q=STATE.q.trim().toLowerCase();
    if(q){ // flat search results across all epics and both statuses
      var res=[];
      d.epics.forEach(function(e){
        (e.openTickets||[]).forEach(function(t){if(matchQ(t,q))res.push({t:t,view:"open"});});
        (e.closedTickets||[]).forEach(function(t){if(matchQ(t,q))res.push({t:t,view:"closed"});});
        (e.wfTickets||[]).forEach(function(t){if(matchQ(t,q))res.push({t:t,view:"wf"});});
        (e.standingTickets||[]).forEach(function(t){if(matchQ(t,q))res.push({t:t,view:"standing"});});
      });
      var lis=res.map(function(r){return ticketRow(r.t,r.view,true);}).join("")||
        '<li><a style="cursor:default"><span class="b-ttl" style="color:var(--ink-mut)">no tickets match</span></a></li>';
      var det=document.createElement("details");
      det.className="bcard hl span-all";det.open=true;
      det.innerHTML='<summary><span class="b-caret">▶</span>'+
        '<span class="b-head-main"><span class="b-head-id">SEARCH</span>'+
        '<span class="b-head-name">'+res.length+' match'+(res.length===1?'':'es')+
        ' for “'+esc(STATE.q.trim())+'”</span></span></summary>'+
        '<ul class="b-list">'+lis+'</ul>';
      host.appendChild(det);
      return;
    }
    var filtering=!!(STATE.fRisk||STATE.fPrio||STATE.fEff||STATE.fBlk);
    var eps=d.epics.filter(function(e){return (e.open+e.closed)>0;}).slice();
    if(!STATE.showEmpty)eps=eps.filter(function(e){return e.open>0;});
    eps.sort(function(a,b){
      var ns=nsKey(a,b); if(ns) return ns;
      if(s==="open") return b.open-a.open || b.closed-a.closed;
      if(s==="closed") return b.closed-a.closed || b.open-a.open;
      if(s==="recentClosed") return (b.lastClosed||"").localeCompare(a.lastClosed||"");
      if(s==="recentCreated") return (b.lastCreated||"").localeCompare(a.lastCreated||"");
      if(s==="id") return a.id.localeCompare(b.id);
      return 0;
    });
    eps.forEach(function(e){
      var view=cardViews[e.id]||(e.open>0?"open":"closed");
      var openF=(e.openTickets||[]).filter(passFilters);
      var closedF=(e.closedTickets||[]).filter(passFilters);
      if(filtering&&!openF.length&&!closedF.length)return; // nothing matches in this epic
      var items=sortTickets(view==="open"?openF:closedF);
      var lis=items.map(function(it){return ticketRow(it,view,false);}).join("");
      if(!lis) lis='<li><a style="cursor:default"><span class="b-ttl" style="color:var(--ink-mut)">none in this view</span></a></li>';
      var det=document.createElement("details");
      det.className="bcard";
      det.open=(e.id in cardOpen)?cardOpen[e.id]:(filtering||items.length<=25);
      det.addEventListener("toggle",function(){cardOpen[e.id]=det.open;});
      det.innerHTML=
        '<summary><span class="b-caret">▶</span>'+
        '<span class="b-emo">'+(e.emoji||"")+'</span>'+
        '<span class="b-head-main"><a class="b-head-id" href="/epic/'+e.id+'" title="open the epic page">__PFX__-'+e.id+' ↗</a>'+
        '<span class="b-head-name">'+esc(e.title)+'</span></span>'+
        '<span class="b-toggle" data-epic="'+e.id+'">'+
          '<button type="button" data-view="open" aria-pressed="'+(view==="open")+'">open '+(filtering?openF.length+"/"+e.open:e.open)+'</button>'+
          '<button type="button" data-view="closed" aria-pressed="'+(view==="closed")+'">closed '+(filtering?closedF.length+"/"+e.closed:e.closed)+'</button>'+
        '</span></summary><ul class="b-list">'+lis+'</ul>';
      host.appendChild(det);
    });
    if(!host.children.length)
      host.innerHTML='<div class="chart-empty" style="grid-column:1/-1">No tickets match the current filters.</div>';
  }
  function renderAll(){renderHeader(STATE.data);renderHealth(STATE.data);renderTracks(STATE.data);
    renderEpics();drawMatrix();drawEffortBars();renderUpNext();renderChains();renderRecent();
    renderBacklog();renderLastWorked();renderTopStrip();}
  // ---- deep-linkable state: filters/search/sort live in the URL hash ----
  var HASH_DEFAULTS={q:"",risk:"",prio:"",eff:"",blk:"",tsort:"date",esort:"recentClosed",
    sort:"open",bucket:"week",n:"3",meter:"tickets",empty:"0"};
  function syncHash(){
    var m={q:STATE.q.trim(),risk:STATE.fRisk,prio:STATE.fPrio,eff:STATE.fEff,blk:STATE.fBlk,
      tsort:STATE.tSort,esort:STATE.backlogSort,sort:STATE.sort,bucket:STATE.bucket,
      n:String(STATE.recentN),meter:STATE.meterMode,empty:STATE.showEmpty?"1":"0"};
    var parts=[];
    Object.keys(m).forEach(function(k){
      if(m[k]!==HASH_DEFAULTS[k])parts.push(k+"="+encodeURIComponent(m[k]));});
    var h=parts.length?"#"+parts.join("&"):"";
    if(h!==location.hash)history.replaceState(null,"",h||location.pathname);
  }
  function hashToState(){
    var h=location.hash.replace(/^#/,"");if(!h||h.indexOf("=")===-1)return;
    var m={};
    h.split("&").forEach(function(kv){var i=kv.indexOf("=");if(i<0)return;
      m[kv.slice(0,i)]=decodeURIComponent(kv.slice(i+1));});
    if(m.q!=null)STATE.q=m.q;
    if(m.risk!=null)STATE.fRisk=m.risk;
    if(m.prio!=null)STATE.fPrio=m.prio;
    if(m.eff!=null)STATE.fEff=m.eff;
    if(m.blk!=null)STATE.fBlk=m.blk;
    if(m.tsort!=null)STATE.tSort=m.tsort;
    if(m.esort!=null)STATE.backlogSort=m.esort;
    if(m.sort!=null)STATE.sort=m.sort;
    if(m.bucket!=null)STATE.bucket=m.bucket;
    if(m.n!=null)STATE.recentN=parseInt(m.n,10)||3;
    if(m.meter!=null)STATE.meterMode=m.meter;
    if(m.empty!=null)STATE.showEmpty=m.empty==="1";
  }
  function syncControls(){
    document.getElementById("tsearch").value=STATE.q;
    document.getElementById("fRisk").value=STATE.fRisk;
    document.getElementById("fPrio").value=STATE.fPrio;
    document.getElementById("fEff").value=STATE.fEff;
    document.getElementById("fBlk").value=STATE.fBlk;
    document.getElementById("tSort").value=STATE.tSort;
    document.getElementById("epicSort").value=STATE.backlogSort;
    document.getElementById("showEmpty").checked=STATE.showEmpty;
    document.querySelectorAll("#sortCtls button").forEach(function(b){
      b.setAttribute("aria-pressed",String(b.getAttribute("data-sort")===STATE.sort));});
    document.querySelectorAll("#bucketSel button").forEach(function(b){
      b.setAttribute("aria-pressed",String(b.getAttribute("data-bucket")===STATE.bucket));});
    document.querySelectorAll("#recentN button").forEach(function(b){
      b.setAttribute("aria-pressed",String(b.getAttribute("data-n")===String(STATE.recentN)));});
  }
  function load(){
    var btn=document.getElementById("regen");btn.disabled=true;btn.classList.add("spin");
    fetch("/api/data",{cache:"no-store"}).then(function(r){return r.json();}).then(function(d){
      STATE.data=d;
      DEPIDS=(d.depGraph&&d.depGraph.nodes)||{};
      renderAll();
    }).catch(function(err){
      document.getElementById("tagline").textContent="Scan failed: "+err;
    }).finally(function(){btn.disabled=false;btn.classList.remove("spin");});
  }
  document.querySelectorAll("#sortCtls button").forEach(function(b){
    b.addEventListener("click",function(){
      document.querySelectorAll("#sortCtls button").forEach(function(x){x.setAttribute("aria-pressed","false");});
      b.setAttribute("aria-pressed","true");STATE.sort=b.getAttribute("data-sort");renderEpics();syncHash();
    });
  });
  document.querySelectorAll("#bucketSel button").forEach(function(b){
    b.addEventListener("click",function(){
      document.querySelectorAll("#bucketSel button").forEach(function(x){x.setAttribute("aria-pressed","false");});
      b.setAttribute("aria-pressed","true");STATE.bucket=b.getAttribute("data-bucket");renderCharts();syncHash();
    });
  });
  document.getElementById("tvBurnup").addEventListener("click",function(){
    STATE.tvBu=!STATE.tvBu;this.textContent=STATE.tvBu?"chart":"table";renderCharts();});
  document.getElementById("tvThroughput").addEventListener("click",function(){
    STATE.tvTp=!STATE.tvTp;this.textContent=STATE.tvTp?"chart":"table";renderCharts();});
  // Epic chips sit inside ticket links; intercept and route to the epic page.
  document.addEventListener("click",function(ev){
    var ch=ev.target.closest(".b-meta[data-epic]");if(!ch)return;
    ev.preventDefault();ev.stopPropagation();
    location.href="/epic/"+ch.getAttribute("data-epic");
  },true);
  // Dependency chips do the same, routing to the explorer focused on that ticket.
  document.addEventListener("click",function(ev){
    var ch=ev.target.closest(".b-meta[data-dep]");if(!ch)return;
    ev.preventDefault();ev.stopPropagation();
    location.href="/deps?id="+encodeURIComponent(ch.getAttribute("data-dep"));
  },true);
  document.addEventListener("keydown",function(ev){
    if(ev.key!=="Enter")return;
    var ch=ev.target&&ev.target.closest?ev.target.closest(".b-meta[data-dep]"):null;
    if(!ch)return;ev.preventDefault();
    location.href="/deps?id="+encodeURIComponent(ch.getAttribute("data-dep"));
  });
  // Unpin controls (panel buttons + pinned chips) sit inside ticket links too.
  document.addEventListener("click",function(ev){
    var b=ev.target.closest(".pin-x[data-id],.b-meta.pinned[data-unpin]");if(!b)return;
    ev.preventDefault();ev.stopPropagation();
    if(b.disabled)return;
    b.disabled=true;
    fetch("/api/pin",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({id:b.getAttribute("data-id")||b.getAttribute("data-unpin"),
        pinned:false})})
      .then(function(){load();});
  },true);
  document.addEventListener("keydown",function(ev){
    if(ev.key!=="Enter")return;
    var ch=ev.target.closest?ev.target.closest(".b-meta[data-epic]"):null;if(!ch)return;
    ev.preventDefault();location.href="/epic/"+ch.getAttribute("data-epic");
  });
  document.getElementById("chartMatrix").addEventListener("click",function(ev){
    var c=ev.target.closest(".mx-cell[data-p]");if(!c)return;
    location.href="/prioritize?p="+c.getAttribute("data-p")+"&r="+c.getAttribute("data-r");
  });
  document.addEventListener("click",function(ev){
    var k=ev.target.closest(".kpi[data-href]");if(!k)return;
    var href=k.getAttribute("data-href");
    if(href.charAt(0)==="#"&&SEC_SET[href.slice(1)])SEC_SET[href.slice(1)](false,true);
    location.href=href;
  });
  document.getElementById("regen").addEventListener("click",load);
  var es=document.getElementById("epicSort");
  if(es) es.addEventListener("change",function(){STATE.backlogSort=es.value;renderBacklog();syncHash();});
  [["fRisk","fRisk"],["fPrio","fPrio"],["fEff","fEff"],["fBlk","fBlk"],["tSort","tSort"]].forEach(function(pair){
    var el=document.getElementById(pair[0]);
    el.addEventListener("change",function(){STATE[pair[1]]=el.value;renderBacklog();syncHash();});
  });
  var se=document.getElementById("showEmpty");
  se.addEventListener("change",function(){STATE.showEmpty=se.checked;renderBacklog();syncHash();});
  var sr=document.getElementById("tsearch");
  sr.addEventListener("input",function(){STATE.q=sr.value;renderBacklog();syncHash();});
  sr.addEventListener("keydown",function(e){if(e.key==="Escape"){sr.value="";STATE.q="";renderBacklog();syncHash();}});
  document.querySelectorAll("#recentN button").forEach(function(b){
    b.addEventListener("click",function(){
      document.querySelectorAll("#recentN button").forEach(function(x){x.setAttribute("aria-pressed","false");});
      b.setAttribute("aria-pressed","true");
      STATE.recentN=parseInt(b.getAttribute("data-n"),10)||3;
      renderRecent();syncHash();
    });
  });
  document.querySelectorAll("#meterMode button").forEach(function(b){
    b.addEventListener("click",function(){STATE.meterMode=b.getAttribute("data-mode");renderMeter();syncHash();});
  });
  document.getElementById("backlog").addEventListener("click",function(ev){
    var b=ev.target.closest(".b-toggle button"); if(!b) return;
    ev.preventDefault(); ev.stopPropagation();
    var epicId=b.parentElement.getAttribute("data-epic");
    cardViews[epicId]=b.getAttribute("data-view"); cardOpen[epicId]=true;
    renderBacklog();
  });
  document.getElementById("expandAll").addEventListener("click",function(){
    STATE.data.epics.forEach(function(e){cardOpen[e.id]=true;}); renderBacklog();});
  document.getElementById("collapseAll").addEventListener("click",function(){
    STATE.data.epics.forEach(function(e){cardOpen[e.id]=false;}); renderBacklog();});
  // ---- global jump-to search (tickets + epics) ----
  var gIn=document.getElementById("gsearch"),gDrop=document.getElementById("gdrop"),
      gRes=[],gAct=-1,G_MAX=14;
  function gSearch(qRaw){
    var d=STATE.data;if(!d)return [];
    var q=qRaw.trim().toLowerCase();if(!q)return [];
    var qe=q.replace(/^em-?/,"");
    var exact=[],rest=[];
    d.epics.forEach(function(e){
      var eid=e.id.toLowerCase();
      if(eid.indexOf(qe)===-1&&("em-"+eid).indexOf(q)===-1&&e.title.toLowerCase().indexOf(q)===-1)return;
      var r={kind:"epic",href:"/epic/"+e.id,id:"__PFX__-"+e.id,title:e.title,emo:e.emoji,
             sub:e.open+" open · "+e.closed+" closed"};
      (eid===qe?exact:rest).push(r);
    });
    (d.adrs||[]).forEach(function(a){
      var idl=a.num.toLowerCase();
      if(idl.indexOf(q)===-1&&a.title.toLowerCase().indexOf(q)===-1)return;
      var k=a.kind||"adr";
      var r={kind:k,cls:k==="adr"?"adr":"doc",
             href:"/doc/"+encodeURI(a.path),id:a.num,title:a.title,
             sub:a.status||""};
      (idl===q?exact:rest).push(r);
    });
    // open tickets surface first — managing open work is the priority
    [["openTickets","open"],["closedTickets","closed"],["wfTickets","wf"],
     ["standingTickets","standing"]].forEach(function(bk){
      d.epics.forEach(function(e){
        (e[bk[0]]||[]).forEach(function(t){
          if(!matchQ(t,q))return;
          var r={kind:bk[1],href:"/ticket/"+encodeURIComponent(t.id),id:t.id,
                 title:t.title,sub:(e.emoji?e.emoji+" ":"")+"__PFX__-"+e.id};
          (t.id.toLowerCase()===q||t.id.toLowerCase()==="em-"+qe?exact:rest).push(r);
        });
      });
    });
    return exact.concat(rest);
  }
  function gRender(){
    var rows=gRes.slice(0,G_MAX).map(function(r,i){
      return '<a href="'+r.href+'"'+(i===gAct?' class="active"':'')+'>'+
        '<span class="g-kind k-'+(r.cls||r.kind)+'">'+r.kind+'</span>'+
        (r.emo?'<span class="g-emo">'+r.emo+'</span>':'')+
        '<span class="g-id">'+esc(r.id)+'</span><span class="g-ttl">'+esc(r.title)+'</span>'+
        '<span class="g-sub">'+esc(r.sub||"")+'</span></a>';
    }).join("");
    if(gRes.length>G_MAX)rows+='<div class="g-empty">'+(gRes.length-G_MAX)+' more — keep typing to narrow</div>';
    gDrop.innerHTML=rows;gDrop.hidden=false;gIn.setAttribute("aria-expanded","true");
    var act=gDrop.querySelector("a.active");
    if(act&&act.scrollIntoView)act.scrollIntoView({block:"nearest"});
  }
  function gClose(){gDrop.hidden=true;gIn.setAttribute("aria-expanded","false");}
  function gUpdate(){
    gRes=gSearch(gIn.value);gAct=gRes.length?0:-1;
    if(!gIn.value.trim()){gClose();return;}
    if(!gRes.length){gDrop.innerHTML='<div class="g-empty">no ticket or epic matches</div>';
      gDrop.hidden=false;gIn.setAttribute("aria-expanded","true");return;}
    gRender();
  }
  gIn.addEventListener("input",gUpdate);
  gIn.addEventListener("focus",function(){if(gIn.value.trim())gUpdate();});
  gIn.addEventListener("keydown",function(e){
    if(e.key==="Escape"){gIn.value="";gClose();gIn.blur();return;}
    if(!gRes.length)return;
    var vis=Math.min(gRes.length,G_MAX);
    if(e.key==="ArrowDown"){e.preventDefault();gAct=(gAct+1)%vis;gRender();}
    else if(e.key==="ArrowUp"){e.preventDefault();gAct=(gAct-1+vis)%vis;gRender();}
    else if(e.key==="Enter"){e.preventDefault();
      var r=gRes[Math.max(0,gAct)];if(r)location.href=r.href;}
  });
  document.addEventListener("click",function(ev){
    if(!ev.target.closest(".gsearch-row"))gClose();
  });
  document.addEventListener("keydown",function(e){
    var el=document.activeElement,tag=el?el.tagName:"";
    var typing=/INPUT|SELECT|TEXTAREA/.test(tag);
    if((e.key==="/"&&!typing)||((e.metaKey||e.ctrlKey)&&(e.key==="k"||e.key==="K"))){
      e.preventDefault();gIn.focus();gIn.select();
    }
  });
  // ---- collapsible sections (preference remembered in this browser) ----
  var COLLAPSED={};try{COLLAPSED=JSON.parse(localStorage.getItem("cpCollapsed")||"{}");}catch(e){}
  var SEC_SET={};
  document.querySelectorAll(".wrap section[id]").forEach(function(sec){
    var h2=sec.querySelector(".sec-head h2");if(!h2)return;
    var btn=document.createElement("button");
    btn.className="sec-tgl";btn.type="button";
    btn.title="collapse or expand this section";
    h2.insertBefore(btn,h2.firstChild);
    function set(collapsed,save){
      sec.classList.toggle("collapsed",collapsed);
      btn.setAttribute("aria-expanded",String(!collapsed));
      btn.textContent=collapsed?"▸":"▾";
      if(save){COLLAPSED[sec.id]=collapsed;
        try{localStorage.setItem("cpCollapsed",JSON.stringify(COLLAPSED));}catch(e){}}
    }
    SEC_SET[sec.id]=set;
    btn.addEventListener("click",function(){set(!sec.classList.contains("collapsed"),true);});
    set(!!COLLAPSED[sec.id],false);
  });
  // jumping to a section always expands it first
  document.querySelectorAll('a[href^="#sec-"]').forEach(function(a){
    a.addEventListener("click",function(){
      var id=a.getAttribute("href").slice(1);
      if(SEC_SET[id])SEC_SET[id](false,true);
    });
  });
  hashToState();
  syncControls();
  load();
})();

/*NOTES_JS*/
</script></body></html>"""


# --------------------------------------------------------------------------- #
# Shared floating "scroll to top / bottom" control, used on every long page.
# --------------------------------------------------------------------------- #
SCROLLNAV_CSS = r"""
.scrollnav{position:fixed;right:16px;bottom:16px;z-index:50;display:flex;flex-direction:column;gap:8px;
opacity:0;transform:translateY(10px);pointer-events:none;transition:opacity .18s ease,transform .18s ease}
.scrollnav.on{opacity:1;transform:none;pointer-events:auto}
.scrollnav button{width:42px;height:42px;border-radius:50%;border:1px solid var(--line);
background:var(--surface);color:var(--ink-2);box-shadow:var(--shadow);cursor:pointer;font-size:1.05rem;
line-height:1;display:flex;align-items:center;justify-content:center;font-family:var(--font-mono)}
.scrollnav button:hover{border-color:var(--accent);color:var(--accent-ink)}
.scrollnav button[hidden]{display:none}
@media (prefers-reduced-motion:reduce){.scrollnav{transition:none}}
"""

SCROLLNAV_HTML = (
    '<div class="scrollnav" id="scrollNav" aria-hidden="true">'
    '<button type="button" id="snTop" title="Back to top" aria-label="Back to top">&#8593;</button>'
    '<button type="button" id="snBot" title="Jump to bottom" aria-label="Jump to bottom">&#8595;</button>'
    "</div>"
)

SCROLLNAV_JS = r"""<script>
(function(){
  var nav=document.getElementById("scrollNav");if(!nav)return;
  var top=document.getElementById("snTop"),bot=document.getElementById("snBot");
  var reduce=matchMedia("(prefers-reduced-motion:reduce)").matches;
  function docH(){return Math.max(document.body.scrollHeight,document.documentElement.scrollHeight);}
  function y(){return window.pageYOffset||document.documentElement.scrollTop||0;}
  function upd(){
    var cur=y(),atBottom=cur+window.innerHeight>=docH()-4;
    nav.classList.toggle("on",cur>160);
    nav.setAttribute("aria-hidden",String(cur<=160));
    top.hidden=cur<160;
    bot.hidden=atBottom;
  }
  function go(to){window.scrollTo(reduce?{top:to}:{top:to,behavior:"smooth"});}
  top.addEventListener("click",function(){go(0);});
  bot.addEventListener("click",function(){go(docH());});
  addEventListener("scroll",upd,{passive:true});
  addEventListener("resize",upd);
  upd();
})();
</script>"""

# Inject the control into the dashboard once. Adding its CSS to the dashboard
# <style> means the flow pages pick it up for free via DASHBOARD_CSS below.
DASHBOARD_HTML = DASHBOARD_HTML.replace("</style>", SCROLLNAV_CSS + "</style>", 1)
DASHBOARD_HTML = DASHBOARD_HTML.replace(
    "</script></body></html>", "</script>" + SCROLLNAV_HTML + SCROLLNAV_JS + "</body></html>", 1)


# --------------------------------------------------------------------------- #
# Flow pages (/throughput, /burnup) -- expanded views of the dashboard charts
# --------------------------------------------------------------------------- #
# They reuse the dashboard's design tokens, page chrome and chart classes by
# lifting its one <style> block, rather than keeping a second copy in sync.
DASHBOARD_CSS = re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.S).group(1)

FLOW_CSS = r"""
.tp-bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:16px}
.tp-bar .lab{font-family:var(--font-mono);font-size:.66rem;letter-spacing:.12em;
text-transform:uppercase;color:var(--ink-mut)}
.kpis.flow{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.kpi.flow .k-val{font-size:clamp(1.2rem,2.2vw,1.55rem)}
.tp-charts{display:grid;gap:16px;margin-top:16px}
/* grid/flex items default to min-width:auto, so a wide fixed-size child (the
   dependency SVG) blows the item past the page. Let overflow:auto scroll it. */
.tp-charts>figure,.pr-duo>figure{min-width:0}
.ctable.tall{max-height:min(64vh,640px)}
.ln-avg{fill:none;stroke:var(--warn);stroke-width:2;stroke-dasharray:5 3;stroke-linejoin:round}
.sw-avg{background:var(--warn)}
.bar-eff{fill:var(--warn)}.sw-eff{background:var(--warn)}
.zero-line{stroke:var(--ink-mut);opacity:.5}
.ln-proj-created{fill:none;stroke:var(--c-created);stroke-width:1.6;stroke-dasharray:4 4;opacity:.75}
.ln-proj-closed{fill:none;stroke:var(--c-closed);stroke-width:1.6;stroke-dasharray:4 4;opacity:.75}
.ln-gap{fill:none;stroke:var(--warn);stroke-width:2;stroke-linejoin:round}
.ar-gap{fill:var(--warn);opacity:.13}
.sw-gap{background:var(--warn)}
.now-line{stroke:var(--ink-mut);stroke-dasharray:2 3;opacity:.6}
.meet{fill:var(--warn)}
.proj-txt{fill:var(--warn);font-family:var(--font-mono);font-weight:700}
svg a{cursor:pointer}
.neg{color:#c23b3b}.pos{color:var(--done)}
"""

# Shared chart/series maths for both flow pages. Kept as one namespace object so
# each page's script can pull the pieces it needs off it.
FLOW_JS = r"""<script>
var FLOW=(function(){
  function days(h){return Math.round(h/8*10)/10;}
  function monthShort(iso){var m=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    return m[(parseInt(iso.slice(5,7),10)||1)-1];}
  function fmtPeriod(unit,iso){
    if(unit==="day")return iso.slice(8,10).replace(/^0/,"")+" "+monthShort(iso);
    if(unit==="month")return monthShort(iso)+" "+iso.slice(0,4);
    return "wk of "+iso;
  }
  function fmtDate(iso){return iso.slice(8,10).replace(/^0/,"")+" "+monthShort(iso)+" "+iso.slice(0,4);}
  // All period maths in UTC: local-midnight Dates shift the day in +TZ offsets.
  function toUTC(iso){var p=iso.split("-");return new Date(Date.UTC(+p[0],+p[1]-1,+p[2]));}
  function isoOf(d){return d.toISOString().slice(0,10);}
  function periodEnd(unit,iso){
    var d=toUTC(iso);
    if(unit==="day")return iso;
    if(unit==="week")d.setUTCDate(d.getUTCDate()+6);
    else{d.setUTCMonth(d.getUTCMonth()+1);d.setUTCDate(0);}
    return isoOf(d);
  }
  function addBuckets(unit,iso,k){
    var d=toUTC(iso);k=Math.round(k);
    if(unit==="day")d.setUTCDate(d.getUTCDate()+k);
    else if(unit==="week")d.setUTCDate(d.getUTCDate()+7*k);
    else d.setUTCMonth(d.getUTCMonth()+k);
    return isoOf(d);
  }
  function barPath(x,y,w,h,r){
    if(h<=0)return "";
    r=Math.min(r,w/2,h);
    return "M"+x+" "+(y+h)+"V"+(y+r)+"a"+r+" "+r+" 0 0 1 "+r+" "+(-r)+"h"+(w-2*r)+
      "a"+r+" "+r+" 0 0 1 "+r+" "+r+"V"+(y+h)+"z";
  }
  function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;");}
  function closedHref(unit,p){
    return "/filter?status=closed&closed_from="+p.period+"&closed_to="+periodEnd(unit,p.period)+
      "&label="+encodeURIComponent("Closed in "+fmtPeriod(unit,p.period));
  }
  function createdHref(unit,p){
    return "/filter?created_from="+p.period+"&created_to="+periodEnd(unit,p.period)+
      "&label="+encodeURIComponent("Created in "+fmtPeriod(unit,p.period));
  }
  function pointsInRange(data,bucket,range){
    var s=(data.series&&data.series[bucket])||{points:[],unit:bucket};
    var pts=s.points||[];
    if(range>0){
      var t=new Date();
      var cut=isoOf(new Date(Date.UTC(t.getFullYear(),t.getMonth(),t.getDate()-range)));
      pts=pts.filter(function(p){return periodEnd(s.unit,p.period)>=cut;});
    }
    return {unit:s.unit,points:pts};
  }
  function avgWindow(unit){return unit==="day"?7:unit==="week"?4:3;}
  // Trailing mean, sized to the bucket.
  function rollingAvg(pts,key,win){
    return pts.map(function(_,i){
      var a=Math.max(0,i-win+1),sum=0,k;
      for(k=a;k<=i;k++)sum+=pts[k][key];
      return sum/(i-a+1);
    });
  }
  // Least-squares fit of pts[key] over the last `win` buckets, x = bucket index.
  function fit(pts,key,win){
    var n=pts.length,from=Math.max(0,n-win),m=n-from,sx=0,sy=0,sxx=0,sxy=0,i,y;
    for(i=from;i<n;i++){y=pts[i][key];sx+=i;sy+=y;sxx+=i*i;sxy+=i*y;}
    var den=m*sxx-sx*sx;
    if(m<2||!den)return {a:(n?pts[n-1][key]:0),b:0,from:from};
    var b=(m*sxy-sx*sy)/den;
    return {a:(sy-b*sx)/m,b:b,from:from};
  }
  function xLabels(points,unit,maxTicks){
    var out=[],n=points.length,i;
    if(unit==="month"){for(i=0;i<n;i++)out.push({i:i,txt:monthShort(points[i].period)});return out;}
    var st=Math.max(1,Math.ceil(n/(maxTicks||12)));
    for(i=0;i<n;i+=st)out.push({i:i,txt:fmtPeriod(unit,points[i].period)});
    return out;
  }
  function tip(host,id){
    var t=document.createElement("div");t.className="ctip";t.id=id;host.appendChild(t);return t;
  }
  function tiles(hostId,list,tagline){
    document.getElementById(hostId).innerHTML=list.map(function(t){
      return '<div class="kpi flow '+(t.cls||"")+(t.href?" clickable":"")+'"'+
        (t.href?' data-href="'+esc(t.href)+'" title="Click for the ticket list"':"")+'>'+
        '<span class="k-val '+(t.vcls||"")+'">'+t.v+'</span>'+
        '<span class="k-lab">'+t.l+'</span><span class="k-sub">'+esc(t.s)+'</span></div>';
    }).join("");
    document.getElementById(hostId).querySelectorAll(".kpi.clickable").forEach(function(el){
      el.addEventListener("click",function(){location.href=el.getAttribute("data-href");});
    });
    if(tagline)document.getElementById("tagline").innerHTML=tagline;
  }
  function wire(id,state,key,render,cast){
    var host=document.getElementById(id);
    if(!host)return;
    host.addEventListener("click",function(ev){
      var b=ev.target.closest("button[data-v]");if(!b)return;
      state[key]=cast?cast(b.dataset.v):b.dataset.v;
      host.querySelectorAll("button").forEach(function(x){
        x.setAttribute("aria-pressed",String(x===b));});
      render();
    });
  }
  function table(hostId,titleId,pts,unit,cols,asc,cell){
    document.getElementById(titleId).textContent=
      "Per-bucket detail — "+pts.length+" "+unit+(pts.length===1?"":"s");
    var rows=(asc?pts:pts.slice().reverse()).map(function(p){
      return '<tr><td><a href="'+esc(closedHref(unit,p))+'">'+fmtPeriod(unit,p.period)+'</a></td>'+
        cols.map(function(c){return "<td>"+cell(p,c[0])+"</td>";}).join("")+'</tr>';
    }).join("");
    document.getElementById(hostId).innerHTML=
      '<div class="ctable tall"><table><thead><tr><th>'+unit+(asc?" ↑":" ↓")+'</th>'+
      cols.map(function(c){return "<th>"+c[1]+"</th>";}).join("")+
      '</tr></thead><tbody>'+rows+'</tbody></table></div>';
  }
  function csv(name,pts,unit,cols,asc,cell){
    var head=[unit].concat(cols.map(function(c){return c[1];})).join(",");
    var body=(asc?pts:pts.slice().reverse()).map(function(p){
      return [p.period].concat(cols.map(function(c){
        return String(cell(p,c[0])).replace(/[+d%]/g,"");})).join(",");
    }).join("\n");
    var url=URL.createObjectURL(new Blob([head+"\n"+body+"\n"],{type:"text/csv"}));
    var a=document.createElement("a");
    a.href=url;a.download=name+"-"+unit+"-"+new Date().toISOString().slice(0,10)+".csv";
    a.click();URL.revokeObjectURL(url);
  }
  function boot(render){
    fetch("/api/data").then(function(r){return r.json();}).then(function(d){
      if(d.error){document.getElementById("stamp").textContent="scan error: "+d.error;return;}
      document.getElementById("stamp").textContent="live scan · "+d.generated;
      document.getElementById("foot-src").textContent=d.source+"/";
      document.getElementById("foot-time").textContent="Scanned "+d.generated;
      render(d);
    }).catch(function(e){document.getElementById("stamp").textContent="load failed: "+e;});
  }
  return {days:days,monthShort:monthShort,fmtPeriod:fmtPeriod,fmtDate:fmtDate,
          toUTC:toUTC,isoOf:isoOf,periodEnd:periodEnd,addBuckets:addBuckets,
          barPath:barPath,esc:esc,closedHref:closedHref,createdHref:createdHref,
          pointsInRange:pointsInRange,avgWindow:avgWindow,rollingAvg:rollingAvg,
          fit:fit,xLabels:xLabels,tip:tip,tiles:tiles,wire:wire,table:table,
          csv:csv,boot:boot};
})();
</script>"""

BUCKET_CONTROLS = r"""
      <span class="lab">bucket</span>
      <div class="controls" role="group" aria-label="Time bucket" id="bucketSel">
        <button type="button" data-v="day" aria-pressed="true">day</button>
        <button type="button" data-v="week" aria-pressed="false">week</button>
        <button type="button" data-v="month" aria-pressed="false">month</button>
      </div>
      <span class="lab">range</span>
      <div class="controls" role="group" aria-label="Date range" id="rangeSel">
        <button type="button" data-v="14" aria-pressed="false">14d</button>
        <button type="button" data-v="30" aria-pressed="false">30d</button>
        <button type="button" data-v="90" aria-pressed="false">90d</button>
        <button type="button" data-v="0" aria-pressed="true">all</button>
      </div>"""

DETAIL_FIGURE = r"""
  <figure class="chart">
    <figcaption class="chart-cap">
      <span class="chart-title" id="tblTitle">Per-bucket detail</span>
      <button type="button" class="lnk" id="sortBtn">oldest first</button>
      <button type="button" class="lnk" id="csvBtn">download CSV</button>
    </figcaption>
    <div id="tableHost"></div>
    <p class="chart-note">Newest bucket first. Click a bucket for the tickets closed in it.</p>
  </figure>"""


def _flow_page(title, emoji, eyebrow, heading, controls, body, script, extra_css=""):
    """Page shell shared by /throughput, /burnup and /deps."""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>" + title + " &middot; __BRAND__</title>"
        "<link rel=\"icon\" href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>" + emoji + "</text></svg>\">"
        "<style>" + DASHBOARD_CSS + FLOW_CSS + extra_css + "</style></head>"
        '<body><div class="page"><div class="wrap">'
        '<header class="head">'
        '<a class="hbtn" href="/">&larr; dashboard</a>'
        '<p class="eyebrow" style="margin-top:14px">' + eyebrow + "</p>"
        "<h1>" + heading + "</h1>"
        '<p class="tagline" id="tagline">&nbsp;</p>'
        '<div class="stamp-row">'
        '<span class="stamp"><span class="dot"></span><span id="stamp">loading&hellip;</span></span>'
        '<div class="tp-bar">' + controls + "</div></div></header>"
        '<div class="kpis flow" id="tiles"></div>'
        '<div class="tp-charts">' + body + "</div>"
        "<footer><p class=\"mono\">Live scan of <span id=\"foot-src\">tickets</span>"
        ' &middot; <span id="foot-time"></span></p></footer>'
        "</div></div>" + SCROLLNAV_HTML + FLOW_JS + "<script>" + script
        + "</script>" + SCROLLNAV_JS + "</body></html>"
    )


THROUGHPUT_BODY = r"""
  <figure class="chart">
    <figcaption class="chart-cap">
      <span class="chart-title" id="mainTitle">Throughput</span>
      <span class="legend2" id="mainLegend"></span>
    </figcaption>
    <div class="chart-plot" id="chartMain"></div>
    <p class="chart-note" id="mainNote"></p>
  </figure>

  <figure class="chart" id="netFig">
    <figcaption class="chart-cap">
      <span class="chart-title">Net flow &mdash; created minus closed</span>
      <span class="legend2">
        <span class="lg2"><i class="sw2 sw-created"></i>Backlog grew</span>
        <span class="lg2"><i class="sw2 sw-closed"></i>Backlog shrank</span>
      </span>
    </figcaption>
    <div class="chart-plot" id="chartNet"></div>
    <p class="chart-note">Bars above the line are buckets that opened more than they closed. Sustained height above the line is where the backlog came from.</p>
  </figure>"""

THROUGHPUT_SCRIPT = r"""
(function(){
  var F=FLOW,STATE={bucket:"day",range:0,metric:"tickets",asc:false,data:null};

  function renderTiles(pts,unit){
    var created=0,closed=0,effH=0,peak=null,i;
    for(i=0;i<pts.length;i++){
      created+=pts[i].created;closed+=pts[i].closed;effH+=pts[i].effortClosedH||0;
      if(!peak||pts[i].closed>peak.closed)peak=pts[i];
    }
    var net=created-closed,last=pts.length?pts[pts.length-1]:null;
    var backlog=last?(last.cumCreated-last.cumClosed):0;
    var avg=pts.length?(closed/pts.length):0;
    var span=pts.length?(F.fmtPeriod(unit,pts[0].period)+" → "+F.fmtPeriod(unit,pts[pts.length-1].period)):"no data";
    var win=pts.length?("&closed_from="+pts[0].period+"&closed_to="+F.periodEnd(unit,pts[pts.length-1].period)):"";
    F.tiles("tiles",[
      {v:created.toLocaleString(),l:"Created",s:span,cls:"is-open"},
      {v:closed.toLocaleString(),l:"Closed",s:span,cls:"is-done",
       href:pts.length?("/filter?status=closed"+win+"&label="+encodeURIComponent("Closed · "+span)):""},
      {v:(net>0?"+":"")+net,l:"Net flow",s:"created − closed",vcls:net>0?"neg":net<0?"pos":""},
      {v:(Math.round(avg*10)/10),l:"Avg closed / "+unit,s:pts.length+" "+unit+(pts.length===1?"":"s")},
      {v:peak?peak.closed:0,l:"Peak "+unit,s:peak?F.fmtPeriod(unit,peak.period):"—",
       href:peak?F.closedHref(unit,peak):""},
      {v:F.days(effH).toLocaleString()+"d",l:"Effort closed",s:"1d = 8h"}
    ],"<b>"+closed.toLocaleString()+"</b> closed against <b>"+created.toLocaleString()+
      "</b> created over "+F.esc(span)+". <b>"+
      (backlog>0?backlog.toLocaleString()+" unresolved":"none unresolved")+"</b> repo-wide.");
  }

  function drawMain(pts,unit){
    var host=document.getElementById("chartMain"),eff=STATE.metric==="effort",w=F.avgWindow(unit);
    document.getElementById("mainTitle").textContent=eff?
      ("Effort closed per "+unit):("Throughput — created vs closed per "+unit);
    document.getElementById("mainLegend").innerHTML=eff?
      '<span class="lg2"><i class="sw2 sw-eff"></i>Effort days</span><span class="lg2"><i class="sw2 sw-avg"></i>'+w+'-'+unit+' avg</span>':
      '<span class="lg2"><i class="sw2 sw-created"></i>Created</span><span class="lg2"><i class="sw2 sw-closed"></i>Closed</span><span class="lg2"><i class="sw2 sw-avg"></i>'+w+'-'+unit+' avg closed</span>';
    document.getElementById("mainNote").innerHTML=eff?
      "Estimated effort of the tickets closed in each "+unit+". The dashed line is a "+w+"-"+unit+" trailing average. Click a bar for the ticket list.":
      "Click a bar for the tickets it counts. The dashed line is a "+w+"-"+unit+" trailing average of closed, which reads through the day-to-day noise.";
    if(!pts.length){host.innerHTML='<div class="chart-empty">No dated tickets in this range.</div>';return;}

    var W=980,H=320,padL=44,padR=16,padT=14,padB=30,n=pts.length,i;
    var val=function(p){return eff?F.days(p.effortClosedH||0):0;};
    var maxY=1;
    for(i=0;i<n;i++)maxY=Math.max(maxY,eff?val(pts[i]):Math.max(pts[i].created,pts[i].closed));
    var niceMax=Math.max(5,Math.ceil(maxY/10)*10);
    var plotW=W-padL-padR,plotH=H-padT-padB,groupW=plotW/n,gap=2,series=eff?1:2;
    var barW=Math.min(26,Math.max(2,(groupW-gap*(series+1))/series));
    var off=(groupW-(barW*series+gap*(series-1)))/2;
    function Y(v){return padT+plotH*(1-v/niceMax);}

    var svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+
      (eff?"Effort days closed per ":"Tickets created and closed per ")+unit+'">';
    var step=niceMax/4,k,yy,yv;
    for(k=0;k<=4;k++){yv=Math.round(step*k);yy=Y(yv);
      svg+='<line class="grid-line" x1="'+padL+'" y1="'+yy+'" x2="'+(W-padR)+'" y2="'+yy+'" stroke-width="1"/>';
      svg+='<text class="axis-txt" x="'+(padL-6)+'" y="'+(yy+3)+'" text-anchor="end" font-size="10">'+yv+'</text>';
    }
    for(i=0;i<n;i++){
      var gx=padL+groupW*i+off,p=pts[i];
      if(eff){
        svg+='<a href="'+F.esc(F.closedHref(unit,p))+'"><path class="bar-eff" d="'+
          F.barPath(gx,Y(val(p)),barW,plotH*(val(p)/niceMax),4)+'"/></a>';
      }else{
        svg+='<a href="'+F.esc(F.createdHref(unit,p))+'"><path class="bar-created" d="'+
          F.barPath(gx,Y(p.created),barW,plotH*(p.created/niceMax),4)+'"/></a>';
        svg+='<a href="'+F.esc(F.closedHref(unit,p))+'"><path class="bar-closed" d="'+
          F.barPath(gx+barW+gap,Y(p.closed),barW,plotH*(p.closed/niceMax),4)+'"/></a>';
      }
    }
    var avg=F.rollingAvg(pts,eff?"effortClosedH":"closed",w),dpath="";
    for(i=0;i<n;i++){
      var av=eff?F.days(avg[i]):avg[i];
      dpath+=(i?"L":"M")+(padL+groupW*i+groupW/2)+" "+Y(Math.min(av,niceMax));
    }
    svg+='<path class="ln-avg" d="'+dpath+'"/>';
    F.xLabels(pts,unit).forEach(function(t){
      svg+='<text class="axis-txt" x="'+(padL+groupW*t.i+groupW/2)+'" y="'+(H-8)+
        '" text-anchor="middle" font-size="10">'+t.txt+'</text>';
    });
    svg+='</svg>';
    host.innerHTML=svg;
    var tp=F.tip(host,"m-tip"),svgEl=host.querySelector("svg");
    svgEl.addEventListener("mousemove",function(ev){
      var r=svgEl.getBoundingClientRect();
      var idx=Math.floor(((ev.clientX-r.left)/r.width*W-padL)/groupW);
      if(idx<0)idx=0; if(idx>n-1)idx=n-1;
      var p=pts[idx],top=eff?val(p):Math.max(p.created,p.closed);
      tp.style.opacity="1";
      tp.style.left=((padL+groupW*idx+groupW/2)/W*r.width)+"px";
      tp.style.top=(Y(top)/H*r.height)+"px";
      tp.innerHTML='<b>'+F.fmtPeriod(unit,p.period)+'</b>'+
        (eff?'':'<div class="row"><span class="k">created</span><span>'+p.created+'</span></div>'+
             '<div class="row"><span class="k">closed</span><span>'+p.closed+'</span></div>'+
             '<div class="row"><span class="k">net</span><span>'+(p.created-p.closed>0?"+":"")+(p.created-p.closed)+'</span></div>')+
        '<div class="row"><span class="k">effort</span><span>'+F.days(p.effortClosedH||0)+'d</span></div>'+
        '<div class="row"><span class="k">avg</span><span>'+(Math.round((eff?F.days(avg[idx]):avg[idx])*10)/10)+'</span></div>';
    });
    svgEl.addEventListener("mouseleave",function(){tp.style.opacity="0";});
  }

  function drawNet(pts,unit){
    var fig=document.getElementById("netFig");
    fig.hidden=STATE.metric==="effort";
    if(fig.hidden)return;
    var host=document.getElementById("chartNet");
    if(!pts.length){host.innerHTML='<div class="chart-empty">No dated tickets in this range.</div>';return;}
    var W=980,H=220,padL=44,padR=16,padT=14,padB=30,n=pts.length,i,mag=1;
    for(i=0;i<n;i++)mag=Math.max(mag,Math.abs(pts[i].created-pts[i].closed));
    var niceMax=Math.max(5,Math.ceil(mag/5)*5);
    var plotW=W-padL-padR,plotH=H-padT-padB,groupW=plotW/n;
    var barW=Math.min(26,Math.max(2,groupW-3)),zero=padT+plotH/2;
    function Y(v){return zero-(v/niceMax)*(plotH/2);}
    var svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Net tickets created minus closed per '+unit+'">';
    [niceMax,0,-niceMax].forEach(function(yv){
      var yy=Y(yv);
      svg+='<line class="'+(yv===0?"zero-line":"grid-line")+'" x1="'+padL+'" y1="'+yy+'" x2="'+(W-padR)+'" y2="'+yy+'" stroke-width="1"/>';
      svg+='<text class="axis-txt" x="'+(padL-6)+'" y="'+(yy+3)+'" text-anchor="end" font-size="10">'+(yv>0?"+":"")+yv+'</text>';
    });
    for(i=0;i<n;i++){
      var p=pts[i],net=p.created-p.closed;
      if(!net)continue;
      var x=padL+groupW*i+(groupW-barW)/2;
      var y=net>0?Y(net):zero,h=Math.abs(Y(net)-zero);
      svg+='<path class="'+(net>0?"bar-created":"bar-closed")+'" d="'+F.barPath(x,y,barW,h,3)+'"/>';
    }
    F.xLabels(pts,unit).forEach(function(t){
      svg+='<text class="axis-txt" x="'+(padL+groupW*t.i+groupW/2)+'" y="'+(H-8)+
        '" text-anchor="middle" font-size="10">'+t.txt+'</text>';
    });
    svg+='</svg>';
    host.innerHTML=svg;
    var tp=F.tip(host,"n-tip"),svgEl=host.querySelector("svg");
    svgEl.addEventListener("mousemove",function(ev){
      var r=svgEl.getBoundingClientRect();
      var idx=Math.floor(((ev.clientX-r.left)/r.width*W-padL)/groupW);
      if(idx<0)idx=0; if(idx>n-1)idx=n-1;
      var p=pts[idx],net=p.created-p.closed;
      tp.style.opacity="1";
      tp.style.left=((padL+groupW*idx+groupW/2)/W*r.width)+"px";
      tp.style.top=((net>0?Y(net):zero)/H*r.height)+"px";
      tp.innerHTML='<b>'+F.fmtPeriod(unit,p.period)+'</b>'+
        '<div class="row"><span class="k">net</span><span>'+(net>0?"+":"")+net+'</span></div>'+
        '<div class="row"><span class="k">created</span><span>'+p.created+'</span></div>'+
        '<div class="row"><span class="k">closed</span><span>'+p.closed+'</span></div>';
    });
    svgEl.addEventListener("mouseleave",function(){tp.style.opacity="0";});
  }

  var COLS=[["created","created"],["closed","closed"],["net","net"],["effD","effort"],
            ["cumCreated","created Σ"],["cumClosed","closed Σ"],["gap","unresolved"]];
  function cell(p,key){
    if(key==="net"){var v=p.created-p.closed;return (v>0?"+":"")+v;}
    if(key==="gap")return p.cumCreated-p.cumClosed;
    if(key==="effD")return F.days(p.effortClosedH||0)+"d";
    return p[key];
  }
  function render(){
    var s=F.pointsInRange(STATE.data,STATE.bucket,STATE.range);
    renderTiles(s.points,s.unit);
    drawMain(s.points,s.unit);
    drawNet(s.points,s.unit);
    F.table("tableHost","tblTitle",s.points,s.unit,COLS,STATE.asc,cell);
    document.getElementById("sortBtn").textContent=STATE.asc?"newest first":"oldest first";
  }
  F.wire("bucketSel",STATE,"bucket",render);
  F.wire("rangeSel",STATE,"range",render,Number);
  F.wire("metricSel",STATE,"metric",render);
  document.getElementById("sortBtn").addEventListener("click",function(){STATE.asc=!STATE.asc;render();});
  document.getElementById("csvBtn").addEventListener("click",function(){
    var s=F.pointsInRange(STATE.data,STATE.bucket,STATE.range);
    F.csv("throughput",s.points,s.unit,COLS,STATE.asc,cell);});
  F.boot(function(d){STATE.data=d;render();});
})();
"""

THROUGHPUT_HTML = _flow_page(
    "Throughput", "📈", "Flow metrics", "Throughput",
    BUCKET_CONTROLS + r"""
      <span class="lab">measure</span>
      <div class="controls" role="group" aria-label="Measure" id="metricSel">
        <button type="button" data-v="tickets" aria-pressed="true">tickets</button>
        <button type="button" data-v="effort" aria-pressed="false">effort</button>
      </div>""",
    THROUGHPUT_BODY + DETAIL_FIGURE, THROUGHPUT_SCRIPT)


BURNUP_BODY = r"""
  <figure class="chart">
    <figcaption class="chart-cap">
      <span class="chart-title" id="mainTitle">Burn-up</span>
      <span class="legend2" id="mainLegend"></span>
    </figcaption>
    <div class="chart-plot" id="chartMain"></div>
    <p class="chart-note" id="mainNote"></p>
  </figure>

  <figure class="chart">
    <figcaption class="chart-cap">
      <span class="chart-title">Unresolved backlog over time</span>
      <span class="legend2"><span class="lg2"><i class="sw2 sw-gap"></i>Created &minus; closed, cumulative</span></span>
    </figcaption>
    <div class="chart-plot" id="chartGap"></div>
    <p class="chart-note">The vertical gap between the burn-up lines, plotted on its own. Flat means closing at the rate work arrives; rising means the backlog is growing.</p>
  </figure>"""

BURNUP_SCRIPT = r"""
(function(){
  var F=FLOW,STATE={bucket:"day",range:0,forecast:"on",asc:false,data:null};

  // Fit scope and done over the trailing window; they converge when done is
  // gaining on scope. Returns null when it never converges (scope outruns done).
  function project(pts,unit){
    var n=pts.length;
    if(n<2)return null;
    var win=Math.max(3,Math.min(n,unit==="day"?14:unit==="week"?6:3));
    var sc=F.fit(pts,"cumCreated",win),dn=F.fit(pts,"cumClosed",win);
    var horizon=(n-1)+Math.max(6,Math.ceil(n*1.5));
    if(dn.b<=sc.b)return {scope:sc,done:dn,meet:null,horizon:horizon};
    var t=(sc.a-dn.a)/(dn.b-sc.b);
    if(t<n-1)t=n-1;                       // already met: pin to today
    return {scope:sc,done:dn,meet:t,horizon:horizon,
            beyond:t>horizon,
            at:Math.min(t,horizon)};
  }

  function renderTiles(pts,unit,pr){
    var last=pts.length?pts[pts.length-1]:null;
    var scope=last?last.cumCreated:0,done=last?last.cumClosed:0,left=scope-done;
    var pctDone=scope?(done/scope*100):0;
    var span=pts.length?(F.fmtPeriod(unit,pts[0].period)+" → "+F.fmtPeriod(unit,pts[pts.length-1].period)):"no data";
    var scopeRate=pr?Math.round(pr.scope.b*10)/10:0,doneRate=pr?Math.round(pr.done.b*10)/10:0;
    var eta="—",etaSub="needs a closing rate above the scope rate";
    if(pr&&pr.meet!=null){
      if(pr.beyond){eta="beyond horizon";etaSub="converges later than "+F.fmtDate(F.addBuckets(unit,pts[pts.length-1].period,pr.horizon-(pts.length-1)));}
      else{
        var iso=F.addBuckets(unit,pts[pts.length-1].period,pr.meet-(pts.length-1));
        eta=F.fmtDate(iso);
        etaSub=pr.meet<=pts.length-1?"scope already met":"at the current "+unit+" rates";
      }
    }
    F.tiles("tiles",[
      {v:scope.toLocaleString(),l:"Scope",s:"tickets created, all time",cls:"is-open"},
      {v:done.toLocaleString(),l:"Done",s:"tickets closed, all time",cls:"is-done",
       href:"/filter?status=closed&label="+encodeURIComponent("Closed and shipped")},
      {v:left.toLocaleString(),l:"Remaining",s:"open + won't fix",
       href:"/filter?status=open&label="+encodeURIComponent("Open tickets")},
      {v:pctDone.toFixed(1)+"%",l:"Complete",s:span},
      {v:(scopeRate>0?"+":"")+scopeRate,l:"Scope / "+unit,s:"trailing fit",vcls:scopeRate>doneRate?"neg":""},
      {v:(doneRate>0?"+":"")+doneRate,l:"Closing / "+unit,s:"trailing fit",vcls:doneRate>scopeRate?"pos":""},
      {v:eta,l:"Projected finish",s:etaSub,vcls:(pr&&pr.meet!=null&&!pr.beyond)?"pos":"neg"}
    ],"<b>"+pctDone.toFixed(1)+"%</b> complete — <b>"+done.toLocaleString()+"</b> of <b>"+
      scope.toLocaleString()+"</b> tickets closed, <b>"+left.toLocaleString()+"</b> still to go.");
  }

  function drawMain(pts,unit){
    var host=document.getElementById("chartMain");
    var pr=STATE.forecast==="on"?project(pts,unit):null;
    document.getElementById("mainTitle").textContent="Burn-up — cumulative created vs closed per "+unit;
    document.getElementById("mainLegend").innerHTML=
      '<span class="lg2"><i class="sw2 sw-created"></i>Scope (created)</span>'+
      '<span class="lg2"><i class="sw2 sw-closed"></i>Done (closed)</span>'+
      (pr?'<span class="lg2"><i class="sw2 sw-avg"></i>Projection</span>':'');
    document.getElementById("mainNote").innerHTML=pr?
      ("Dashed rays extend the trailing fit of each line. Where they cross is when closing catches scope, if the current rates hold — a forecast, not a promise."):
      ("The gap between the lines is work not yet closed. Turn the projection on to extend both trends.");
    if(!pts.length){host.innerHTML='<div class="chart-empty">No dated tickets in this range.</div>';return;}

    var W=980,H=340,padL=52,padR=54,padT=16,padB=30,n=pts.length,i;
    var dom=(pr&&pr.meet!=null)?Math.max(n-1,pr.at):(n-1);
    if(pr&&pr.meet==null)dom=n-1;
    var maxY=pts[n-1].cumCreated||1;
    if(pr&&pr.meet!=null)maxY=Math.max(maxY,pr.done.a+pr.done.b*pr.at,pr.scope.a+pr.scope.b*pr.at);
    var niceMax=Math.max(100,Math.ceil(maxY/200)*200);
    var plotW=W-padL-padR,plotH=H-padT-padB;
    function X(i){return padL+plotW*(dom<=0?0.5:i/dom);}
    function Y(v){return padT+plotH*(1-v/niceMax);}

    var svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Cumulative tickets created versus closed per '+unit+'">';
    var step=niceMax/4,k,yy,yv;
    for(k=0;k<=4;k++){yv=step*k;yy=Y(yv);
      svg+='<line class="grid-line" x1="'+padL+'" y1="'+yy+'" x2="'+(W-padR)+'" y2="'+yy+'" stroke-width="1"/>';
      svg+='<text class="axis-txt" x="'+(padL-6)+'" y="'+(yy+3)+'" text-anchor="end" font-size="10">'+yv+'</text>';
    }
    F.xLabels(pts,unit,10).forEach(function(t){
      svg+='<text class="axis-txt" x="'+X(t.i)+'" y="'+(H-8)+'" text-anchor="middle" font-size="10">'+t.txt+'</text>';
    });
    var cA="M"+X(0)+","+Y(0),xA="M"+X(0)+","+Y(0),cL="",xL="";
    for(i=0;i<n;i++){
      cA+=" L"+X(i)+","+Y(pts[i].cumCreated);
      xA+=" L"+X(i)+","+Y(pts[i].cumClosed);
      cL+=(i?"L":"M")+X(i)+","+Y(pts[i].cumCreated)+" ";
      xL+=(i?"L":"M")+X(i)+","+Y(pts[i].cumClosed)+" ";
    }
    cA+=" L"+X(n-1)+","+Y(0)+" Z"; xA+=" L"+X(n-1)+","+Y(0)+" Z";
    svg+='<path class="ar-created" d="'+cA+'"/><path class="ar-closed" d="'+xA+'"/>';
    svg+='<path class="ln-created" d="'+cL+'"/><path class="ln-closed" d="'+xL+'"/>';

    if(pr){
      var end=(pr.meet!=null)?pr.at:dom;
      if(end>n-1){
        svg+='<line class="now-line" x1="'+X(n-1)+'" y1="'+padT+'" x2="'+X(n-1)+'" y2="'+(H-padB)+'" stroke-width="1"/>';
        svg+='<path class="ln-proj-created" d="M'+X(n-1)+','+Y(pts[n-1].cumCreated)+
          'L'+X(end)+','+Y(Math.min(pr.scope.a+pr.scope.b*end,niceMax))+'"/>';
        svg+='<path class="ln-proj-closed" d="M'+X(n-1)+','+Y(pts[n-1].cumClosed)+
          'L'+X(end)+','+Y(Math.min(pr.done.a+pr.done.b*end,niceMax))+'"/>';
      }
      if(pr.meet!=null&&!pr.beyond){
        var my=Y(Math.min(pr.done.a+pr.done.b*pr.at,niceMax));
        svg+='<circle class="meet" cx="'+X(pr.at)+'" cy="'+my+'" r="4.5"/>';
        svg+='<text class="proj-txt" x="'+X(pr.at)+'" y="'+(my-10)+'" text-anchor="middle" font-size="10">'+
          F.esc(F.fmtDate(F.addBuckets(unit,pts[n-1].period,pr.at-(n-1))))+'</text>';
      }
    }
    var lc=pts[n-1].cumCreated,lx=pts[n-1].cumClosed;
    svg+='<circle class="dot-created" cx="'+X(n-1)+'" cy="'+Y(lc)+'" r="4"/>';
    svg+='<circle class="dot-closed" cx="'+X(n-1)+'" cy="'+Y(lx)+'" r="4"/>';
    svg+='<text x="'+(X(n-1)+7)+'" y="'+(Y(lc)-6)+'" font-size="10" style="fill:var(--c-created);font-family:var(--font-mono);font-weight:700">'+lc+'</text>';
    svg+='<text x="'+(X(n-1)+7)+'" y="'+(Y(lx)+13)+'" font-size="10" style="fill:var(--c-closed);font-family:var(--font-mono);font-weight:700">'+lx+'</text>';
    svg+='<line id="bu-cross" class="cross" x1="0" y1="'+padT+'" x2="0" y2="'+(H-padB)+'" stroke-width="1" style="opacity:0"/>';
    svg+='<circle id="bu-dc" r="4" class="dot-created" style="opacity:0"/><circle id="bu-dx" r="4" class="dot-closed" style="opacity:0"/>';
    svg+='</svg>';
    host.innerHTML=svg;
    var tp=F.tip(host,"bu-tip"),svgEl=host.querySelector("svg");
    var cross=host.querySelector("#bu-cross"),dc=host.querySelector("#bu-dc"),dx=host.querySelector("#bu-dx");
    svgEl.addEventListener("mousemove",function(ev){
      var r=svgEl.getBoundingClientRect();
      var idx=Math.round(((ev.clientX-r.left)/r.width*W-padL)/plotW*dom);
      if(idx<0)idx=0; if(idx>n-1)idx=n-1;
      var p=pts[idx],px=X(idx);
      cross.setAttribute("x1",px);cross.setAttribute("x2",px);cross.style.opacity=".6";
      dc.setAttribute("cx",px);dc.setAttribute("cy",Y(p.cumCreated));dc.style.opacity="1";
      dx.setAttribute("cx",px);dx.setAttribute("cy",Y(p.cumClosed));dx.style.opacity="1";
      tp.style.opacity="1";
      tp.style.left=(px/W*r.width)+"px";
      tp.style.top=(Y(p.cumCreated)/H*r.height)+"px";
      tp.innerHTML='<b>'+F.fmtPeriod(unit,p.period)+'</b>'+
        '<div class="row"><span class="k">scope</span><span>'+p.cumCreated+'</span></div>'+
        '<div class="row"><span class="k">done</span><span>'+p.cumClosed+'</span></div>'+
        '<div class="row"><span class="k">unresolved</span><span>'+(p.cumCreated-p.cumClosed)+'</span></div>'+
        '<div class="row"><span class="k">complete</span><span>'+(p.cumCreated?(p.cumClosed/p.cumCreated*100).toFixed(1):"0.0")+'%</span></div>';
    });
    svgEl.addEventListener("mouseleave",function(){
      tp.style.opacity="0";cross.style.opacity="0";dc.style.opacity="0";dx.style.opacity="0";});
  }

  function drawGap(pts,unit){
    var host=document.getElementById("chartGap");
    if(!pts.length){host.innerHTML='<div class="chart-empty">No dated tickets in this range.</div>';return;}
    var W=980,H=220,padL=52,padR=54,padT=14,padB=30,n=pts.length,i,maxY=1;
    for(i=0;i<n;i++)maxY=Math.max(maxY,pts[i].cumCreated-pts[i].cumClosed);
    var niceMax=Math.max(50,Math.ceil(maxY/50)*50);
    var plotW=W-padL-padR,plotH=H-padT-padB;
    function X(i){return padL+plotW*(n===1?0.5:i/(n-1));}
    function Y(v){return padT+plotH*(1-v/niceMax);}
    var svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Unresolved tickets over time">';
    var step=niceMax/2,k,yy,yv;
    for(k=0;k<=2;k++){yv=step*k;yy=Y(yv);
      svg+='<line class="grid-line" x1="'+padL+'" y1="'+yy+'" x2="'+(W-padR)+'" y2="'+yy+'" stroke-width="1"/>';
      svg+='<text class="axis-txt" x="'+(padL-6)+'" y="'+(yy+3)+'" text-anchor="end" font-size="10">'+yv+'</text>';
    }
    var ar="M"+X(0)+","+Y(0),ln="";
    for(i=0;i<n;i++){var g=pts[i].cumCreated-pts[i].cumClosed;
      ar+=" L"+X(i)+","+Y(g);ln+=(i?"L":"M")+X(i)+","+Y(g)+" ";}
    ar+=" L"+X(n-1)+","+Y(0)+" Z";
    svg+='<path class="ar-gap" d="'+ar+'"/><path class="ln-gap" d="'+ln+'"/>';
    F.xLabels(pts,unit,10).forEach(function(t){
      svg+='<text class="axis-txt" x="'+X(t.i)+'" y="'+(H-8)+'" text-anchor="middle" font-size="10">'+t.txt+'</text>';
    });
    var lg=pts[n-1].cumCreated-pts[n-1].cumClosed;
    svg+='<circle class="meet" cx="'+X(n-1)+'" cy="'+Y(lg)+'" r="4"/>';
    svg+='<text class="proj-txt" x="'+(X(n-1)+7)+'" y="'+(Y(lg)+4)+'" font-size="10">'+lg+'</text>';
    svg+='</svg>';
    host.innerHTML=svg;
    var tp=F.tip(host,"g-tip"),svgEl=host.querySelector("svg");
    svgEl.addEventListener("mousemove",function(ev){
      var r=svgEl.getBoundingClientRect();
      var idx=Math.round(((ev.clientX-r.left)/r.width*W-padL)/plotW*(n-1));
      if(idx<0)idx=0; if(idx>n-1)idx=n-1;
      var p=pts[idx],g=p.cumCreated-p.cumClosed;
      tp.style.opacity="1";
      tp.style.left=(X(idx)/W*r.width)+"px";
      tp.style.top=(Y(g)/H*r.height)+"px";
      tp.innerHTML='<b>'+F.fmtPeriod(unit,p.period)+'</b>'+
        '<div class="row"><span class="k">unresolved</span><span>'+g+'</span></div>'+
        '<div class="row"><span class="k">net this '+unit+'</span><span>'+
          ((p.created-p.closed)>0?"+":"")+(p.created-p.closed)+'</span></div>';
    });
    svgEl.addEventListener("mouseleave",function(){tp.style.opacity="0";});
  }

  var COLS=[["cumCreated","scope Σ"],["cumClosed","done Σ"],["gap","unresolved"],
            ["pct","complete"],["created","created"],["closed","closed"],["net","net"]];
  function cell(p,key){
    if(key==="gap")return p.cumCreated-p.cumClosed;
    if(key==="pct")return (p.cumCreated?(p.cumClosed/p.cumCreated*100).toFixed(1):"0.0")+"%";
    if(key==="net"){var v=p.created-p.closed;return (v>0?"+":"")+v;}
    return p[key];
  }
  function render(){
    var s=F.pointsInRange(STATE.data,STATE.bucket,STATE.range);
    var pr=STATE.forecast==="on"?project(s.points,s.unit):null;
    renderTiles(s.points,s.unit,pr);
    drawMain(s.points,s.unit);
    drawGap(s.points,s.unit);
    F.table("tableHost","tblTitle",s.points,s.unit,COLS,STATE.asc,cell);
    document.getElementById("sortBtn").textContent=STATE.asc?"newest first":"oldest first";
  }
  F.wire("bucketSel",STATE,"bucket",render);
  F.wire("rangeSel",STATE,"range",render,Number);
  F.wire("fcSel",STATE,"forecast",render);
  document.getElementById("sortBtn").addEventListener("click",function(){STATE.asc=!STATE.asc;render();});
  document.getElementById("csvBtn").addEventListener("click",function(){
    var s=F.pointsInRange(STATE.data,STATE.bucket,STATE.range);
    F.csv("burnup",s.points,s.unit,COLS,STATE.asc,cell);});
  F.boot(function(d){STATE.data=d;render();});
})();
"""

BURNUP_HTML = _flow_page(
    "Burn-up", "📊", "Flow metrics", "Burn-up",
    BUCKET_CONTROLS + r"""
      <span class="lab">projection</span>
      <div class="controls" role="group" aria-label="Projection" id="fcSel">
        <button type="button" data-v="on" aria-pressed="true">on</button>
        <button type="button" data-v="off" aria-pressed="false">off</button>
      </div>""",
    BURNUP_BODY + DETAIL_FIGURE, BURNUP_SCRIPT)


DEPS_CSS = r"""
.sw-wf{background:var(--wf)}.sw-focus{background:var(--warn)}
#graphHost{min-width:0;max-width:100%}
.dgx-wrap{position:relative;overflow:auto;border:1px solid var(--line);border-radius:9px;max-width:100%;
background:var(--surface-2);max-height:min(72vh,760px)}
.dgx-wrap svg{display:block}
.dgx-edge{fill:none;stroke:var(--line);stroke-width:1.5}
.dgx-edge.past{stroke:var(--done);stroke-dasharray:3 3;opacity:.55}
.dgx-edge.hot{stroke:var(--warn);stroke-width:2.4;opacity:1}
.dgx-node{fill:var(--surface);stroke:var(--line);stroke-width:1}
.dgx-node.open{fill:var(--accent-soft);stroke:var(--accent)}
.dgx-node.closed{fill:var(--done-soft);stroke:var(--done)}
.dgx-node.wf{fill:var(--wf-soft);stroke:var(--wf)}
.dgx-node.focus{stroke:var(--warn);stroke-width:2.5}
.dgx-g{cursor:pointer}
.dgx-g:hover .dgx-node{stroke:var(--warn);stroke-width:2}
.dgx-id{font-family:var(--font-mono);font-size:10px;font-weight:700;fill:var(--ink)}
.dgx-ttl{font-family:var(--font-sans);font-size:10px;fill:var(--ink-mut)}
.dgx-open{font-family:var(--font-mono);font-size:10px;fill:var(--accent-ink)}
.dgx-col{font-family:var(--font-mono);font-size:10px;fill:var(--ink-mut);letter-spacing:.06em}
.dgx-colline{stroke:var(--line-2);stroke-dasharray:2 4}
.dgx-empty{color:var(--ink-mut);font-size:.85rem;padding:40px 12px;text-align:center}
.dgsearch{font-family:var(--font-mono);font-size:.74rem;color:var(--ink);background:var(--surface);
border:1px solid var(--line);border-radius:100px;padding:6px 13px;max-width:min(42vw,300px);cursor:pointer}
.dgsearch:focus{outline:2px solid var(--accent);outline-offset:1px}
.dgsearch option{font-family:var(--font-mono);color:var(--ink)}
#dgEpic{max-width:min(42vw,260px)}
/* rich node hover overlay */
.dgxtip{transform:none;white-space:normal;min-width:180px;max-width:300px;padding:9px 11px;line-height:1.45}
.dgxtip.flip{transform:translateX(-100%)}
.dgxtip .tt-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:4px}
.dgxtip .tt-id{font-weight:700;color:var(--accent-ink)}
.dgxtip .tt-ttl{font-family:var(--font-sans);font-size:.8rem;font-weight:600;line-height:1.3;margin-bottom:5px}
.dgxtip .tt-epic{color:var(--ink-mut);font-size:.66rem;margin-bottom:6px}
.dgxtip .tt-pills{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:6px}
.dgxtip .tt-pill{border:1px solid var(--line);border-radius:4px;padding:1px 6px;font-size:.62rem;color:var(--ink-mut)}
.dgxtip .tt-pill.r-high{color:#c23b3b;border-color:#c23b3b}
.dgxtip .tt-pill.r-medium{color:var(--warn);border-color:var(--warn)}
.dgxtip .tt-pill.r-low{color:var(--done);border-color:var(--done)}
.dgxtip .tt-row{display:flex;justify-content:space-between;gap:18px}
.dgxtip .tt-row .k{color:var(--ink-mut)}
.dgxtip .tt-hint{color:var(--ink-mut);font-size:.63rem;font-style:italic;margin-top:5px}
/* focused ticket, named in full above the graph */
.focus-card{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:baseline;padding:11px 13px;margin:0 0 12px;
background:var(--surface-2);border:1px solid var(--line);border-radius:7px}
.fc-id{font-family:var(--font-mono);font-size:.78rem;font-weight:700;color:var(--accent-ink)}
.fc-ttl{font-size:.92rem;font-weight:600;letter-spacing:-.01em;flex:1 1 240px;min-width:0}
.fc-badge{font-family:var(--font-mono);font-size:.6rem;letter-spacing:.05em;padding:2px 7px;border-radius:4px}
.fc-badge.open{background:var(--accent-soft);color:var(--accent-ink)}
.fc-badge.closed{background:var(--done-soft);color:var(--done)}
.fc-badge.wf{background:var(--wf-soft);color:var(--ink-mut)}
.fc-epic{font-family:var(--font-mono);font-size:.68rem;color:var(--ink-mut);text-decoration:none;
border:1px solid var(--line);border-radius:5px;padding:2px 7px;background:var(--surface)}
.fc-epic:hover{border-color:var(--accent);color:var(--accent-ink)}
/* unfocused overview */
.ov{padding:4px 2px 2px}
.ov-grid{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(258px,1fr));margin-top:10px}
.ov-card{display:flex;gap:9px;align-items:baseline;padding:9px 11px;border:1px solid var(--line);
border-radius:7px;background:var(--surface);text-decoration:none;color:inherit}
.ov-card:hover{border-color:var(--warn)}
.ov-id{font-family:var(--font-mono);font-size:.7rem;font-weight:700;color:var(--accent-ink);flex:none}
.ov-ttl{font-size:.76rem;color:var(--ink-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ov-n{margin-left:auto;font-family:var(--font-mono);font-size:.66rem;color:var(--warn);flex:none}
.ov h4{margin:0;font-size:.82rem}
.ov p{margin:4px 0 0;font-size:.76rem;color:var(--ink-mut)}
.hop-lists{display:grid;gap:16px}
@media(min-width:880px){.hop-lists{grid-template-columns:1fr 1fr}}
.hopcol h4{margin:0 0 8px;font-size:.8rem;font-weight:600}
.hopcol .hop{margin:0 0 10px}
.hop-h{font-family:var(--font-mono);font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;
color:var(--ink-mut);margin:0 0 4px}
.hop ul{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:5px}
.hop li a{display:inline-flex;gap:6px;align-items:center;font-family:var(--font-mono);font-size:.68rem;
border:1px solid var(--line);border-radius:5px;padding:2px 7px;text-decoration:none;background:var(--surface)}
.hop li a:hover{border-color:var(--warn)}
.hop li a .st{width:6px;height:6px;border-radius:50%}
.st-open{background:var(--accent)}.st-closed{background:var(--done)}.st-wf{background:var(--wf)}
.legend-row{display:flex;gap:14px;flex-wrap:wrap}
.crumbs{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:10px;
font-family:var(--font-mono);font-size:.7rem;color:var(--ink-mut)}
.crumbs a{text-decoration:none;border:1px solid var(--line);border-radius:5px;padding:2px 7px;background:var(--surface)}
"""

DEPS_BODY = r"""
  <figure class="chart">
    <figcaption class="chart-cap">
      <span class="chart-title" id="graphTitle">Dependency graph</span>
      <span class="legend2 legend-row">
        <span class="lg2"><i class="sw2 sw-created"></i>Open</span>
        <span class="lg2"><i class="sw2 sw-closed"></i>Closed</span>
        <span class="lg2"><i class="sw2 sw-wf"></i>Won&#39;t fix</span>
        <span class="lg2"><i class="sw2 sw-focus"></i>Focus</span>
      </span>
      <a class="lnk" id="openTicket" href="#">open ticket &#8599;</a>
    </figcaption>
    <div class="focus-card" id="focusCard" hidden></div>
    <div id="graphHost"></div>
    <p class="chart-note" id="graphNote">Columns are hops from the focus. Left is the <b>past</b> &mdash; what had to finish first. Right is the <b>future</b> &mdash; what this unlocks. Click any box to re-centre on it; the browser back button retraces your steps.</p>
  </figure>

  <figure class="chart">
    <figcaption class="chart-cap">
      <span class="chart-title">Traversal</span>
    </figcaption>
    <div class="hop-lists">
      <div class="hopcol"><h4>&larr; Blocked by (past)</h4><div id="upList"></div></div>
      <div class="hopcol"><h4>Unblocks (future) &rarr;</h4><div id="downList"></div></div>
    </div>
    <p class="chart-note">Every transitive neighbour, grouped by how many hops away it is. Ignores the hop limit above.</p>
  </figure>"""

DEPS_SCRIPT = r"""
(function(){
  var F=FLOW;
  var STATE={id:"",hops:2,dir:"both",past:"on",epicFilter:"__all",_filled:null,
             data:null,kids:{},pars:{},nodes:{}};

  function qs(){
    var m=/[?&]id=([^&]+)/.exec(location.search);
    return m?decodeURIComponent(m[1]):"";
  }
  function cls(st){return st==="OPEN"?"open":st==="CLOSED"?"closed":"wf";}
  function visible(id){
    return STATE.past==="on"||STATE.nodes[id].status==="OPEN";
  }
  // BFS out from the focus; negative levels upstream (blockers), positive down.
  function walk(from,adj,limit,sign){
    var lvl={},q=[[from,0]],seen={};seen[from]=1;
    while(q.length){
      var cur=q.shift(),id=cur[0],d=cur[1];
      if(limit>0&&d>=limit)continue;
      (adj[id]||[]).forEach(function(nb){
        if(!STATE.nodes[nb]||!visible(nb)||seen[nb])return;
        seen[nb]=1;lvl[nb]=sign*(d+1);q.push([nb,d+1]);
      });
    }
    return lvl;
  }
  // Unbounded reach, for the counts and the hop lists.
  function reach(from,adj){
    var out={},q=[[from,0]],seen={};seen[from]=1;
    while(q.length){
      var cur=q.shift();
      (adj[cur[0]]||[]).forEach(function(nb){
        if(!STATE.nodes[nb]||!visible(nb)||seen[nb])return;
        seen[nb]=1;out[nb]=cur[1]+1;q.push([nb,cur[1]+1]);
      });
    }
    return out;
  }
  function longestUp(id,memo,stack){
    memo=memo||{};stack=stack||{};
    if(memo[id]!=null)return memo[id];
    if(stack[id])return 0;                       // cycle guard
    stack[id]=1;
    var best=0;
    (STATE.pars[id]||[]).forEach(function(p){
      if(STATE.nodes[p]&&visible(p))best=Math.max(best,longestUp(p,memo,stack)+1);
    });
    stack[id]=0;memo[id]=best;return best;
  }

  function setFocus(id,push){
    STATE.id=id;
    if(push)history.pushState({id:id},"",id?("/deps?id="+encodeURIComponent(id)):"/deps");
    render();
  }
  function trunc(s,n){s=s||"";return s.length>n?s.slice(0,n-1)+"…":s;}

  // Two linked pickers: choose an epic to narrow the ticket list, so neither
  // dropdown carries all 572 ids at once. "__all" is the no-epic-filter value;
  // an empty code is the real "unassigned" bucket, so the two stay distinct.
  function epicMeta(){var m={};(STATE.data.epics||[]).forEach(function(e){m[e.id]=e;});return m;}
  function fillEpics(){
    var epics=epicMeta(),counts={};
    Object.keys(STATE.nodes).forEach(function(n){
      var c=STATE.nodes[n].epic||"";counts[c]=(counts[c]||0)+1;});
    var html='<option value="__all">— all epics ('+Object.keys(STATE.nodes).length+') —</option>';
    Object.keys(counts).sort().forEach(function(c){
      var e=epics[c]||{};
      html+='<option value="'+F.esc(c)+'">'+F.esc((e.emoji||"__FAVICON__")+"  __PFX__-"+(c||"?")+" · "+
        (e.title||c||"unassigned")+"  ("+counts[c]+")")+'</option>';
    });
    document.getElementById("dgEpic").innerHTML=html;
  }
  function fillTickets(epic){
    if(STATE._filled===epic)return;         // 572 <option>s: rebuild only on change
    STATE._filled=epic;
    var ids=Object.keys(STATE.nodes).filter(function(n){
      return epic==="__all"||(STATE.nodes[n].epic||"")===epic;
    }).sort(function(a,b){return a.localeCompare(b,undefined,{numeric:true});});
    var html='<option value="">— no focus (overview) —</option>';
    ids.forEach(function(n){
      html+='<option value="'+F.esc(n)+'">'+F.esc(n+" — "+trunc(STATE.nodes[n].title,54))+'</option>';
    });
    document.getElementById("dgFocus").innerHTML=html;
  }
  // Keep both dropdowns consistent with the current focus. A focused ticket
  // pins the epic filter to its own epic; with no focus the user's epic choice
  // stands so they can keep browsing that epic's tickets.
  function syncSelects(){
    var id=STATE.id;
    if(id&&STATE.nodes[id])STATE.epicFilter=STATE.nodes[id].epic||"";
    document.getElementById("dgEpic").value=STATE.epicFilter;
    fillTickets(STATE.epicFilter);
    document.getElementById("dgFocus").value=id&&STATE.nodes[id]?id:"";
  }

  function nodeTip(nid){
    var t=STATE.nodes[nid],ep=epicMeta()[t.epic];
    var pills="";
    if(t.priority)pills+='<span class="tt-pill">P'+F.esc(String(t.priority))+'</span>';
    if(t.risk)pills+='<span class="tt-pill r-'+t.risk.toLowerCase()+'">'+F.esc(t.risk)+'</span>';
    if(t.effort)pills+='<span class="tt-pill">'+F.esc(t.effort)+'</span>';
    return '<div class="tt-head"><span class="tt-id">'+F.esc(nid)+'</span>'+
      '<span class="fc-badge '+cls(t.status)+'">'+F.esc(t.status)+'</span></div>'+
      '<div class="tt-ttl">'+F.esc(t.title||"(no title)")+'</div>'+
      (ep?'<div class="tt-epic">'+(ep.emoji||"")+" __PFX__-"+F.esc(ep.id)+" · "+F.esc(ep.title||"")+'</div>':"")+
      (pills?'<div class="tt-pills">'+pills+'</div>':"")+
      '<div class="tt-row"><span class="k">blocked by</span><b>'+(STATE.pars[nid]||[]).length+'</b></div>'+
      '<div class="tt-row"><span class="k">blocks</span><b>'+(STATE.kids[nid]||[]).length+'</b></div>'+
      '<div class="tt-hint">'+(nid===STATE.id?"the focus":"click to re-centre")+'</div>';
  }

  function drawFocusCard(){
    var card=document.getElementById("focusCard"),id=STATE.id,t=STATE.nodes[id];
    if(!t){card.hidden=true;return;}
    card.hidden=false;
    var ep=(STATE.data.epics||[]).filter(function(e){return e.id===t.epic;})[0];
    card.innerHTML='<span class="fc-id">'+F.esc(id)+'</span>'+
      '<span class="fc-badge '+cls(t.status)+'">'+F.esc(t.status)+'</span>'+
      '<span class="fc-ttl">'+F.esc(t.title||"(no title)")+'</span>'+
      (ep?'<a class="fc-epic" href="/epic/'+F.esc(ep.id)+'">'+(ep.emoji||"")+" __PFX__-"+F.esc(ep.id)+'</a>':"")+
      (t.effort?'<span class="fc-epic">'+F.esc(t.effort)+'</span>':"")+
      '<a class="fc-epic" href="/ticket/'+encodeURIComponent(id)+'">open ticket ↗</a>';
  }

  // No focus: name the whole graph and offer the best places to start.
  function renderOverview(){
    document.getElementById("focusCard").hidden=true;
    document.getElementById("openTicket").hidden=true;
    var ids=Object.keys(STATE.nodes),edges=STATE.data.depGraph.edges.length;
    var open=ids.filter(function(n){return STATE.nodes[n].status==="OPEN";});
    var blocked=open.filter(function(n){
      return (STATE.pars[n]||[]).some(function(p){return STATE.nodes[p].status==="OPEN";});});
    var roots=open.filter(function(n){
      return !(STATE.pars[n]||[]).some(function(p){return STATE.nodes[p].status==="OPEN";});});
    var memo={},deepest=0;
    ids.forEach(function(n){deepest=Math.max(deepest,longestUp(n,memo));});
    var reachN=open.map(function(n){return [n,Object.keys(reach(n,STATE.kids)).length];})
                   .sort(function(a,b){return b[1]-a[1];});
    F.tiles("tiles",[
      {v:ids.length,l:"Tickets in graph",s:"have a dependency edge"},
      {v:edges,l:"Dependencies",s:"blocker → blocked"},
      {v:open.length,l:"Open",s:"still in flight",cls:"is-open"},
      {v:blocked.length,l:"Blocked open",s:"waiting on open work",vcls:blocked.length?"neg":"pos"},
      {v:roots.length,l:"Ready roots",s:"nothing open blocks them",vcls:"pos"},
      {v:deepest,l:"Deepest chain",s:"hops end to end"}
    ],"<b>"+ids.length+"</b> tickets joined by <b>"+edges+"</b> dependency edges. "+
      "Pick a ticket to walk its past and future, or start from one of the blockers below.");
    var cards=reachN.filter(function(r){return r[1]>0;}).slice(0,18).map(function(r){
      var t=STATE.nodes[r[0]];
      return '<a class="ov-card" href="/deps?id='+encodeURIComponent(r[0])+'">'+
        '<span class="ov-id">'+F.esc(r[0])+'</span>'+
        '<span class="ov-ttl">'+F.esc(trunc(t.title,40))+'</span>'+
        '<span class="ov-n">'+r[1]+' ↓</span></a>';
    }).join("");
    document.getElementById("graphHost").innerHTML=
      '<div class="ov"><h4>Start here — open tickets holding up the most work</h4>'+
      '<p>The number is how many tickets sit downstream of it, transitively.</p>'+
      (cards?'<div class="ov-grid">'+cards+'</div>'
            :'<p>No open ticket currently blocks another.</p>')+'</div>';
    document.getElementById("graphTitle").textContent="Dependency graph — no focus";
    document.getElementById("upList").innerHTML=
      '<p class="chart-note" style="margin:0">Pick a focus to see what had to finish first.</p>';
    document.getElementById("downList").innerHTML=
      '<p class="chart-note" style="margin:0">Pick a focus to see what it unlocks.</p>';
  }

  function drawGraph(){
    var host=document.getElementById("graphHost"),id=STATE.id;
    var up=(STATE.dir==="down")?{}:walk(id,STATE.pars,STATE.hops,-1);
    var dn=(STATE.dir==="up")?{}:walk(id,STATE.kids,STATE.hops,1);
    var lvl={};lvl[id]=0;
    Object.keys(up).forEach(function(k){lvl[k]=up[k];});
    Object.keys(dn).forEach(function(k){if(lvl[k]==null)lvl[k]=dn[k];});

    var cols={},k;
    Object.keys(lvl).forEach(function(n){(cols[lvl[n]]=cols[lvl[n]]||[]).push(n);});
    var levels=Object.keys(cols).map(Number).sort(function(a,b){return a-b;});
    if(!levels.length){host.innerHTML='<div class="dgx-empty">Nothing to show.</div>';return;}

    var NW=214,NH=44,GX=64,GY=12,PAD=16,HDR=22;
    var rows=0;levels.forEach(function(l){cols[l].sort();rows=Math.max(rows,cols[l].length);});
    var W=PAD*2+levels.length*(NW+GX)-GX,H=PAD*2+HDR+rows*(NH+GY)-GY;
    var pos={};
    // Centre each column vertically so a lone focus lines up with a tall
    // neighbour column instead of pinning to the top.
    levels.forEach(function(l,ci){
      var off=(rows-cols[l].length)/2;
      cols[l].forEach(function(n,ri){
        pos[n]={x:PAD+ci*(NW+GX),y:PAD+HDR+(off+ri)*(NH+GY)};
      });
    });
    var svg='<svg width="'+W+'" height="'+H+'" viewBox="0 0 '+W+' '+H+
      '" role="img" aria-label="Dependency graph centred on '+F.esc(id)+'">';
    levels.forEach(function(l,ci){
      var cx=PAD+ci*(NW+GX)+NW/2;
      var lab=l===0?"FOCUS":(l<0?l+" hop"+(l<-1?"s":""):"+"+l+" hop"+(l>1?"s":""));
      svg+='<text class="dgx-col" x="'+cx+'" y="'+(PAD+8)+'" text-anchor="middle">'+lab+'</text>';
      if(ci)svg+='<line class="dgx-colline" x1="'+(PAD+ci*(NW+GX)-GX/2)+'" y1="'+(PAD+HDR-6)+
        '" x2="'+(PAD+ci*(NW+GX)-GX/2)+'" y2="'+(H-PAD)+'" stroke-width="1"/>';
    });
    STATE.data.depGraph.edges.forEach(function(e){
      var a=pos[e[0]],b=pos[e[1]];
      if(!a||!b)return;
      var done=STATE.nodes[e[0]].status!=="OPEN";
      var hot=(e[0]===id||e[1]===id);
      var x1=a.x+NW,y1=a.y+NH/2,x2=b.x,y2=b.y+NH/2,mx=(x1+x2)/2;
      if(x2<x1){ // edge runs backwards (cycle or same column): route under
        svg+='<path class="dgx-edge'+(done?" past":"")+(hot?" hot":"")+'" d="M'+(a.x)+','+y1+
          ' C'+(a.x-40)+','+y1+' '+(x2+NW+40)+','+y2+' '+(b.x+NW)+','+y2+'"/>';
      }else{
        svg+='<path class="dgx-edge'+(done?" past":"")+(hot?" hot":"")+'" d="M'+x1+','+y1+
          ' C'+mx+','+y1+' '+mx+','+y2+' '+x2+','+y2+'"/>';
      }
    });
    Object.keys(lvl).forEach(function(n,ix){
      var p=pos[n],t=STATE.nodes[n];
      svg+='<g class="dgx-g" data-id="'+F.esc(n)+'">'+
        '<clipPath id="dgxc'+ix+'"><rect x="'+p.x+'" y="'+p.y+'" width="'+(NW-12)+'" height="'+NH+'"/></clipPath>'+
        '<rect class="dgx-node '+cls(t.status)+(n===id?" focus":"")+'" x="'+p.x+'" y="'+p.y+
        '" width="'+NW+'" height="'+NH+'" rx="7"/>'+
        '<text class="dgx-id" x="'+(p.x+10)+'" y="'+(p.y+17)+'">'+F.esc(n)+'</text>'+
        '<text class="dgx-ttl" clip-path="url(#dgxc'+ix+')" x="'+(p.x+10)+'" y="'+(p.y+33)+'">'+
          F.esc((t.title||"").length>32?t.title.slice(0,31)+"…":(t.title||""))+'</text></g>';
    });
    svg+='</svg>';
    host.innerHTML='<div class="dgx-wrap">'+svg+'<div class="ctip dgxtip" id="dgxTip"></div></div>';
    var wrap=host.querySelector(".dgx-wrap"),tp=host.querySelector("#dgxTip");
    wrap.querySelectorAll(".dgx-g").forEach(function(g){
      var nid=g.getAttribute("data-id");
      g.addEventListener("click",function(){if(nid!==STATE.id)setFocus(nid,true);});
      // Content on enter (once), position on move: snappy, and rich enough to
      // read the ticket without hunting.
      g.addEventListener("mouseenter",function(){tp.innerHTML=nodeTip(nid);tp.style.opacity="1";});
      g.addEventListener("mousemove",function(ev){
        var r=wrap.getBoundingClientRect();
        var x=ev.clientX-r.left+wrap.scrollLeft,y=ev.clientY-r.top+wrap.scrollTop;
        // flip to the left of the cursor near the right edge so it never clips
        tp.classList.toggle("flip",x>wrap.clientWidth-300);
        tp.style.left=x+"px";tp.style.top=y+"px";
      });
      g.addEventListener("mouseleave",function(){tp.style.opacity="0";});
    });
    var shown=Object.keys(lvl).length;
    document.getElementById("graphTitle").textContent=
      "Dependency graph — "+shown+" ticket"+(shown===1?"":"s")+" within "+
      (STATE.hops>0?STATE.hops+" hop"+(STATE.hops>1?"s":""):"any distance")+" of "+STATE.id;
  }

  function hopList(hostId,map,empty){
    var byHop={};
    Object.keys(map).forEach(function(n){(byHop[map[n]]=byHop[map[n]]||[]).push(n);});
    var hops=Object.keys(byHop).map(Number).sort(function(a,b){return a-b;});
    if(!hops.length){document.getElementById(hostId).innerHTML=
      '<p class="chart-note" style="margin:0">'+empty+'</p>';return;}
    document.getElementById(hostId).innerHTML=hops.map(function(h){
      return '<div class="hop"><p class="hop-h">'+h+' hop'+(h>1?"s":"")+' · '+byHop[h].length+'</p><ul>'+
        byHop[h].sort().map(function(n){
          var t=STATE.nodes[n];
          return '<li><a href="/deps?id='+encodeURIComponent(n)+'" title="'+F.esc(t.title||"")+'">'+
            '<i class="st st-'+cls(t.status)+'"></i>'+F.esc(n)+'</a></li>';
        }).join("")+'</ul></div>';
    }).join("");
  }

  function render(){
    var id=STATE.id,t=STATE.nodes[id];
    syncSelects();
    if(!id){renderOverview();return;}
    if(!t){
      document.getElementById("focusCard").hidden=true;
      document.getElementById("openTicket").hidden=true;
      document.getElementById("graphHost").innerHTML=
        '<div class="dgx-empty">No ticket <b>'+F.esc(id)+'</b> in the dependency graph. '+
        'Only tickets with a <code>depends_on</code> or <code>blocks</code> field appear here.</div>';
      document.getElementById("tiles").innerHTML="";
      document.getElementById("upList").innerHTML="";
      document.getElementById("downList").innerHTML="";
      return;
    }
    document.getElementById("openTicket").hidden=false;
    var up=reach(id,STATE.pars),dn=reach(id,STATE.kids);
    var nUp=Object.keys(up).length,nDn=Object.keys(dn).length;
    var direct=(STATE.pars[id]||[]).filter(visible),dkids=(STATE.kids[id]||[]).filter(visible);
    var openBlockers=(STATE.pars[id]||[]).filter(function(p){return STATE.nodes[p].status==="OPEN";});
    F.tiles("tiles",[
      {v:t.status,l:"Status",s:t.epic?("epic __PFX__-"+t.epic):"—",
       cls:t.status==="OPEN"?"is-open":t.status==="CLOSED"?"is-done":"is-wf",
       href:"/ticket/"+encodeURIComponent(id)},
      {v:openBlockers.length,l:"Open blockers",s:openBlockers.length?"still in the way":"nothing in the way",
       vcls:openBlockers.length?"neg":"pos"},
      {v:direct.length,l:"Blocked by",s:"direct, 1 hop"},
      {v:dkids.length,l:"Blocks",s:"direct, 1 hop"},
      {v:nUp,l:"Upstream total",s:"transitive past"},
      {v:nDn,l:"Downstream total",s:"transitive future"},
      {v:longestUp(id),l:"Longest chain",s:"hops behind it"}
    ],"<b>"+F.esc(id)+"</b> — "+F.esc(t.title||"")+". "+
      (openBlockers.length?("Blocked by <b>"+openBlockers.length+"</b> open ticket"+(openBlockers.length>1?"s":"")+"; ")
                          :"Nothing open blocks it; ")+
      "it unlocks <b>"+nDn+"</b> downstream.");
    document.getElementById("openTicket").href="/ticket/"+encodeURIComponent(id);
    document.getElementById("dgFocus").value=id;
    drawFocusCard();
    drawGraph();
    hopList("upList",up,"Nothing blocks this ticket — it is a root.");
    hopList("downList",dn,"This ticket blocks nothing downstream — it is a leaf.");
  }

  function index(d){
    STATE.nodes=d.depGraph.nodes;STATE.kids={};STATE.pars={};
    d.depGraph.edges.forEach(function(e){
      (STATE.kids[e[0]]=STATE.kids[e[0]]||[]).push(e[1]);
      (STATE.pars[e[1]]=STATE.pars[e[1]]||[]).push(e[0]);
    });
  }
  F.wire("hopsSel",STATE,"hops",render,Number);
  F.wire("dirSel",STATE,"dir",render);
  F.wire("pastSel",STATE,"past",render);
  // Epic picker only narrows the ticket list; it does not move the graph. The
  // ticket picker sets the focus.
  document.getElementById("dgEpic").addEventListener("change",function(){
    STATE.epicFilter=this.value;
    fillTickets(this.value);
    var id=STATE.id,keep=id&&STATE.nodes[id]&&
      (this.value==="__all"||(STATE.nodes[id].epic||"")===this.value);
    document.getElementById("dgFocus").value=keep?id:"";
  });
  document.getElementById("dgFocus").addEventListener("change",function(){
    var v=this.value;
    if(!v||STATE.nodes[v])setFocus(v,true); else render();
  });
  document.getElementById("clearFocus").addEventListener("click",function(){
    STATE.epicFilter="__all";setFocus("",true);
  });
  window.addEventListener("popstate",function(){
    STATE.id=qs();render();
  });

  F.boot(function(d){
    STATE.data=d;index(d);fillEpics();
    STATE.id=qs();          // no ?id= means the overview, not a guessed focus
    render();
  });
})();
"""

DEPS_HTML = _flow_page(
    "Dependencies", "🔗", "Dependency explorer", "Dependencies",
    r"""
      <span class="lab">epic</span>
      <select class="dgsearch" id="dgEpic" aria-label="Filter tickets by epic"></select>
      <span class="lab">ticket</span>
      <select class="dgsearch" id="dgFocus" aria-label="Focus ticket"></select>
      <button type="button" class="lnk" id="clearFocus" title="Show the whole graph at a glance">clear focus</button>
      <span class="lab">hops</span>
      <div class="controls" role="group" aria-label="Hops from focus" id="hopsSel">
        <button type="button" data-v="1" aria-pressed="false">1</button>
        <button type="button" data-v="2" aria-pressed="true">2</button>
        <button type="button" data-v="3" aria-pressed="false">3</button>
        <button type="button" data-v="0" aria-pressed="false">all</button>
      </div>
      <span class="lab">show</span>
      <div class="controls" role="group" aria-label="Direction" id="dirSel">
        <button type="button" data-v="both" aria-pressed="true">both</button>
        <button type="button" data-v="up" aria-pressed="false">past</button>
        <button type="button" data-v="down" aria-pressed="false">future</button>
      </div>
      <span class="lab">closed</span>
      <div class="controls" role="group" aria-label="Include closed tickets" id="pastSel">
        <button type="button" data-v="on" aria-pressed="true">show</button>
        <button type="button" data-v="off" aria-pressed="false">hide</button>
      </div>""",
    DEPS_BODY, DEPS_SCRIPT, extra_css=DEPS_CSS)


PRIORITIZE_CSS = r"""
.pr-duo{display:grid;gap:16px}
@media(min-width:960px){.pr-duo{grid-template-columns:minmax(300px,.85fr) 1.15fr}}
.pr-in{font-family:var(--font-mono);font-size:.74rem;color:var(--ink);background:var(--surface);
border:1px solid var(--line);border-radius:100px;padding:6px 13px;min-width:170px}
.pr-in:focus{outline:2px solid var(--accent);outline-offset:1px}
.mx-cell.sel{outline:2.5px solid var(--warn);outline-offset:1px}
.mx-cell.dim{opacity:.32}
.pr-scatter{position:relative}
.pt{stroke:var(--surface);stroke-width:1;cursor:pointer}
.pt.r-high{fill:#c23b3b}.pt.r-medium{fill:var(--warn)}.pt.r-low{fill:var(--done)}
@media (prefers-color-scheme:dark){.pt.r-high{fill:#e06c6c}}
.pt:hover{stroke:var(--ink);stroke-width:1.6}
.sw-high{background:#c23b3b}.sw-med{background:var(--warn)}.sw-low{background:var(--done)}
@media (prefers-color-scheme:dark){.sw-high{background:#e06c6c}}
.quad{fill:var(--ink-mut);font-family:var(--font-mono);font-size:9px;opacity:.55;letter-spacing:.06em}
.qline{stroke:var(--ink-mut);stroke-dasharray:3 3;opacity:.45}
.pr-tbl td .tid{font-family:var(--font-mono);font-weight:600;color:var(--accent-ink);text-decoration:none}
.pr-tbl td .tid:hover{text-decoration:underline}
.pr-tbl td.ttl{text-align:left;font-family:var(--font-sans);color:var(--ink-2);max-width:340px;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pr-tbl td.rank{color:var(--ink-mut)}
.pr-tbl .pill{font-family:var(--font-mono);font-size:.6rem;border:1px solid var(--line);
border-radius:4px;padding:1px 5px;color:var(--ink-mut);white-space:nowrap}
.pr-tbl .pill.r-high{color:#c23b3b;border-color:#c23b3b}
.pr-tbl .pill.r-medium{color:var(--warn);border-color:var(--warn)}
.pr-tbl .pill.r-low{color:var(--done);border-color:var(--done)}
.pr-tbl .pill.blk{color:var(--warn);border-color:var(--warn)}
.pr-tbl .dep{text-decoration:none;color:var(--ink-mut);opacity:.5}
.pr-tbl .dep:hover{opacity:1;color:var(--accent-ink)}
.pr-tbl tr.hi td{background:var(--accent-soft)}
.pr-empty{color:var(--ink-mut);font-size:.85rem;padding:34px 12px;text-align:center}
"""

PRIORITIZE_BODY = r"""
  <div class="pr-duo">
    <figure class="chart fill">
      <figcaption class="chart-cap">
        <span class="chart-title">Priority &times; risk</span>
        <span class="legend2"><span class="lg2 mx-legend"><span class="lm">darker</span><span class="dm">brighter</span> = more tickets</span></span>
      </figcaption>
      <div id="chartMatrix"></div>
      <p class="chart-note">Click a cell to filter to it; click it again to clear. Counts respect the other filters.</p>
    </figure>
    <figure class="chart fill">
      <figcaption class="chart-cap">
        <span class="chart-title">Impact vs effort</span>
        <span class="legend2">
          <span class="lg2"><i class="sw2 sw-high"></i>High</span>
          <span class="lg2"><i class="sw2 sw-med"></i>Medium</span>
          <span class="lg2"><i class="sw2 sw-low"></i>Low</span>
        </span>
      </figcaption>
      <div class="chart-plot pr-scatter" id="chartScatter"></div>
      <p class="chart-note">Each dot is an open ticket: effort across, tickets it unblocks up, size by priority, colour by risk. Dashed lines split the quadrants at the median effort. Click a dot to open the ticket.</p>
    </figure>
  </div>

  <figure class="chart">
    <figcaption class="chart-cap">
      <span class="chart-title" id="tblTitle">Ranked backlog</span>
      <span class="legend2"><span class="lg2" id="sortLab"></span></span>
      <button type="button" class="lnk" id="copyBtn" title="Copy the current view's ticket ids, one per line">copy ids</button>
      <button type="button" class="lnk" id="csvBtn">download CSV</button>
    </figcaption>
    <div id="tableHost"></div>
    <p class="chart-note" id="scoreNote"></p>
  </figure>"""

PRIORITIZE_SCRIPT = r"""
(function(){
  var F=FLOW;
  var STATE={p:"",r:"",state:"",sort:"score",q:"",data:null,tix:[],deps:{}};
  var RISKW={HIGH:24,MEDIUM:12,LOW:4};
  var RISKORD={HIGH:0,MEDIUM:1,LOW:2};

  // Transparent, on purpose: the note under the table spells this out.
  function score(t){
    var s=(5-(parseInt(t.priority,10)||4))*12;
    s+=RISKW[t.risk]||0;
    s+=Math.min(t.unblocks||0,10)*5;
    s-=Math.min(F.days(t.effortH||0),10)*2;
    if(t.blocked)s-=25;
    return Math.round(s*10)/10;
  }
  function days(h){return F.days(h||0);}

  function qs(k){var m=new RegExp("[?&]"+k+"=([^&]+)").exec(location.search);
    return m?decodeURIComponent(m[1]):"";}
  function syncUrl(){
    var p=[];
    if(STATE.p)p.push("p="+STATE.p);
    if(STATE.r)p.push("r="+STATE.r);
    history.replaceState(null,"","/prioritize"+(p.length?"?"+p.join("&"):""));
  }

  function passes(t,ignore){
    if(ignore!=="cell"){
      if(STATE.p&&String(t.priority)!==STATE.p)return false;
      if(STATE.r&&t.risk!==STATE.r)return false;
    }
    if(STATE.state==="ready"&&t.blocked)return false;
    if(STATE.state==="blocked"&&!t.blocked)return false;
    if(STATE.q){
      var q=STATE.q.toLowerCase();
      if(t.id.toLowerCase().indexOf(q)===-1&&(t.title||"").toLowerCase().indexOf(q)===-1)return false;
    }
    return true;
  }
  function filtered(){return STATE.tix.filter(function(t){return passes(t);});}

  function sorted(list){
    var s=STATE.sort,a=list.slice();
    if(s==="score")a.sort(function(x,y){return score(y)-score(x)||x.id.localeCompare(y.id);});
    else if(s==="priority")a.sort(function(x,y){return (x.priority-y.priority)||(RISKORD[x.risk]-RISKORD[y.risk]);});
    else if(s==="risk")a.sort(function(x,y){return (RISKORD[x.risk]-RISKORD[y.risk])||(x.priority-y.priority);});
    else if(s==="effort")a.sort(function(x,y){return (x.effortH==null?1e9:x.effortH)-(y.effortH==null?1e9:y.effortH);});
    else if(s==="unblocks")a.sort(function(x,y){return (y.unblocks||0)-(x.unblocks||0);});
    else if(s==="age")a.sort(function(x,y){return (x.created||"9999").localeCompare(y.created||"9999");});
    return a;
  }

  function renderTiles(list){
    var effH=0,blocked=0,unb=0,fire=0,quick=0;
    list.forEach(function(t){
      effH+=t.effortH||0;
      if(t.blocked)blocked++; else if((t.effortH!=null)&&t.effortH<=4)quick++;
      unb+=t.unblocks||0;
      if(String(t.priority)==="1"&&t.risk==="HIGH")fire++;
    });
    var all=STATE.tix.length;
    F.tiles("tiles",[
      {v:list.length,l:"Matching tickets",s:list.length===all?"no filter":"of "+all+" open",cls:"is-open"},
      {v:days(effH).toLocaleString()+"d",l:"Effort",s:"1d = 8h"},
      {v:fire,l:"P1 × HIGH",s:"the fire drawer",vcls:fire?"neg":"pos"},
      {v:blocked,l:"Blocked",s:"waiting on open work",vcls:blocked?"neg":""},
      {v:quick,l:"Quick wins",s:"ready, ≤ 4h",vcls:quick?"pos":""},
      {v:unb,l:"Tickets unblocked",s:"summed across matches"}
    ],"<b>"+list.length+"</b> open ticket"+(list.length===1?"":"s")+" match"+(list.length===1?"es":"")+
      " — <b>"+days(effH).toLocaleString()+" days</b> of estimated work, <b>"+blocked+"</b> blocked.");
  }

  function drawMatrix(){
    var host=document.getElementById("chartMatrix");
    var risks=["HIGH","MEDIUM","LOW"],cells={},maxC=0,prios={};
    // Cell counts ignore the cell filter itself, so the grid never collapses
    // to a single lit square once you pick one.
    STATE.tix.filter(function(t){return passes(t,"cell");}).forEach(function(t){
      if(!t.risk||!t.priority)return;
      prios[t.priority]=1;
      var k=t.priority+"|"+t.risk,c=cells[k]||(cells[k]={n:0,h:0});
      c.n++;c.h+=(t.effortH||0);
      if(c.n>maxC)maxC=c.n;
    });
    var rows=Object.keys(prios).sort(function(a,b){return (+a)-(+b);});
    if(!rows.length){host.innerHTML='<div class="pr-empty">Nothing matches.</div>';return;}
    var sel=STATE.p&&STATE.r;
    var out='<div class="matrix" style="grid-template-rows:auto repeat('+rows.length+',1fr)">'+
      '<span class="mx-lab"></span>'+
      risks.map(function(r){return '<span class="mx-lab">'+r.toLowerCase()+'</span>';}).join("");
    rows.forEach(function(p){
      out+='<span class="mx-lab row">P'+p+'</span>';
      risks.forEach(function(r){
        var c=cells[p+"|"+r]||{n:0,h:0};
        if(!c.n){out+='<button type="button" class="mx-cell empty" tabindex="-1"><span class="n">·</span></button>';return;}
        var isSel=(STATE.p===p&&STATE.r===r);
        var step=Math.max(1,Math.ceil(c.n/maxC*4));
        out+='<button type="button" class="mx-cell s'+step+(isSel?" sel":(sel?" dim":""))+
          '" data-p="'+p+'" data-r="'+r+'" title="P'+p+' × '+r+' — '+c.n+' open, '+days(c.h)+'d">'+
          '<span class="n">'+c.n+'</span><span class="d">'+days(c.h)+'d</span></button>';
      });
    });
    host.innerHTML=out+'</div>';
    host.querySelectorAll(".mx-cell[data-p]").forEach(function(b){
      b.addEventListener("click",function(){
        var p=b.getAttribute("data-p"),r=b.getAttribute("data-r");
        if(STATE.p===p&&STATE.r===r){STATE.p="";STATE.r="";}else{STATE.p=p;STATE.r=r;}
        syncUrl();render();
      });
    });
  }

  function median(a){
    if(!a.length)return 0;
    var s=a.slice().sort(function(x,y){return x-y;}),m=s.length>>1;
    return s.length%2?s[m]:(s[m-1]+s[m])/2;
  }
  function drawScatter(list){
    var host=document.getElementById("chartScatter");
    if(!list.length){host.innerHTML='<div class="pr-empty">Nothing matches.</div>';return;}
    var W=640,H=330,padL=44,padR=18,padT=16,padB=36;
    var maxX=Math.max.apply(null,list.map(function(t){return days(t.effortH);}).concat([1]));
    var maxY=Math.max.apply(null,list.map(function(t){return t.unblocks||0;}).concat([1]));
    var niceX=Math.max(1,Math.ceil(maxX)),niceY=Math.max(1,maxY);
    var plotW=W-padL-padR,plotH=H-padT-padB;
    function X(v){return padL+plotW*(v/niceX);}
    function Y(v){return padT+plotH*(1-v/niceY);}
    var medX=median(list.map(function(t){return days(t.effortH);}));
    var svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Open tickets by effort and tickets unblocked">';
    var k,yy,xx;
    for(k=0;k<=4;k++){
      yy=padT+plotH*(1-k/4);
      svg+='<line class="grid-line" x1="'+padL+'" y1="'+yy+'" x2="'+(W-padR)+'" y2="'+yy+'" stroke-width="1"/>';
      svg+='<text class="axis-txt" x="'+(padL-6)+'" y="'+(yy+3)+'" text-anchor="end" font-size="10">'+
        Math.round(niceY*k/4)+'</text>';
      xx=padL+plotW*(k/4);
      svg+='<text class="axis-txt" x="'+xx+'" y="'+(H-16)+'" text-anchor="middle" font-size="10">'+
        (Math.round(niceX*k/4*10)/10)+'d</text>';
    }
    svg+='<text class="axis-txt" x="'+(padL+plotW/2)+'" y="'+(H-3)+'" text-anchor="middle" font-size="9">effort (days)</text>';
    var medYv=Math.max(1,median(list.map(function(t){return t.unblocks||0;})));
    svg+='<line class="qline" x1="'+X(medX)+'" y1="'+padT+'" x2="'+X(medX)+'" y2="'+(H-padB)+'" stroke-width="1"/>';
    svg+='<line class="qline" x1="'+padL+'" y1="'+Y(medYv)+'" x2="'+(W-padR)+'" y2="'+Y(medYv)+'" stroke-width="1"/>';
    svg+='<text class="quad" x="'+(padL+6)+'" y="'+(padT+12)+'">QUICK WINS</text>';
    svg+='<text class="quad" x="'+(W-padR-6)+'" y="'+(padT+12)+'" text-anchor="end">BIG BETS</text>';
    svg+='<text class="quad" x="'+(padL+6)+'" y="'+(H-padB-6)+'">FILL-INS</text>';
    svg+='<text class="quad" x="'+(W-padR-6)+'" y="'+(H-padB-6)+'" text-anchor="end">SLOG</text>';
    // deterministic jitter so co-located dots stay countable
    function jit(id,m){var h=0,i;for(i=0;i<id.length;i++)h=(h*31+id.charCodeAt(i))|0;return ((h%100)/100-0.5)*m;}
    sorted(list).slice().reverse().forEach(function(t){
      var r=[0,7,5.5,4.5,3.5][parseInt(t.priority,10)]||3.5;
      var cx=X(days(t.effortH))+jit(t.id,7),cy=Y(t.unblocks||0)+jit(t.id+"y",7);
      svg+='<a href="/ticket/'+encodeURIComponent(t.id)+'"><circle class="pt r-'+
        (t.risk||"low").toLowerCase()+'" cx="'+cx.toFixed(1)+'" cy="'+cy.toFixed(1)+'" r="'+r+
        '" data-id="'+F.esc(t.id)+'" opacity="'+(t.blocked?0.45:0.9)+'"><title>'+
        F.esc(t.id+" — "+(t.title||""))+"\nP"+t.priority+" · "+t.risk+" · "+days(t.effortH)+
        "d · unblocks "+(t.unblocks||0)+(t.blocked?" · blocked":"")+'</title></circle></a>';
    });
    svg+='</svg>';
    host.innerHTML=svg;
  }

  var COLS=["#","ticket","title","P","risk","effort","unblocks","state","score"];
  function drawTable(list){
    var host=document.getElementById("tableHost");
    document.getElementById("tblTitle").textContent="Ranked backlog — "+list.length+" ticket"+(list.length===1?"":"s");
    document.getElementById("sortLab").textContent="sorted by "+STATE.sort;
    if(!list.length){host.innerHTML='<div class="pr-empty">No open ticket matches these filters.</div>';return;}
    var rows=list.map(function(t,i){
      return '<tr'+(String(t.priority)==="1"&&t.risk==="HIGH"?' class="hi"':'')+'>'+
        '<td class="rank">'+(i+1)+'</td>'+
        '<td><a class="tid" href="/ticket/'+encodeURIComponent(t.id)+'">'+F.esc(t.id)+'</a>'+
          (STATE.deps[t.id]?' <a class="dep" href="/deps?id='+encodeURIComponent(t.id)+
            '" title="dependency graph">&#9741;</a>':'')+'</td>'+
        '<td class="ttl" title="'+F.esc(t.title||"")+'">'+F.esc(t.title||"")+'</td>'+
        '<td>P'+F.esc(String(t.priority))+'</td>'+
        '<td><span class="pill r-'+(t.risk||"").toLowerCase()+'">'+F.esc(t.risk||"—")+'</span></td>'+
        '<td>'+(t.effortH==null?"—":days(t.effortH)+"d")+'</td>'+
        '<td>'+(t.unblocks||0)+'</td>'+
        '<td>'+(t.blocked?'<span class="pill blk">blocked</span>':'<span class="pill">ready</span>')+'</td>'+
        '<td>'+score(t)+'</td></tr>';
    }).join("");
    host.innerHTML='<div class="ctable tall pr-tbl"><table><thead><tr>'+
      COLS.map(function(c){return "<th>"+c+"</th>";}).join("")+
      '</tr></thead><tbody>'+rows+'</tbody></table></div>';
  }

  function downloadCSV(list){
    var head="rank,id,title,priority,risk,effort_days,unblocks,blocked,score";
    var body=list.map(function(t,i){
      return [i+1,t.id,'"'+String(t.title||"").replace(/"/g,'""')+'"',t.priority,t.risk,
              (t.effortH==null?"":days(t.effortH)),(t.unblocks||0),(t.blocked?"yes":"no"),score(t)].join(",");
    }).join("\n");
    var url=URL.createObjectURL(new Blob([head+"\n"+body+"\n"],{type:"text/csv"}));
    var a=document.createElement("a");
    a.href=url;a.download="prioritize-"+new Date().toISOString().slice(0,10)+".csv";
    a.click();URL.revokeObjectURL(url);
  }

  function copyIds(list){
    var text=list.map(function(t){return t.slug||t.id;}).join("\n");
    var btn=document.getElementById("copyBtn"),done=function(ok){
      btn.textContent=ok?"copied "+list.length:"copy failed";
      setTimeout(function(){btn.textContent="copy ids";},1400);};
    if(navigator.clipboard&&navigator.clipboard.writeText)
      navigator.clipboard.writeText(text).then(function(){done(true);},function(){done(false);});
    else{  // http/older-browser fallback
      var ta=document.createElement("textarea");ta.value=text;
      ta.style.position="fixed";ta.style.opacity="0";document.body.appendChild(ta);
      ta.select();try{done(document.execCommand("copy"));}catch(e){done(false);}
      document.body.removeChild(ta);
    }
  }

  function render(){
    var list=filtered(),ranked=sorted(list);
    renderTiles(list);
    drawMatrix();
    drawScatter(list);
    drawTable(ranked);
    document.getElementById("scoreNote").innerHTML=
      "P1×HIGH rows are tinted. <b>score</b> = (5−priority)×12 + risk(high 24, medium 12, low 4) "+
      "+ min(unblocks,10)×5 − min(effort days,10)×2 − 25 if blocked. It is a starting order, not an oracle.";
    // reflect cell filter in the priority/risk button groups
    [["pSel",STATE.p],["rSel",STATE.r]].forEach(function(pair){
      var host=document.getElementById(pair[0]);
      host.querySelectorAll("button").forEach(function(b){
        b.setAttribute("aria-pressed",String(b.dataset.v===pair[1]));});
    });
  }

  function wireCell(id,key){
    var host=document.getElementById(id);
    host.addEventListener("click",function(ev){
      var b=ev.target.closest("button[data-v]");if(!b)return;
      STATE[key]=b.dataset.v;
      syncUrl();render();
    });
  }
  wireCell("pSel","p");
  wireCell("rSel","r");
  F.wire("stSel",STATE,"state",render);
  F.wire("sortSel",STATE,"sort",render);
  document.getElementById("prQ").addEventListener("input",function(){
    STATE.q=this.value.trim();render();});
  document.getElementById("prClear").addEventListener("click",function(){
    STATE.p="";STATE.r="";STATE.state="";STATE.q="";STATE.sort="score";
    document.getElementById("prQ").value="";
    ["stSel","sortSel"].forEach(function(g){
      document.getElementById(g).querySelectorAll("button").forEach(function(b){
        b.setAttribute("aria-pressed",String(b.dataset.v===(g==="stSel"?"":"score")));});
    });
    syncUrl();render();
  });
  document.getElementById("csvBtn").addEventListener("click",function(){
    downloadCSV(sorted(filtered()));});
  document.getElementById("copyBtn").addEventListener("click",function(){
    copyIds(sorted(filtered()));});

  F.boot(function(d){
    STATE.data=d;
    STATE.deps=(d.depGraph&&d.depGraph.nodes)||{};
    STATE.tix=[];
    d.epics.forEach(function(e){(e.openTickets||[]).forEach(function(t){STATE.tix.push(t);});});
    STATE.p=qs("p");STATE.r=qs("r");
    render();
  });
})();
"""

PRIORITIZE_HTML = _flow_page(
    "Prioritize", "🎯", "Planning", "Prioritisation matrix",
    r"""
      <input class="pr-in" id="prQ" type="search" placeholder="search id or title" aria-label="Search"/>
      <span class="lab">priority</span>
      <div class="controls" role="group" aria-label="Priority" id="pSel">
        <button type="button" data-v="" aria-pressed="true">all</button>
        <button type="button" data-v="1" aria-pressed="false">1</button>
        <button type="button" data-v="2" aria-pressed="false">2</button>
        <button type="button" data-v="3" aria-pressed="false">3</button>
        <button type="button" data-v="4" aria-pressed="false">4</button>
      </div>
      <span class="lab">risk</span>
      <div class="controls" role="group" aria-label="Risk" id="rSel">
        <button type="button" data-v="" aria-pressed="true">all</button>
        <button type="button" data-v="HIGH" aria-pressed="false">high</button>
        <button type="button" data-v="MEDIUM" aria-pressed="false">med</button>
        <button type="button" data-v="LOW" aria-pressed="false">low</button>
      </div>
      <span class="lab">state</span>
      <div class="controls" role="group" aria-label="State" id="stSel">
        <button type="button" data-v="" aria-pressed="true">all</button>
        <button type="button" data-v="ready" aria-pressed="false">ready</button>
        <button type="button" data-v="blocked" aria-pressed="false">blocked</button>
      </div>
      <span class="lab">sort</span>
      <div class="controls" role="group" aria-label="Sort" id="sortSel">
        <button type="button" data-v="score" aria-pressed="true">score</button>
        <button type="button" data-v="priority" aria-pressed="false">priority</button>
        <button type="button" data-v="risk" aria-pressed="false">risk</button>
        <button type="button" data-v="effort" aria-pressed="false">effort</button>
        <button type="button" data-v="unblocks" aria-pressed="false">unblocks</button>
        <button type="button" data-v="age" aria-pressed="false">age</button>
      </div>
      <button type="button" class="lnk" id="prClear">clear filters</button>""",
    PRIORITIZE_BODY, PRIORITIZE_SCRIPT, extra_css=PRIORITIZE_CSS)



# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quieter console
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        if isinstance(body, str) and ctype.startswith("text/html"):
            body = _transform_html(body)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location, cookie=None):
        self.send_response(302)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie + "; Path=/; Max-Age=31536000")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _activate_from_request(self):
        """Pick the interface for this request from the `ifc` cookie (default:
        the first registered)."""
        refresh_registry()
        if len(INTERFACES) <= 1:
            activate(INTERFACES[0])
            return
        slug = None
        m = re.search(r"(?:^|;\s*)ifc=([^;]+)", self.headers.get("Cookie", "") or "")
        if m:
            slug = urllib.parse.unquote(m.group(1))
        activate(_IFACE_BY_SLUG.get(slug, INTERFACES[0]))

    def do_GET(self):
        with _LOCK:
            self._activate_from_request()
            self._do_get()

    def _do_get(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(DASHBOARD_HTML)
            return
        if path in ("/throughput", "/throughput/"):
            self._send(THROUGHPUT_HTML)
            return
        if path in ("/burnup", "/burnup/"):
            self._send(BURNUP_HTML)
            return
        if path in ("/deps", "/deps/"):   # ?id= is read client-side
            self._send(DEPS_HTML)
            return
        if path in ("/prioritize", "/prioritize/"):   # ?p=&r= read client-side
            self._send(PRIORITIZE_HTML)
            return
        if path == "/api/pocket":
            # The bar's created/closed badge, on pages that never load the board.
            try:
                data, _ = scan()
                self._send(json.dumps(data["pocket"]),
                           "application/json; charset=utf-8")
            except Exception as exc:
                self._send(json.dumps({"error": str(exc)}),
                           "application/json; charset=utf-8", code=500)
            return
        if path == "/api/ids":
            # Just the id -> status/title index: the notes pop-out wants to chip
            # the ticket ids in a captured item, not the whole board.
            try:
                data, _ = scan()
                self._send(json.dumps(data["index"]),
                           "application/json; charset=utf-8")
            except Exception as exc:
                self._send(json.dumps({"error": str(exc)}),
                           "application/json; charset=utf-8", code=500)
            return
        if path == "/api/data":
            try:
                data, _ = scan()
                self._send(json.dumps(data), "application/json; charset=utf-8")
            except Exception as exc:  # surface scan errors to the button
                self._send(json.dumps({"error": str(exc)}),
                           "application/json; charset=utf-8", code=500)
            return
        if path == "/api/note":
            import urllib.parse
            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            which = (qs.get("which") or [""])[0]
            if which not in NOTE_FILES:
                self._send(json.dumps({"ok": False, "error": "unknown note " + which}),
                           "application/json; charset=utf-8", code=404)
                return
            self._send(json.dumps({"ok": True, "which": which,
                                   "content": load_note(which)}),
                       "application/json; charset=utf-8")
            return
        if path.startswith("/ticket/"):
            import urllib.parse
            tid = urllib.parse.unquote(path[len("/ticket/"):])
            data, id_index = scan()
            target = id_index.get(tid)
            if not target:
                # Id prefixes are unique across a hub, so a ticket URL should
                # work whichever interface happens to be active: hand the
                # request to the interface that owns the prefix.
                owner = _owner_interface(tid)
                if owner:
                    self._redirect(self.path,
                                   cookie="ifc=" + urllib.parse.quote(owner.slug))
                    return
                self._send("<h1>404</h1><p>No ticket " + html.escape(tid) + "</p>", code=404)
                return
            self._send(render_ticket_page(target, set(id_index),
                                          {e["id"] for e in data["epics"]},
                                          adr_index()[1],
                                          pinned=tid in load_pins(),
                                          id_index=id_index,
                                          dep_graph=data.get("depGraph")))
            return
        if path in ("/adrs", "/adrs/", "/adr"):
            recs, by_num = adr_index()
            self._send(render_series_page(doc_series()[0], recs, by_num))
            return
        if path.startswith("/docs/"):
            # Index page for any document series (the generic /adrs).
            want = path[len("/docs/"):].strip("/").upper()
            for series in doc_series():
                if series["prefix"] == want:
                    recs, by_num = series_index(
                        series["prefix"],
                        os.path.join(REPO_ROOT, series["dir"]))
                    self._send(render_series_page(series, recs, by_num))
                    return
            self._send("<h1>404</h1><p>No document series "
                       + html.escape(want) + "</p>", code=404)
            return
        if path == "/filter":
            import urllib.parse
            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            data, _ = scan()
            self._send(render_filter_page(data, qs))
            return
        if path.startswith("/epic/"):
            import urllib.parse
            code = urllib.parse.unquote(path[len("/epic/"):]).upper()
            data, _ = scan()
            match = [e for e in data["epics"] if e["id"] == code]
            if not match:
                self._send("<h1>404</h1><p>No epic " + html.escape(code) + "</p>", code=404)
                return
            self._send(render_epic_page(match[0]))
            return
        if path == "/favicon.ico":
            self._send(b"", "image/x-icon", code=204)
            return
        if path.startswith("/doc/"):
            import urllib.parse
            rel = urllib.parse.unquote(path[len("/doc/"):])
            full = os.path.realpath(os.path.join(REPO_ROOT, rel))
            if (not full.startswith(os.path.realpath(REPO_ROOT) + os.sep)
                    or not full.endswith(".md") or not os.path.isfile(full)):
                self._send("<h1>404</h1><p>No doc " + html.escape(rel) + "</p>", code=404)
                return
            data, id_index = scan()
            rel_key = os.path.relpath(full, REPO_ROOT).replace(os.sep, "/")
            self._send(render_doc_page(full, set(id_index),
                                       {e["id"] for e in data["epics"]},
                                       adr_index()[1],
                                       pinned=rel_key in load_pins()))
            return
        if path.endswith(".md"):
            # Stray raw-markdown URL (old bookmark or a relative link that
            # escaped rewriting): route to the canonical page for it.
            import urllib.parse
            rel = urllib.parse.unquote(path).lstrip("/")
            base = os.path.basename(rel)
            m = _MD_EPIC_FILE_RE.match(base)
            if m:
                data, _ = scan()
                code = "E" + m.group(1)
                if any(e["id"] == code for e in data["epics"]):
                    self._redirect("/epic/" + code)
                    return
            m = _MD_TICKET_FILE_RE.match(base)
            if m:
                _, id_index = scan()
                if m.group(1) in id_index:
                    self._redirect("/ticket/" + m.group(1))
                    return
            full = os.path.realpath(os.path.join(REPO_ROOT, rel))
            if (full.startswith(os.path.realpath(REPO_ROOT) + os.sep)
                    and os.path.isfile(full)):
                self._redirect("/doc/" + os.path.relpath(full, REPO_ROOT))
                return
            hits = (glob.glob(os.path.join(TICKETS_DIR, "**", base), recursive=True)
                    or glob.glob(os.path.join(REPO_ROOT, "docs", "**", base),
                                 recursive=True))
            if len(hits) == 1:
                self._redirect("/doc/" + os.path.relpath(hits[0], REPO_ROOT))
                return
        self._send("<h1>404</h1>", code=404)

    def do_POST(self):
        with _LOCK:
            self._activate_from_request()
            self._do_post()

    def _do_post(self):
        if self.path == "/api/ifc-order":
            # Drag-reorder in the hub switcher. Only registry-driven hubs can
            # persist an order; --repo hubs are pinned to their flag order.
            if not REGISTRY_FILE:
                self._send(json.dumps({"ok": False, "error": "order is fixed "
                           "by --repo flags"}), "application/json; charset=utf-8",
                           code=400)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                slugs = json.loads(self.rfile.read(length).decode("utf-8"))["slugs"]
                by_slug = {it.slug: it for it in INTERFACES}
                if sorted(slugs) != sorted(by_slug):
                    raise ValueError("slugs don't match the serving interfaces")
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": "bad request: " + str(exc)}),
                           "application/json; charset=utf-8", code=400)
                return
            ordered = [by_slug[s].root for s in slugs]
            save_registry_order(REGISTRY_FILE, ordered)
            global _REGISTRY_MTIME
            try:
                _REGISTRY_MTIME = os.path.getmtime(REGISTRY_FILE)
            except OSError:
                pass
            build_registry(ordered)
            self._send(json.dumps({"ok": True, "order": slugs}),
                       "application/json; charset=utf-8")
            return
        if self.path == "/api/pin":
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                tid = payload["id"]
                want = bool(payload.get("pinned"))
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": "bad request: " + str(exc)}),
                           "application/json; charset=utf-8", code=400)
                return
            # A slash means a repo-relative doc path — any markdown file can be
            # pinned, not just tickets. Bare ids must be real tickets.
            if "/" in tid:
                full = os.path.realpath(os.path.join(REPO_ROOT, tid))
                if not (full.startswith(os.path.realpath(REPO_ROOT) + os.sep)
                        and full.endswith(".md") and os.path.isfile(full)):
                    self._send(json.dumps({"ok": False, "error": "unknown doc " + tid}),
                               "application/json; charset=utf-8", code=404)
                    return
            else:
                _, id_index = scan()
                if tid not in id_index:
                    self._send(json.dumps({"ok": False, "error": "unknown ticket " + tid}),
                               "application/json; charset=utf-8", code=404)
                    return
            try:
                pins = load_pins()
                if want:
                    pins[tid] = datetime.datetime.now().isoformat(timespec="seconds")
                else:
                    pins.pop(tid, None)
                save_pins(pins)
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": str(exc)}),
                           "application/json; charset=utf-8", code=500)
                return
            self._send(json.dumps({"ok": True, "id": tid, "pinned": want}),
                       "application/json; charset=utf-8")
            return
        if self.path == "/api/note":
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                which = payload["which"]
                content = payload["content"]
                if which not in NOTE_FILES or not isinstance(content, str):
                    raise ValueError("unknown note or non-string content")
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": "bad request: " + str(exc)}),
                           "application/json; charset=utf-8", code=400)
                return
            try:
                save_note(which, content)
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": str(exc)}),
                           "application/json; charset=utf-8", code=500)
                return
            self._send(json.dumps({"ok": True, "which": which}),
                       "application/json; charset=utf-8")
            return
        if self.path == "/api/render":
            # Render scratchpad markdown for the notes pop-out's preview toggle.
            # md_to_html escapes raw HTML, so the result is safe to inject.
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                content = payload.get("content")
                if not isinstance(content, str):
                    raise ValueError("content must be a string")
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": "bad request: " + str(exc)}),
                           "application/json; charset=utf-8", code=400)
                return
            try:
                # A scratchpad block that became a ticket carries its id; link
                # it, so the preview is a way through to the work it spawned.
                data, id_index = scan()
                rendered = autolink_ids(md_to_html(content), set(id_index),
                                        {e["id"] for e in data["epics"]},
                                        adr_index()[1])
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": str(exc)}),
                           "application/json; charset=utf-8", code=500)
                return
            self._send(json.dumps({"ok": True, "html": rendered}),
                       "application/json; charset=utf-8")
            return
        if self.path == "/api/theme":
            # The footer picker: set this repo's theme preset in config.json.
            try:
                length = int(self.headers.get("Content-Length", 0))
                name = json.loads(self.rfile.read(length).decode("utf-8"))["theme"]
                if name not in THEMES and name not in PRESETS:
                    raise ValueError("unknown theme %r" % name)
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": "bad request: " + str(exc)}),
                           "application/json; charset=utf-8", code=400)
                return
            try:
                save_theme(name)
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": str(exc)}),
                           "application/json; charset=utf-8", code=500)
                return
            self._send(json.dumps({"ok": True, "theme": name}),
                       "application/json; charset=utf-8")
            return
        if self.path == "/api/links":
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                action = payload.get("action")
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": "bad request: " + str(exc)}),
                           "application/json; charset=utf-8", code=400)
                return
            links = _links_as_list()
            try:
                if action == "add":
                    url = _normalize_url(payload.get("url"))
                    if not url:
                        raise ValueError("a url is required")
                    entry = {}
                    if (payload.get("emoji") or "").strip():
                        entry["emoji"] = payload["emoji"].strip()
                    if (payload.get("title") or "").strip():
                        entry["title"] = payload["title"].strip()
                    entry["url"] = url
                    links.append(entry)
                elif action == "remove":
                    i = int(payload.get("index"))
                    if not (0 <= i < len(links)):
                        raise ValueError("index out of range")
                    links.pop(i)
                else:
                    raise ValueError("unknown action")
                save_links(links)
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": str(exc)}),
                           "application/json; charset=utf-8", code=400)
                return
            self._send(json.dumps({"ok": True, "links": links}),
                       "application/json; charset=utf-8")
            return
        if self.path != "/api/save":
            self._send(json.dumps({"ok": False, "error": "not found"}),
                       "application/json; charset=utf-8", code=404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            tid = payload["id"]
            content = payload["content"]
        except Exception as exc:
            self._send(json.dumps({"ok": False, "error": "bad request: " + str(exc)}),
                       "application/json; charset=utf-8", code=400)
            return

        _, id_index = scan()
        target = id_index.get(tid)
        if not target:
            self._send(json.dumps({"ok": False, "error": "unknown ticket " + tid}),
                       "application/json; charset=utf-8", code=404)
            return

        # Safety: only ever write inside tickets/.
        real = os.path.realpath(target)
        if not real.startswith(os.path.realpath(TICKETS_DIR) + os.sep):
            self._send(json.dumps({"ok": False, "error": "refused: path outside tickets/"}),
                       "application/json; charset=utf-8", code=403)
            return

        try:
            tmp = real + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            os.replace(tmp, real)  # atomic swap
        except Exception as exc:
            self._send(json.dumps({"ok": False, "error": str(exc)}),
                       "application/json; charset=utf-8", code=500)
            return

        self._send(json.dumps({"ok": True, "path": os.path.relpath(real, REPO_ROOT)}),
                   "application/json; charset=utf-8")


# --------------------------------------------------------------------------- #
# Config: read <repo>/.interfacile/config.json and reskin the live server from it.
# --------------------------------------------------------------------------- #
# A theme is a hex remap applied to the canonical (blue) template, plus an
# optional signature strip. "blue" is the template's own colours (identity);
# "violet-neon" is the map derived from the sibling fork.
THEMES = {
    "blue": {"remap": {}, "strip": None},
    "violet-neon": {
        "strip": ["#FF5F15", "#FFFF00", "#39FF14", "#00FFFF", "#E10098"],
        "remap": {
            "#e9edf1": "#ece7f2", "#f3f6f9": "#f5f2f9", "#101720": "#14101f",
            "#3a4652": "#423a52", "#5d6a77": "#675d77", "#d6dde5": "#dcd5e6",
            "#1c3bb3": "#06718a", "#2f9e5b": "#2b9e33", "#8a94a0": "#948aa0",
            "#b3781a": "#bd5410", "#0b0f15": "#100b1a", "#141b25": "#1a1428",
            "#0f1620": "#150f22", "#e8edf3": "#ece8f3", "#c2ccd6": "#cac2d6",
            "#8b98a6": "#948ba6", "#26313e": "#322640", "#a9bdff": "#8ff0fb",
            "#48c483": "#46d332", "#69747f": "#71697f", "#d9a441": "#ff8a4d",
            "#e6ebf0": "#e9e4f0", "#2b53e6": "#0894b3", "#dbe2fb": "#d6f4f9",
            "#d3ecdc": "#daf5d4", "#98a2ad": "#9d98ad", "#e2e6ea": "#e6e2ea",
            "#1d2732": "#291d36", "#6a8dff": "#2ee6f7", "#1c2740": "#0b3540",
            "#16311f": "#143310", "#222c37": "#2c2237", "#3987e5": "#29c8de",
            "#199e70": "#23b53c", "#86b6ef": "#efe08e", "#5598e7": "#f7ae5e",
            "#2a78d6": "#f0742c", "#1c5cab": "#b8107f", "#184f95": "#45400f",
            "#256abf": "#6d3d12", "#c23b3b": "#d6219c", "#e06c6c": "#e668c2",
        },
    },
}


def _hexrgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgbhex(t):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in t)


def _mix(a, b, t):
    """Blend hex a toward hex b by fraction t (0..1)."""
    ra, rb = _hexrgb(a), _hexrgb(b)
    return _rgbhex(tuple(ra[i] * (1 - t) + rb[i] * t for i in range(3)))


def _palette_css(light, dark, strip):
    """Build a :root override (both the dashboard and flow-page variable
    vocabularies) from a small set of semantic roles, deriving surface/soft
    tints so a repo only specifies the colours that define its identity."""
    def block(pal, surface):
        g = pal.get
        surf = g("surface", "#ffffff")
        ground = g("ground", "#e9edf1")
        ink = g("ink", "#101720")
        acc = g("accent", "#1c3bb3")
        done = g("done", "#2f9e5b")
        warn = g("warn", "#b3781a")
        line = g("line", _mix(ground, ink, .14))
        muted = g("muted", _mix(ink, surf, .42))
        wf = g("wontfix", muted)
        surf2 = g("surface2", _mix(surf, ground, .5))
        ink2 = g("ink2", _mix(ink, surf, .28))
        soft = lambda c: _mix(c, surface, .86)
        pairs = [
            ("--bg", ground), ("--ground", ground), ("--surface", surf),
            ("--surface2", surf2), ("--surface-2", surf2),
            ("--ink", ink), ("--ink2", ink2), ("--ink-2", ink2),
            ("--mut", muted), ("--ink-mut", muted),
            ("--line", line), ("--line-2", _mix(line, surf, .5)),
            ("--accent", acc), ("--accent-ink", acc), ("--accent-soft", soft(acc)),
            ("--done", done), ("--done-soft", soft(done)),
            ("--wf", wf), ("--wf-soft", soft(wf)), ("--warn", warn),
        ]
        return ":root{%s}" % ";".join("%s:%s" % kv for kv in pairs)

    css = block(light, light.get("surface", "#ffffff"))
    css += "@media(prefers-color-scheme:dark){%s}" % block(dark, dark.get("surface", "#141b25"))
    css += (".b-open{background:var(--accent-soft);color:var(--accent-ink)}"
            ".b-closed{background:var(--done-soft);color:var(--done)}"
            ".b-wf{background:var(--wf-soft);color:var(--wf)}")
    if strip:
        css += ("html::before{content:'';display:block;height:3px;"
                "background:linear-gradient(90deg,%s)}" % ",".join(strip))
    return css


# Palette presets — each has a distinctive background (the key context cue when
# switching interfaces). Only ground/surface/ink/accent are needed; line, muted,
# tints, and dark-mode softs derive. Plus the two remap presets in THEMES
# (blue, violet-neon), that's 14 built-in looks.
PRESETS = {
    "green":   {"light": {"ground": "#dcefd0", "surface": "#ffffff", "ink": "#15200f", "accent": "#2e8f43"},
                "dark":  {"ground": "#101a0c", "surface": "#18220f", "ink": "#e8f3dd", "accent": "#5fd06a"}},
    "forest":  {"light": {"ground": "#d2e7db", "surface": "#ffffff", "ink": "#0f1f17", "accent": "#12795a"},
                "dark":  {"ground": "#0c1711", "surface": "#132019", "ink": "#daeee4", "accent": "#33c79a"}},
    "teal":    {"light": {"ground": "#d2ecec", "surface": "#ffffff", "ink": "#0f1e1e", "accent": "#0c8f8f"},
                "dark":  {"ground": "#0a1717", "surface": "#11201f", "ink": "#daeeee", "accent": "#2ed9d9"}},
    "cyan":    {"light": {"ground": "#d5ecf6", "surface": "#ffffff", "ink": "#0d1c24", "accent": "#0a86ad"},
                "dark":  {"ground": "#0a1620", "surface": "#111f28", "ink": "#dceef7", "accent": "#38c6e6"}},
    "indigo":  {"light": {"ground": "#e2e2fb", "surface": "#ffffff", "ink": "#141433", "accent": "#4b4bd6"},
                "dark":  {"ground": "#101026", "surface": "#17173a", "ink": "#e6e6f7", "accent": "#8f8ff5"}},
    "violet":  {"light": {"ground": "#ece0f7", "surface": "#ffffff", "ink": "#1f1430", "accent": "#8b3fd6"},
                "dark":  {"ground": "#160f24", "surface": "#1e1633", "ink": "#efe6fa", "accent": "#c48ff5"}},
    "rose":    {"light": {"ground": "#fbe0ec", "surface": "#ffffff", "ink": "#2a0f1c", "accent": "#d6217e"},
                "dark":  {"ground": "#1f0a15", "surface": "#2a1020", "ink": "#fbe0ec", "accent": "#ff6fb0"}},
    "crimson": {"light": {"ground": "#fadfe0", "surface": "#ffffff", "ink": "#280f11", "accent": "#cf2f3b"},
                "dark":  {"ground": "#1c0b0c", "surface": "#271012", "ink": "#fbe0e1", "accent": "#f56b74"}},
    "orange":  {"light": {"ground": "#fde7cf", "surface": "#ffffff", "ink": "#241608", "accent": "#d9741a"},
                "dark":  {"ground": "#1c1108", "surface": "#271910", "ink": "#fbe8d6", "accent": "#ffa24d"}},
    "amber":   {"light": {"ground": "#fbf1c9", "surface": "#ffffff", "ink": "#221d08", "accent": "#b8901a"},
                "dark":  {"ground": "#1a1608", "surface": "#241f10", "ink": "#f7f0d6", "accent": "#f0c74d"}},
    "lime":    {"light": {"ground": "#eaf3c9", "surface": "#ffffff", "ink": "#1c2008", "accent": "#6f9e1a"},
                "dark":  {"ground": "#141808", "surface": "#1e2210", "ink": "#eef3d6", "accent": "#b6e04a"}},
    "slate":   {"light": {"ground": "#e4e8ee", "surface": "#ffffff", "ink": "#141a22", "accent": "#47566b"},
                "dark":  {"ground": "#0f141a", "surface": "#171d26", "ink": "#e6ebf2", "accent": "#8fa0b8"}},
}


def resolve_theme(theme):
    """-> (hex_remap, strip_colours, override_css). A string (or {"name": ...})
    selects a built-in palette PRESET or a remap THEME; a dict with a `light`
    palette is a fully custom theme."""
    if isinstance(theme, dict) and (theme.get("light") or theme.get("palette")):
        light = theme.get("light") or theme.get("palette") or {}
        dark = theme.get("dark") or light
        return {}, None, _palette_css(light, dark, theme.get("strip"))
    name = theme.get("name") if isinstance(theme, dict) else theme
    strip = theme.get("strip") if isinstance(theme, dict) else None
    if name in PRESETS:
        p = PRESETS[name]
        return {}, None, _palette_css(p["light"], p["dark"],
                                      strip if strip is not None else p.get("strip"))
    if name in THEMES:
        t = THEMES[name]
        return dict(t.get("remap", {})), (strip if strip is not None else t.get("strip")), ""
    t = THEMES["blue"]
    return dict(t.get("remap", {})), t.get("strip"), ""


def theme_names():
    """Every built-in theme, remap themes first (blue is the engine default)."""
    return list(THEMES) + [n for n in PRESETS if n not in THEMES]


def _theme_swatch_colors(name):
    """(ground, accent) for a theme's footer dot. Remap themes aren't palette
    dicts, so their two representatives are spelled out here."""
    fixed = {"blue": ("#e9edf1", "#2b53e6"), "violet-neon": ("#ece7f2", "#0894b3")}
    if name in fixed:
        return fixed[name]
    p = PRESETS[name]["light"]
    return p["ground"], p["accent"]


def save_theme(name):
    """Write the chosen preset into the active repo's config.json (preserving
    every other key) and re-resolve the live theme, so the very next render —
    the reload the picker triggers — already wears it."""
    global THEME_REMAP, THEME_STRIP, THEME_OVERRIDE_CSS, THEME_NAME
    path = os.path.join(REPO_ROOT, CONFIG_REL)
    try:
        with open(path, encoding="utf-8") as fh:
            conf = json.load(fh)
        if not isinstance(conf, dict):
            conf = {}
    except FileNotFoundError:
        conf = {}
    conf["theme"] = name
    _ensure_state_dir()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(conf, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    THEME_REMAP, THEME_STRIP, THEME_OVERRIDE_CSS = resolve_theme(name)
    THEME_NAME = name
    it = _IFACE_BY_SLUG.get(ACTIVE_SLUG)
    if it is not None:
        it.conf["theme"] = name
        it._mtime = it._conf_mtime()


def render_theme_picker():
    """The quiet row of theme dots at the very bottom of the board. Clicking
    one writes `theme` into this repo's config.json — the same file, the same
    key you'd edit by hand."""
    dots = []
    for name in theme_names():
        ground, accent = _theme_swatch_colors(name)
        on = " on" if name == THEME_NAME else ""
        dots.append(
            "<button type='button' class='th-dot%s' data-theme='%s' "
            "title='%s' aria-pressed='%s' style='background:%s;"
            "box-shadow:inset 0 0 0 3px %s'></button>"
            % (on, name, name, "true" if on else "false", accent, ground))
    custom = ("<span class='th-custom'>custom palette &middot; pick a dot to "
              "replace it</span>" if THEME_NAME == "custom" else "")
    return (
        "<div class='th-pick'><span class='th-lab'>theme</span>"
        + "".join(dots) + custom + "</div>"
        "<style>"
        ".th-pick{display:flex;align-items:center;justify-content:center;gap:7px;"
        "flex-wrap:wrap;margin-top:14px;opacity:.55;transition:opacity .15s}"
        ".th-pick:hover{opacity:1}"
        ".th-lab{font:500 10.5px/1 var(--font-mono,ui-monospace,monospace);"
        "color:var(--ink-mut);margin-right:3px;text-transform:uppercase;"
        "letter-spacing:.08em}"
        ".th-dot{width:16px;height:16px;border-radius:50%;cursor:pointer;"
        "border:1px solid var(--line);padding:0;transition:transform .12s}"
        ".th-dot:hover{transform:scale(1.35)}"
        ".th-dot.on{outline:2px solid var(--accent);outline-offset:2px}"
        ".th-custom{font:500 10.5px/1 var(--font-sans,system-ui);color:var(--ink-mut)}"
        "</style>"
        "<script>(function(){"
        "document.querySelectorAll('.th-pick .th-dot').forEach(function(b){"
        "b.addEventListener('click',function(){"
        "fetch('/api/theme',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({theme:b.getAttribute('data-theme')})})"
        ".then(function(r){return r.json();})"
        ".then(function(res){if(res.ok)location.reload();});});});"
        "})();</script>")


def _normalize_url(url):
    """Make a link actually clickable: a bare host like `acme.com` is a *relative*
    href that goes nowhere, so give it an https:// scheme. Existing schemes,
    mailto:/tel:, protocol-relative, and site-relative paths are left alone."""
    url = (url or "").strip()
    if url and "://" not in url and not url.startswith(("mailto:", "tel:", "/", "#", "?")):
        url = "https://" + url
    return url


def render_project_links():
    """Emoji link buttons from the config's `links` list, shown in the header
    next to the pin/scratchpad. Every entry is fully user-defined —
    `{"emoji", "title", "url"}` — with no fixed set and no limit, rendered in
    order. A missing emoji falls back to 🔗; an entry with no url is skipped.
    The title shows as a hover overlay. '' when there are none."""
    items = LINKS
    if isinstance(items, dict):        # tolerate an older {name: url|{...}} map
        items = [v if isinstance(v, dict) else {"url": v, "title": k}
                 for k, v in items.items()]
    if not isinstance(items, list):
        return ""
    out = []
    for it in items:
        if isinstance(it, str):
            url, emoji, title = it, "", ""
        elif isinstance(it, dict):
            url = it.get("url") or it.get("href") or ""
            emoji = it.get("emoji") or ""
            title = it.get("title") or it.get("label") or ""
        else:
            continue
        url = _normalize_url(url)
        if not url:
            continue
        emoji = emoji or "\U0001f517"   # 🔗 default when none is given
        title = title or url
        out.append(
            "<a class=\"proj-link\" href=\"%s\" target=\"_blank\" rel=\"noopener\" "
            "aria-label=\"%s\"><span class=\"pl-emo\">%s</span>"
            "<span class=\"pl-tip\" role=\"tooltip\">%s</span></a>"
            % (html.escape(url, quote=True), html.escape(title),
               html.escape(emoji), html.escape(title)))
    return "".join(out)


def _links_as_list():
    """Current links as a mutable list of dicts (tolerating the legacy map form)."""
    items = LINKS
    if isinstance(items, dict):
        items = [v if isinstance(v, dict) else {"url": v, "title": k}
                 for k, v in items.items()]
    out = []
    if isinstance(items, list):
        for x in items:
            if isinstance(x, dict):
                out.append(dict(x))
            elif isinstance(x, str):
                out.append({"url": x})
    return out


def save_links(links):
    """Write the links list into the active repo's `.interfacile/config.json`
    (preserving every other key) and refresh the in-memory config, so an added or
    removed link shows on the next page load without a restart."""
    global LINKS
    path = os.path.join(REPO_ROOT, CONFIG_REL)
    try:
        with open(path, encoding="utf-8") as fh:
            conf = json.load(fh)
        if not isinstance(conf, dict):
            conf = {}
    except FileNotFoundError:
        conf = {}
    if links:
        conf["links"] = links
    else:
        conf.pop("links", None)
    _ensure_state_dir()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(conf, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    LINKS = links
    it = _IFACE_BY_SLUG.get(ACTIVE_SLUG)
    if it is not None:
        it.conf["links"] = links


# The "+" pill that sits after the project links; the popover it opens is injected
# once per page (see _linkedit_html / _transform_html).
_LINK_ADD_BTN = ("<button class='link-add' type='button' "
                 "title='Add or remove a header link' aria-label='Add link'>+</button>")


def _linkedit_html():
    """The add/remove-link popover: an add form plus the current links each with a
    remove button. On success the page reloads so every link button refreshes."""
    rows = []
    for i, it in enumerate(_links_as_list()):
        emoji = it.get("emoji") or "\U0001f517"
        label = it.get("title") or it.get("url") or ""
        rows.append("<div class='lk-row'><span class='lk-e'>%s</span>"
                    "<span class='lk-t'>%s</span>"
                    "<button type='button' class='lk-del' data-i='%d' title='Remove'>&times;</button></div>"
                    % (html.escape(emoji), html.escape(label), i))
    rows_html = "".join(rows) or "<div class='lk-empty'>No links yet.</div>"
    css = ("<style>"
        ".link-add{font-family:var(--font-mono);font-size:1.05rem;line-height:1;border:1px dashed var(--line);"
        "background:var(--surface-2);color:var(--ink-mut);border-radius:100px;width:32px;height:32px;cursor:pointer;"
        "display:inline-flex;align-items:center;justify-content:center;padding:0;flex:none}"
        ".link-add:hover{border-style:solid;border-color:var(--accent);color:var(--accent-ink)}"
        ".link-pop{position:fixed;top:58px;right:16px;z-index:650;width:min(300px,92vw);background:var(--surface);"
        "border:1px solid var(--line);border-radius:12px;box-shadow:0 18px 44px rgba(0,0,0,.24);padding:12px}"
        ".link-pop[hidden]{display:none}"
        ".lk-h{font-family:var(--font-mono);font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;"
        "color:var(--ink-mut);margin:0 0 9px}"
        ".lk-add{display:grid;grid-template-columns:46px 1fr;gap:6px;margin-bottom:10px}"
        ".lk-add input{font-family:var(--font-mono);font-size:.78rem;padding:6px 8px;border:1px solid var(--line);"
        "border-radius:6px;background:var(--surface-2);color:var(--ink);outline:none;min-width:0}"
        ".lk-add input:focus{border-color:var(--accent)}"
        "#lkEmoji{text-align:center}#lkUrl{grid-column:1 / -1}"
        ".lk-add button{grid-column:1 / -1;font-family:var(--font-mono);font-size:.76rem;background:var(--accent);"
        "color:#fff;border:0;border-radius:6px;padding:7px;cursor:pointer}"
        ".lk-list{display:flex;flex-direction:column;gap:2px;max-height:180px;overflow:auto}"
        ".lk-row{display:flex;align-items:center;gap:8px;padding:5px 4px;border-radius:6px}"
        ".lk-row:hover{background:var(--surface-2)}.lk-e{flex:none}"
        ".lk-t{flex:1;font-size:.8rem;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        ".lk-del{flex:none;border:1px solid var(--line);background:var(--surface-2);color:var(--ink-mut);"
        "border-radius:5px;width:22px;height:22px;cursor:pointer;line-height:1;padding:0}"
        ".lk-del:hover{color:#c23b3b;border-color:#c23b3b}"
        ".lk-empty{font-size:.8rem;color:var(--ink-mut);padding:6px 4px}"
        ".lk-msg{font-family:var(--font-mono);font-size:.68rem;color:var(--ink-mut);margin-top:8px;min-height:1em}"
        ".lk-msg.err{color:#c23b3b}"
        "</style>")
    body = ("<div class='link-pop' id='linkPop' hidden>"
            "<p class='lk-h'>Header links</p>"
            "<form class='lk-add' id='lkAdd'>"
            "<input id='lkEmoji' maxlength='4' placeholder='&#128279;' aria-label='Emoji'>"
            "<input id='lkTitle' placeholder='Title' aria-label='Title'>"
            "<input id='lkUrl' type='text' inputmode='url' placeholder='example.com or https://&hellip;' "
            "aria-label='URL' required>"
            "<button type='submit'>Add link</button></form>"
            "<div class='lk-list' id='lkList'>" + rows_html + "</div>"
            "<div class='lk-msg' id='lkMsg'></div></div>")
    js = ("<script>(function(){"
          "var pop=document.getElementById('linkPop');if(!pop)return;"
          "function show(o){pop.hidden=!o;}"
          "document.addEventListener('click',function(e){"
          "if(e.target.closest('.link-add')){e.preventDefault();e.stopPropagation();show(pop.hidden);return;}"
          "if(!e.target.closest('#linkPop'))show(false);});"
          "var msg=document.getElementById('lkMsg');"
          "function fail(m){msg.className='lk-msg err';msg.textContent=m;}"
          "function post(b){return fetch('/api/links',{method:'POST',"
          "headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(function(r){return r.json();});}"
          "document.getElementById('lkAdd').addEventListener('submit',function(e){e.preventDefault();"
          "var url=document.getElementById('lkUrl').value.trim();if(!url){fail('a url is required');return;}"
          "msg.className='lk-msg';msg.textContent='saving\\u2026';"
          "post({action:'add',url:url,title:document.getElementById('lkTitle').value.trim(),"
          "emoji:document.getElementById('lkEmoji').value.trim()})"
          ".then(function(j){if(j.ok)location.reload();else fail(j.error||'failed');})"
          ".catch(function(){fail('server down?');});});"
          "document.getElementById('lkList').addEventListener('click',function(e){"
          "var b=e.target.closest('.lk-del');if(!b)return;msg.className='lk-msg';msg.textContent='removing\\u2026';"
          "post({action:'remove',index:parseInt(b.getAttribute('data-i'),10)})"
          ".then(function(j){if(j.ok)location.reload();else fail(j.error||'failed');})"
          ".catch(function(){fail('server down?');});});"
          "})();</script>")
    return css + body + js


_LIST_TOOLS = r"""<style>
.list-tools{display:inline-flex;gap:12px;align-items:center;margin-left:auto}
.list-tools button{font-family:var(--font-mono,ui-monospace,monospace);font-size:.66rem;letter-spacing:.04em;
color:var(--accent-ink,var(--accent,#0a7d6b));background:none;border:0;cursor:pointer;padding:0;white-space:nowrap}
.list-tools button:hover{text-decoration:underline}
</style><script>(function(){
function rowsOf(sel){var c=sel&&document.querySelector(sel);if(!c)return [];
  return Array.prototype.map.call(c.querySelectorAll('a[href*="/ticket/"]'),function(a){
    var d=a.dataset||{},h=a.getAttribute("href")||"",id=decodeURIComponent((h.split("/ticket/")[1]||"").split(/[?#]/)[0]);
    return {id:id,slug:d.slug||id,title:d.title||"",status:d.status||"",epic:d.epic||"",
            priority:d.priority||"",risk:d.risk||"",effort:d.effort||""};});}
function flash(btn,txt){var o=btn.getAttribute("data-lbl")||btn.textContent;btn.setAttribute("data-lbl",o);
  btn.textContent=txt;setTimeout(function(){btn.textContent=o;},1400);}
function copy(text,btn,msg){
  var ok=function(){flash(btn,msg);},no=function(){flash(btn,"copy failed");};
  if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(text).then(ok,no);
  else{var ta=document.createElement("textarea");ta.value=text;ta.style.position="fixed";ta.style.opacity="0";
    document.body.appendChild(ta);ta.select();try{document.execCommand("copy")?ok():no();}catch(e){no();}document.body.removeChild(ta);}}
function csv(items,name){
  var head="id,title,status,epic,priority,risk,effort";
  var body=items.map(function(t){return [t.id,'"'+String(t.title).replace(/"/g,'""')+'"',t.status,t.epic,t.priority,t.risk,t.effort].join(",");}).join("\n");
  var url=URL.createObjectURL(new Blob([head+"\n"+body+"\n"],{type:"text/csv"}));
  var a=document.createElement("a");a.href=url;a.download=name+"-"+new Date().toISOString().slice(0,10)+".csv";a.click();URL.revokeObjectURL(url);}
document.addEventListener("click",function(e){
  /* single-item copy (a ticket or epic page) */
  var one=e.target.closest(".copy-one[data-copy]");
  if(one){e.preventDefault();copy(one.getAttribute("data-copy"),one,"copied");return;}
  var b=e.target.closest(".list-tools button");if(!b)return;
  var box=b.closest(".list-tools"),items=rowsOf(box.getAttribute("data-src"));
  if(!items.length){flash(b,"no items");return;}
  if(b.classList.contains("lt-copy"))copy(items.map(function(t){return t.slug||t.id;}).join("\n"),b,"copied "+items.length);
  else csv(items,box.getAttribute("data-name")||"tickets");
});
})();</script>"""


def _list_tools(src, name):
    """A tiny copy-ids + CSV toolbar bound (by CSS selector) to a ticket list."""
    return ("<span class='list-tools' data-src='%s' data-name='%s'>"
            "<button type='button' class='lt-copy'>copy ids</button>"
            "<button type='button' class='lt-csv'>CSV</button></span>"
            % (html.escape(src, quote=True), html.escape(name, quote=True)))


def _transform_html(s):
    """Reskin one HTML page for the active interface: theme colours + signature
    strip, then the identity tokens the templates carry (__PFX__, __BRAND__,
    __FAVICON__, __TAGLINE__, __EYEBROW__).

    Tokens — not real-looking search text — so a ticket body that happens to
    mention another project's ids is never rewritten, and editing template
    copy can't silently break the substitution. Always substituted: with no
    config the engine defaults ("TK", "Tickets", ...) render."""
    if THEME_REMAP:
        for a, b in THEME_REMAP.items():
            s = s.replace(a, b)
    if THEME_STRIP:
        strip = ("html::before{content:'';display:block;height:3px;"
                 "background:linear-gradient(90deg,%s)}" % ",".join(THEME_STRIP))
        s = s.replace("*{box-sizing:border-box}",
                      strip + "*{box-sizing:border-box}", 1)
    s = (s.replace("__PFX__", PFX)
          .replace("__BRAND__", BRAND)
          .replace("__FAVICON__", FAVICON)
          .replace("__TAGLINE__", TAGLINE)
          .replace("__EYEBROW__", EYEBROW))
    # Dashboard-only tokens (no-ops on every other page). Always substituted so
    # the standard title mark renders even with no config file.
    if "__HDR_ICON__" in s:
        s = s.replace("__HDR_ICON__", html.escape(HEADER_ICON))
    if "__PROJECT_LINKS__" in s:
        s = s.replace("__PROJECT_LINKS__", _LINK_ADD_BTN + render_project_links())
    if "__DOC_SERIES__" in s:
        s = s.replace("__DOC_SERIES__", "".join(
            '<a class="hbtn" href="%s">%ss &nearr;</a>'
            % (html.escape(sr["href"]), html.escape(sr["prefix"]))
            for sr in doc_series()))
    if "__THEME_PICKER__" in s:
        s = s.replace("__THEME_PICKER__", render_theme_picker())
    if "__POCKET__" in s:
        s = s.replace("__POCKET__", pocket_html())
    # Every page gets the pocket module — the dashboard fills it from the board
    # it already fetched, the top bar from /api/pocket, and neither has to know
    # where the badge ended up.
    s = _BODY_OPEN_RE.sub(
        lambda m: m.group(1) + "<script>" + pocket_js() + "</script>", s, count=1)
    if THEME_OVERRIDE_CSS:
        s = s.replace("</head>", "<style>" + THEME_OVERRIDE_CSS + "</style></head>", 1)
    s = s.replace("</body>", _FOOTER + "</body>", 1)
    if len(INTERFACES) > 1:
        is_dash = 'id="regen"' in s          # the dashboard has the Regenerate button
        s = _BODY_OPEN_RE.sub(lambda m: m.group(1) + _switcher_html(is_dash), s, count=1)
        # Sub-pages get the notes/to-do drawer + logic injected so the bar's
        # scratchpad/to-do buttons work there too (the dashboard has its own).
        if not is_dash:
            s = s.replace("</body>",
                          _NOTES_PANEL_HTML + "<script>" + _NOTES_JS + "</script></body>", 1)
    # Wherever the "+" add-link pill lands (dashboard head or the bar), give it its
    # popover — the small emoji/title/url editor that writes config.json.
    if "link-add" in s and "</body>" in s:
        s = s.replace("</body>", _linkedit_html() + "</body>", 1)
    # Wire copy-ids + CSV toolbars (ticket lists) and single-item copy buttons.
    if ("list-tools" in s or "copy-one" in s) and "</body>" in s:
        s = s.replace("</body>", _LIST_TOOLS + "</body>", 1)
    # Every fenced code block gets a hover "copy" button, on any page.
    if "<pre" in s and "</body>" in s:
        s = s.replace("</body>", _CODE_COPY + "</body>", 1)
    return s


CONFIG_REL = os.path.join(STATE_DIR, "config.json")   # <repo>/.interfacile/config.json


def load_config(repo_root):
    """Read the repo's interface config from `.interfacile/config.json`. {} when
    it's absent; a malformed file is warned about and ignored so a bad config
    never takes the dashboard down."""
    path = os.path.join(repo_root, CONFIG_REL)
    try:
        with open(path, encoding="utf-8") as fh:
            conf = json.load(fh)
        return conf if isinstance(conf, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        sys.stderr.write("%s ignored (%s)\n" % (CONFIG_REL, exc))
        return {}


def apply_config(conf):
    """Overwrite the live per-interface globals from a parsed config dict.
    Missing keys keep the current (default) value, so partial configs are fine."""
    global PFX, ID_DIGITS, BRAND, FAVICON, HEADER_ICON, EYEBROW, TAGLINE
    global SERVER_PORT, EPIC_TITLES, EPIC_EMOJI, THEME_REMAP, THEME_STRIP, _IDRE
    global THEME_OVERRIDE_CSS, LINKS, DOC_RULES
    global TICKET_ID_RE, EPIC_CODE_RE, TICKET_PARTS_RE, DEP_ID_RE, SUB_ID_RE
    global TICKET_LINK_RE, EPIC_LINK_RE, _MD_EPIC_FILE_RE, _MD_TICKET_FILE_RE

    brand = conf.get("brand", {})
    BRAND = brand.get("name", _DEF["brand"])
    FAVICON = brand.get("favicon", _DEF["favicon"])
    HEADER_ICON = brand.get("icon", brand.get("favicon", _DEF["icon"]))
    EYEBROW = brand.get("eyebrow", _DEF["eyebrow"])
    TAGLINE = brand.get("tagline", _DEF["tagline"])

    links = conf.get("links", [])
    LINKS = links if isinstance(links, (list, dict)) else []

    ids = conf.get("ids", {})
    PFX = ids.get("prefix", _DEF["pfx"])
    ID_DIGITS = int(ids.get("digits", _DEF["digits"]))
    _IDRE = make_id_res(PFX, ID_DIGITS)
    TICKET_ID_RE = _IDRE["ticket_id"]
    EPIC_CODE_RE = _IDRE["epic_code"]
    TICKET_PARTS_RE = _IDRE["ticket_parts"]
    DEP_ID_RE = _IDRE["dep_id"]
    SUB_ID_RE = _IDRE["sub_id"]
    TICKET_LINK_RE = _IDRE["ticket_link"]
    EPIC_LINK_RE = _IDRE["epic_link"]
    _MD_EPIC_FILE_RE = _IDRE["md_epic"]
    _MD_TICKET_FILE_RE = _IDRE["md_ticket"]

    epics = {_epic_key(k): v for k, v in conf.get("epics", {}).items()}
    if epics:
        EPIC_TITLES = {k: (v.get("title", k) if isinstance(v, dict) else v)
                       for k, v in epics.items()}
        EPIC_EMOJI = {k: v["emoji"] for k, v in epics.items()
                      if isinstance(v, dict) and v.get("emoji")}
    else:
        EPIC_TITLES = dict(_DEF_EPIC_TITLES)
        EPIC_EMOJI = dict(_DEF_EPIC_EMOJI)

    # Document-link rules: a generic version of the built-in ADR rule. Each
    # rule autolinks PREFIX-### mentions to the matching file under its dir.
    DOC_RULES = []
    for rule in conf.get("documents") or []:
        if not isinstance(rule, dict):
            continue
        pfx = str(rule.get("prefix", "")).strip().upper()
        rel = str(rule.get("dir", "")).strip().strip("/")
        if (not re.match(r"^[A-Z][A-Z0-9]*$", pfx) or not rel or ".." in rel
                or pfx in (PFX, "ADR")):   # ticket ids and ADRs already link
            continue
        DOC_RULES.append({"prefix": pfx, "dir": rel,
                          "title": str(rule.get("title") or "").strip()})

    theme = conf.get("theme", "blue")
    THEME_REMAP, THEME_STRIP, THEME_OVERRIDE_CSS = resolve_theme(theme)
    global THEME_NAME
    if isinstance(theme, dict) and (theme.get("light") or theme.get("palette")):
        THEME_NAME = "custom"
    else:
        name = theme.get("name") if isinstance(theme, dict) else theme
        THEME_NAME = name if (name in THEMES or name in PRESETS) else "blue"
    SERVER_PORT = int(conf.get("server", {}).get("port", _DEF["port"]))


# --------------------------------------------------------------------------- #
# Multi-interface: one process serves several repos, switched by an `ifc` cookie
# and a centered header dropdown. Because the data layer reads module globals,
# each request activates its interface under a lock (fine for a local, single-
# user dashboard — requests are effectively serial anyway).
# --------------------------------------------------------------------------- #
class Interface:
    __slots__ = ("slug", "name", "icon", "root", "conf", "shortcut", "_mtime")

    def __init__(self, slug, name, icon, root, conf, shortcut=""):
        self.slug, self.name, self.icon, self.root, self.conf = slug, name, icon, root, conf
        self.shortcut = shortcut
        self._mtime = self._conf_mtime()

    def _conf_mtime(self):
        try:
            return os.path.getmtime(os.path.join(self.root, CONFIG_REL))
        except OSError:
            return 0.0

    def refresh(self):
        """Re-read config.json if it changed on disk. Tickets are re-scanned on
        every request, so a config that only took effect on restart was the odd
        one out — and silently served stale values after an edit."""
        mt = self._conf_mtime()
        if mt != self._mtime:
            self._mtime = mt
            self.conf = load_config(self.root) or {}


INTERFACES = []                  # ordered list (CLI order == switcher order)
_IFACE_BY_SLUG = {}
ACTIVE_SLUG = ""
_LOCK = threading.Lock()

REGISTRY_FILE = None             # hub only: watched so init/register show up live
_REGISTRY_MTIME = 0.0


def _load_registry_roots(path):
    """The hub registry: {"repos": [roots]} (a bare list is tolerated)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    repos = data if isinstance(data, list) else (data or {}).get("repos", [])
    return [r for r in repos if isinstance(r, str)]


def save_registry_order(path, roots):
    """Persist a new interface order, keeping registry entries that aren't
    currently served (missing tickets/, filtered at launch) and any other
    keys the file carries."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {"repos": data if isinstance(data, list) else []}
    except (OSError, ValueError):
        data = {}
    existing = [r for r in data.get("repos", []) if isinstance(r, str)]
    served = set(roots)
    data["repos"] = list(roots) + [r for r in existing
                                   if os.path.abspath(r) not in served]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def refresh_registry():
    """Follow live registry edits (`interfacile init` / `register` /
    `unregister`) without a hub restart. Runs per request under the request
    lock; when nothing changed it costs one mtime probe."""
    global _REGISTRY_MTIME
    if not REGISTRY_FILE:
        return
    try:
        mt = os.path.getmtime(REGISTRY_FILE)
    except OSError:
        return
    if mt == _REGISTRY_MTIME:
        return
    _REGISTRY_MTIME = mt
    roots = _load_registry_roots(REGISTRY_FILE)
    if roots is None:
        return
    keep = [os.path.abspath(r) for r in roots
            if os.path.isdir(os.path.join(os.path.abspath(r), "tickets"))]
    current = [it.root for it in INTERFACES]
    # Never let a bad edit empty a running hub; ignore no-op rewrites.
    if not keep or keep == current:
        return
    for root in keep:
        if root not in current:
            migrate_state(root)
    build_registry(keep)
    print("hub: registry changed — serving %d interface(s): %s"
          % (len(INTERFACES), ", ".join(it.slug for it in INTERFACES)), flush=True)


def _slugify(base, seen):
    s = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "iface"
    slug, n = s, 2
    while slug in seen:
        slug = "%s-%d" % (s, n); n += 1
    seen.add(slug)
    return slug


def build_registry(repo_roots):
    """Load each repo's config into an Interface and index by slug (CLI order).

    Shortcuts are positional, never configured: the first ten interfaces get
    the keys 1-9 then 0, the rest get none. Reorder (drag the switcher list,
    or `interfacile shortcut N`) and the numbers follow."""
    global INTERFACES, _IFACE_BY_SLUG
    INTERFACES, _IFACE_BY_SLUG, seen = [], {}, set()
    for i, r in enumerate(repo_roots):
        root = os.path.abspath(r)
        conf = load_config(root)
        brand = conf.get("brand", {})
        name = brand.get("name") or os.path.basename(root)
        icon = brand.get("icon") or brand.get("favicon") or _DEF["icon"]
        key = "1234567890"[i] if i < 10 else ""
        it = Interface(_slugify(os.path.basename(root), seen), name, icon, root, conf, key)
        INTERFACES.append(it)
        _IFACE_BY_SLUG[it.slug] = it
    _warn_shared_prefixes()
    return INTERFACES


def _iface_prefix(it):
    """An interface's ticket-id prefix, from its config (else its inference)."""
    pfx = ((it.conf.get("ids") or {}).get("prefix") or "").strip().upper()
    return pfx


def _owner_interface(tid):
    """The interface whose id prefix owns this ticket id — when that isn't the
    active one. Lets /ticket/<id> URLs resolve hub-wide instead of 404ing
    because the "wrong" project was active."""
    m = re.match(r"([A-Za-z]+)-", tid or "")
    if not m or len(INTERFACES) <= 1:
        return None
    pfx = m.group(1).upper()
    active = _IFACE_BY_SLUG.get(ACTIVE_SLUG)
    for it in INTERFACES:
        if it is not active and _iface_prefix(it) == pfx:
            return it
    return None


def _warn_shared_prefixes():
    """Two interfaces sharing an id prefix makes ids ambiguous hub-wide —
    cross-interface ticket links stop being able to pick an owner."""
    seen = {}
    for it in INTERFACES:
        pfx = _iface_prefix(it)
        if pfx and pfx in seen:
            sys.stderr.write("warning: %s and %s both use the id prefix %s- — "
                             "ticket links across the hub will be ambiguous\n"
                             % (seen[pfx].slug, it.slug, pfx))
        elif pfx:
            seen[pfx] = it


def activate(iface):
    """Point every per-interface global at one interface for the current request."""
    global REPO_ROOT, TICKETS_DIR, ADR_DIR, PINS_FILE, NOTE_FILES, ACTIVE_SLUG
    global LEGACY_PINS_FILE, LEGACY_NOTE_FILES
    REPO_ROOT = iface.root
    TICKETS_DIR = os.path.join(REPO_ROOT, "tickets")
    ADR_DIR = os.path.join(REPO_ROOT, "docs", "architecture", "adr")
    PINS_FILE, NOTE_FILES = _state_paths(REPO_ROOT)
    LEGACY_PINS_FILE, LEGACY_NOTE_FILES = _legacy_state_paths(REPO_ROOT)
    ACTIVE_SLUG = iface.slug
    iface.refresh()
    apply_config(iface.conf)


_REPO_URL = "https://github.com/aphoristicEpigram/interfacile"
_GH_PATH = ("M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 "
            "0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13"
            "-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66"
            ".07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15"
            "-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 "
            "1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 "
            "1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 "
            "1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z")
_FOOTER = (
    "<div id='ifc-foot'>"
    "<a href='%s' target='_blank' rel='noopener' title='interfacile on GitHub'>"
    "<svg viewBox='0 0 16 16' aria-hidden='true'><path fill='currentColor' d='%s'/></svg>"
    "</a><span>made with love \U0001FA77</span></div>"
    "<style>body:has(.page){background:var(--ground)}"
    "#ifc-foot{display:flex;align-items:center;justify-content:center;gap:7px;"
    "margin:14px 0 30px;font:500 11.5px/1 var(--font-sans,var(--font,system-ui));"
    "color:var(--ink-mut,var(--mut,#8a8a8a));opacity:.8}"
    "#ifc-foot a{display:inline-flex;color:inherit;text-decoration:none}"
    "#ifc-foot a:hover{color:var(--accent,#666)}"
    "#ifc-foot svg{width:14px;height:14px;display:block}</style>"
    % (_REPO_URL, _GH_PATH)
)

# A hover "copy" affordance on every <pre> code block. Injected by
# _transform_html wherever a page contains one, so ticket bodies, docs, and
# ADRs all get it without each renderer knowing about it.
_CODE_COPY = """<style>
pre{position:relative}
pre .code-copy{position:absolute;top:6px;right:6px;padding:5px 9px;cursor:pointer;
font:600 10.5px/1 var(--font-sans,var(--font,system-ui));border-radius:7px;
border:1px solid var(--line,#d6dde5);background:var(--surface,#fff);
color:var(--ink-mut,var(--mut,#8a8a8a));opacity:0;transition:opacity .12s}
pre:hover .code-copy,pre .code-copy:focus-visible{opacity:1}
pre .code-copy:hover{color:var(--accent,#666);border-color:var(--accent,#888)}
</style><script>(function(){
Array.prototype.forEach.call(document.querySelectorAll('pre'),function(pre){
  if(pre.querySelector('.code-copy'))return;
  var code=pre.querySelector('code'),text=(code||pre).textContent;
  var b=document.createElement('button');
  b.type='button';b.className='code-copy';b.textContent='copy';
  b.title='Copy this code block';
  b.addEventListener('click',function(){
    function done(ok){b.textContent=ok?'copied \\u2713':'copy failed';
      setTimeout(function(){b.textContent='copy';},1400);}
    if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(text).then(function(){done(true);},
        function(){done(false);});
    }else{
      var ta=document.createElement('textarea');ta.value=text;
      document.body.appendChild(ta);ta.select();
      try{done(document.execCommand('copy'));}catch(e){done(false);}
      document.body.removeChild(ta);
    }
  });
  pre.appendChild(b);
});
})();</script>"""

_SWITCHER_CSS = """
body{padding-top:52px}
#ifc-bar{position:fixed;top:0;left:0;right:0;z-index:200;display:flex;align-items:center;gap:12px;
padding:8px 18px;background:var(--surface,#fff);border-bottom:1px solid var(--line,#e2e2e2);
box-shadow:0 1px 3px rgba(0,0,0,.06)}
#ifc-actions{margin-left:auto;display:flex;align-items:center;gap:8px}
/* the pocket + GitHub sit dead-centre in the bar, independent of the left/right
   slots — the same pair, in the same place, on every page */
#ifc-center{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);display:flex;align-items:center;gap:8px}
#ifc-center:empty{display:none}
/* 32px is the bar's one control height. Everything in the bar — GitHub, the
   pocket, the link and notes buttons — is exactly that tall, so the row shares a
   baseline instead of each control being whatever its padding made it. */
#ifc-bar .today-pocket{display:inline-flex;align-items:center;gap:8px;line-height:1;
height:32px;padding:0 13px;box-sizing:border-box;
border:1px solid var(--line);border-radius:100px;background:var(--surface-2,transparent)}
#ifc-bar .today-pocket[hidden]{display:none}
#ifc-bar .tp-lab{font-family:var(--font-mono);font-size:.6rem;letter-spacing:.09em;text-transform:uppercase;
color:var(--ink-mut);border:0;background:none;padding:0;cursor:pointer}
#ifc-bar .tp-lab:hover{color:var(--accent-ink,var(--accent))}
#ifc-bar .tp-item{font-family:var(--font-mono);font-size:.7rem;color:var(--ink-mut);text-decoration:none}
#ifc-bar .tp-item b{font-weight:750;font-size:.8rem}
#ifc-bar .tp-new b{color:var(--accent-ink,var(--accent))}
#ifc-bar .tp-done b{color:var(--done)}
#ifc-bar .tp-item:hover{color:var(--accent-ink,var(--accent))}
@media (max-width:1100px){#ifc-center .today-pocket{display:none}}
#ifc-switch{position:relative}
.ifc-i{margin-left:6px;font-size:1.02em}
/* trigger */
#ifc-cur{display:inline-flex;align-items:center;
font:600 13px/1 var(--font-sans,var(--font,system-ui));color:var(--ink,#15202b);
background:var(--surface-2,var(--surface2,#f4f4f6));border:1px solid var(--line,#d6dde5);
border-radius:10px;padding:8px 11px;cursor:pointer;transition:border-color .14s,box-shadow .14s}
#ifc-cur:hover{border-color:var(--accent,#888);box-shadow:0 1px 7px rgba(0,0,0,.07)}
#ifc-cur[aria-expanded=true]{border-color:var(--accent,#888)}
#ifc-cur .ifc-caret{color:var(--ink-mut,var(--mut,#888));font-size:10px;margin-left:8px;
transition:transform .16s ease}
#ifc-cur[aria-expanded=true] .ifc-caret{transform:rotate(180deg)}
/* menu */
#ifc-menu{position:absolute;top:calc(100% + 8px);left:0;min-width:252px;list-style:none;
margin:0;padding:6px;background:var(--surface,#fff);border:1px solid var(--line,#d6dde5);
border-radius:13px;box-shadow:0 16px 42px rgba(0,0,0,.24);z-index:300;transform-origin:top left;
opacity:0;transform:translateY(-8px) scale(.985);visibility:hidden;pointer-events:none;
transition:opacity .15s ease,transform .15s ease}
#ifc-menu.open{opacity:1;transform:none;visibility:visible;pointer-events:auto}
#ifc-menu .ifc-hd{padding:7px 10px 6px;font:700 10px/1 var(--font-sans,var(--font,system-ui));
letter-spacing:.15em;text-transform:uppercase;color:var(--ink-mut,var(--mut,#8a8a8a))}
#ifc-menu .ifc-hd:hover{background:transparent}
#ifc-menu li{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:9px;
cursor:pointer;transition:background .1s}
#ifc-menu li .ifc-txt{display:flex;flex-direction:column;gap:3px;min-width:0;flex:1 1 auto}
#ifc-menu li .ifc-n{font:600 13px/1.15 var(--font-sans,var(--font,system-ui));
color:var(--ink,#15202b);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#ifc-menu li .ifc-sub{font:500 11px/1 var(--font-mono,ui-monospace,SFMono-Regular,monospace);
color:var(--ink-mut,var(--mut,#8a8a8a));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#ifc-menu li[draggable]{cursor:grab}
#ifc-menu li.ifc-drag{opacity:.45}
.ifc-hint{float:right;font:500 10px/1.6 var(--font-sans,var(--font,system-ui));
color:var(--ink-mut,var(--mut,#8a8a8a));text-transform:none;letter-spacing:0}
#ifc-menu li .ifc-kbd{flex:none;font:600 11px/1 var(--font-mono,ui-monospace,monospace);
color:var(--ink-mut,var(--mut,#8a8a8a));background:var(--surface-2,var(--surface2,#f2f4f8));
border:1px solid var(--line,#d6dde5);border-bottom-width:2px;border-radius:6px;
padding:4px 7px;min-width:11px;text-align:center}
#ifc-menu li .ifc-chk{flex:none;font-size:13px;opacity:0;
color:var(--accent-ink,var(--accent,#0a7d6b))}
#ifc-menu li:hover{background:var(--surface-2,var(--surface2,#f2f4f8))}
#ifc-menu li[aria-selected=true]{background:var(--accent-soft,var(--surface2,#eef1ff))}
#ifc-menu li[aria-selected=true] .ifc-chk{opacity:1}
/* prominent back-to-dashboard on sub-pages (switcher stays leftmost & fixed) */
#ifc-home{display:inline-flex;align-items:center;gap:6px;
font:600 12.5px/1 var(--font-sans,var(--font,system-ui));text-decoration:none;
color:var(--accent-ink,var(--accent,#0a7d6b));
background:var(--accent-soft,var(--surface-2,var(--surface2,#eef1ff)));
border:1px solid var(--accent,#88a);border-radius:9px;padding:8px 13px;
transition:background .12s,color .12s}
#ifc-home:hover{background:var(--accent,#88a);color:var(--surface,#fff)}
/* persistent controls carried onto every page: notes/to-do, github, links */
#ifc-center,#ifc-actions{display:flex;align-items:center;gap:8px}
#ifc-bar .note-btns{display:inline-flex;gap:8px}
#ifc-bar .notebtn{position:relative;font-size:1rem;border:1px solid var(--line);border-radius:100px;
width:38px;height:32px;cursor:pointer;line-height:1;padding:0;display:inline-flex;align-items:center;justify-content:center}
#ifc-bar .nb-notes{background:rgba(234,179,8,.16)}
#ifc-bar .nb-notes:hover,#ifc-bar .nb-notes.active{border-color:#b45309;background:rgba(234,179,8,.32)}
#ifc-bar .nb-todo{background:rgba(220,38,38,.12)}
#ifc-bar .nb-todo:hover,#ifc-bar .nb-todo.active{border-color:#c23b3b;background:rgba(220,38,38,.26)}
#ifc-bar .proj-links{display:inline-flex;gap:8px;align-items:center}
#ifc-bar .proj-links:empty{display:none}
#ifc-bar .proj-link{position:relative;font-size:1rem;text-decoration:none;border:1px solid var(--line);border-radius:100px;
width:38px;height:32px;line-height:1;display:inline-flex;align-items:center;justify-content:center;background:var(--surface-2)}
#ifc-bar .proj-link:hover,#ifc-bar .proj-link:focus-visible{border-color:var(--accent);background:var(--accent-soft)}
#ifc-bar .proj-link .pl-emo{display:inline-flex;pointer-events:none}
#ifc-bar .pl-tip{position:absolute;top:calc(100% + 9px);left:50%;transform:translate(-50%,-4px);white-space:nowrap;
font-family:var(--font-mono);font-size:.72rem;color:var(--ink);background:var(--surface);border:1px solid var(--line);
border-radius:8px;padding:5px 10px;box-shadow:var(--shadow,0 8px 24px rgba(0,0,0,.2));opacity:0;pointer-events:none;transition:opacity .12s,transform .12s;z-index:400}
#ifc-bar .proj-link:hover .pl-tip,#ifc-bar .proj-link:focus-visible .pl-tip,
#ifc-bar .notebtn:hover .pl-tip,#ifc-bar .notebtn:focus-visible .pl-tip{opacity:1;transform:translate(-50%,0)}
#ifc-bar .ghbtn{display:inline-flex;align-items:center;gap:7px;font-family:var(--font-mono);font-size:.72rem;color:var(--ink);
text-decoration:none;background:var(--surface-2);border:1px solid var(--line);border-radius:100px;
height:32px;padding:0 13px;box-sizing:border-box;white-space:nowrap}
#ifc-bar .ghbtn:hover{border-color:var(--accent);background:var(--accent-soft);color:var(--accent-ink)}
#ifc-bar .ghbtn svg{width:14px;height:14px;fill:currentColor;flex:none}
/* the notes/to-do drawer, injected on non-dashboard pages */
.notepanel{position:fixed;top:0;right:0;bottom:0;width:min(460px,92vw);z-index:600;display:flex;flex-direction:column;
background:var(--surface);border-left:1px solid var(--line);box-shadow:-18px 0 44px rgba(0,0,0,.22)}
.notepanel[hidden]{display:none}
.np-head{display:flex;align-items:center;gap:9px;padding:12px 14px;border-bottom:1px solid var(--line);flex:none}
.np-emo{flex:none}.np-title{font-weight:700;font-size:.9rem}
.np-file{font-family:var(--font-mono);font-size:.66rem;color:var(--ink-mut);background:var(--surface-2);
border:1px solid var(--line);border-radius:5px;padding:2px 7px;white-space:nowrap}
.np-status{font-family:var(--font-mono);font-size:.66rem;color:var(--ink-mut);margin-left:auto;white-space:nowrap}
.np-close{font-size:1.05rem;border:1px solid var(--line);background:var(--surface-2);color:var(--ink-mut);
border-radius:6px;width:28px;height:28px;cursor:pointer;line-height:1;padding:0;flex:none}
.np-close:hover{color:#c23b3b;border-color:#c23b3b}
.notepanel #npText{flex:1;width:100%;resize:none;border:0;outline:0;background:transparent;color:var(--ink);
padding:14px 16px;font-family:var(--font-mono);font-size:.82rem;line-height:1.6}
.np-todo{flex:1;display:flex;flex-direction:column;min-height:0}.np-todo[hidden]{display:none}
.np-add{padding:12px 14px;border-bottom:1px solid var(--line);flex:none}
.np-add input{width:100%;font-family:var(--font-mono);font-size:.8rem;padding:8px 11px;outline:none;
border:1px solid var(--line);border-radius:7px;background:var(--surface-2);color:var(--ink)}
.np-add input:focus{border-color:var(--accent)}
.np-items{flex:1;overflow-y:auto;padding:6px 8px}
.np-it{display:flex;align-items:flex-start;gap:9px;padding:7px 9px;border-radius:7px}
.np-it:hover{background:var(--surface-2)}
.np-it input[type=checkbox]{margin-top:2px;accent-color:var(--accent);width:15px;height:15px;flex:none;cursor:pointer}
.np-it .t{flex:1;font-size:.84rem;line-height:1.45;word-break:break-word}
.np-it.done .t{color:var(--ink-mut);text-decoration:line-through}
.np-tid{font-family:var(--font-mono,ui-monospace,monospace);font-size:.78rem;color:var(--accent);
  text-decoration:none;border-bottom:1px dotted currentColor;white-space:nowrap}
.np-tid:hover{opacity:.72}
.np-tid.done{color:var(--done)}
.np-tid.done::after{content:" ✓"}
.np-it .del{opacity:0;font-family:var(--font-mono);font-size:.66rem;border:1px solid var(--line);
background:var(--surface-2);border-radius:5px;cursor:pointer;color:var(--ink-mut);padding:1px 6px}
.np-it:hover .del,.np-it .del:focus-visible{opacity:1}
.np-empty{font-size:.82rem;color:var(--ink-mut);padding:10px 9px;margin:0}
.np-done-h{font-family:var(--font-mono);font-size:.62rem;letter-spacing:.08em;text-transform:uppercase;
color:var(--ink-mut);padding:12px 9px 4px;border-top:1px solid var(--line);margin-top:8px}
.np-foot{flex:none;padding:9px 14px;border-top:1px solid var(--line)}
.np-foot button{font-family:var(--font-mono);font-size:.68rem;color:var(--ink-mut);background:none;border:0;cursor:pointer;padding:0}
.np-foot button:hover{color:var(--accent-ink)}
.np-ico{font-size:.9rem;border:1px solid var(--line);background:var(--surface-2);color:var(--ink-mut);
border-radius:6px;width:28px;height:28px;cursor:pointer;line-height:1;padding:0;flex:none}
.np-ico:hover{border-color:var(--accent);color:var(--accent-ink)}
.np-ico.on{border-color:var(--accent);color:var(--accent-ink);background:var(--accent-soft)}
.np-ico[hidden]{display:none}
.np-prev{flex:1;overflow-y:auto;padding:14px 16px;font-size:.86rem;line-height:1.6;color:var(--ink)}
.np-prev[hidden]{display:none}
.np-prev>*:first-child{margin-top:0}
.np-prev h1,.np-prev h2,.np-prev h3,.np-prev h4{margin:1.1em 0 .4em;line-height:1.25}
.np-prev h1{font-size:1.14rem}.np-prev h2{font-size:1.02rem}.np-prev h3{font-size:.93rem}
.np-prev p{margin:.6em 0}
.np-prev ul,.np-prev ol{margin:.6em 0;padding-left:1.35em}
.np-prev li{margin:.25em 0}
.np-prev a{color:var(--accent-ink)}
.np-prev code{font-family:var(--font-mono);font-size:.82em;background:var(--surface-2);
border:1px solid var(--line);border-radius:4px;padding:1px 5px}
.np-prev pre{background:var(--surface-2);border:1px solid var(--line);border-radius:7px;padding:10px 12px;overflow-x:auto}
.np-prev pre code{border:0;background:none;padding:0}
.np-prev blockquote{margin:.6em 0;padding-left:12px;border-left:3px solid var(--line);color:var(--ink-mut)}
.np-prev table{border-collapse:collapse;width:100%;font-size:.8rem;margin:.6em 0}
.np-prev td,.np-prev th{border:1px solid var(--line);padding:5px 8px;text-align:left}
.np-prev img{max-width:100%}
.np-prev hr{border:0;border-top:1px solid var(--line);margin:1em 0}
"""

_SWITCHER_JS = """
(function(){
var cur=document.getElementById('ifc-cur'),menu=document.getElementById('ifc-menu');
function set(o){if(!menu)return;menu.classList.toggle('open',o);
cur.setAttribute('aria-expanded',o?'true':'false');}
function go(slug){document.cookie='ifc='+encodeURIComponent(slug)+';path=/;max-age=31536000';
location.href='/';}
if(cur&&menu){
cur.addEventListener('click',function(e){e.stopPropagation();set(!menu.classList.contains('open'));});
menu.addEventListener('click',function(e){var li=e.target.closest('li[data-slug]');
if(li&&!justDragged())go(li.getAttribute('data-slug'));});
document.addEventListener('click',function(){set(false);});
}
/* drag an interface to a new spot; the first ten positions get keys 1-9, 0.
   The order is saved to the registry, so it survives restarts. */
var dragging=null,dragEnd=0;
function justDragged(){return Date.now()-dragEnd<400;}
if(menu&&menu.querySelector('li[draggable]')){
menu.addEventListener('dragstart',function(e){var li=e.target.closest('li[data-slug]');
if(!li)return;dragging=li;li.classList.add('ifc-drag');
e.dataTransfer.effectAllowed='move';
try{e.dataTransfer.setData('text/plain',li.getAttribute('data-slug'));}catch(err){}});
menu.addEventListener('dragover',function(e){if(!dragging)return;e.preventDefault();
e.dataTransfer.dropEffect='move';
var li=e.target.closest('li[data-slug]');if(!li||li===dragging)return;
var r=li.getBoundingClientRect();
li.parentNode.insertBefore(dragging,(e.clientY-r.top)<r.height/2?li:li.nextSibling);});
menu.addEventListener('drop',function(e){e.preventDefault();});
menu.addEventListener('dragend',function(){if(!dragging)return;
dragging.classList.remove('ifc-drag');dragging=null;dragEnd=Date.now();
var slugs=Array.prototype.map.call(menu.querySelectorAll('li[data-slug]'),
function(li){return li.getAttribute('data-slug');});
fetch('/api/ifc-order',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({slugs:slugs})}).then(function(){location.reload();});});
}
/* per-interface shortcut key switches from anywhere (ignored while typing) */
var keymap={};
if(menu)Array.prototype.forEach.call(menu.querySelectorAll('li[data-key]'),function(li){
var k=li.getAttribute('data-key');if(k)keymap[k.toLowerCase()]=li.getAttribute('data-slug');});
document.addEventListener('keydown',function(e){
if(e.key==='Escape'){set(false);return;}
if(e.ctrlKey||e.metaKey||e.altKey)return;
var t=e.target,tn=t&&t.tagName;
if(tn==='INPUT'||tn==='TEXTAREA'||tn==='SELECT'||(t&&t.isContentEditable))return;
var slug=keymap[(e.key||'').toLowerCase()];
if(slug){e.preventDefault();go(slug);}
});
/* click-outside-to-close now lives in the notes module itself, so it can honour
   the "keep open" eye. */
function relocate(){
/* multi-interface: GitHub info goes to the centre of the sticky switcher bar;
   project links + notes/to-do go to the right slot (links kept left of the
   pin/scratchpad). Regenerate deliberately stays in the card header, top row,
   across from the eyebrow — so it and the icons read as swapped. */
var acts=document.getElementById('ifc-actions');
if(!acts)return;
var mid=document.getElementById('ifc-center');
/* Move a control into its slot — but never touch one the bar already rendered
   there. appendChild is a *move*: re-appending a sub-page's own pocket dragged
   it to the end of the row, which is why the order differed from the board's.
   Placement is stated, not left to whatever order the calls happen to run in. */
function place(el,slot,first){
  if(!el||!slot||slot.contains(el))return;
  if(first&&slot.firstChild)slot.insertBefore(el,slot.firstChild);
  else slot.appendChild(el);
}
place(document.getElementById('ghBtn'),mid);                 /* centre */
place(document.getElementById('todayPocket'),acts,true);     /* right, leading */
place(document.getElementById('projLinks'),acts);
place(document.querySelector('.note-btns'),acts);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',relocate);
else relocate();
})();
"""


# The notes/to-do drawer + its logic, reused verbatim on every non-dashboard page
# so the pin/scratchpad/to-do buttons "come with you". The dashboard keeps its own
# inline copy; these are injected only on sub-pages (see _transform_html).
_NOTES_PANEL_HTML = (
    '<aside class="notepanel" id="notePanel" hidden aria-label="Notes pop-out">'
    '<div class="np-head"><span class="np-emo" id="npEmo">&#128221;</span>'
    '<span class="np-title" id="npTitle">Notes</span>'
    '<span class="np-file" id="npFile">.interfacile/scratchpad.md</span>'
    '<span class="np-status" id="npStatus"></span>'
    '<button class="np-ico" id="npPrev" type="button" title="Preview markdown"'
    ' aria-label="Preview markdown">&#128196;</button>'
    '<button class="np-ico" id="npKeep" type="button" aria-pressed="false"'
    ' title="Keep open while you navigate">&#128065;&#65039;</button>'
    '<button class="np-close" id="npClose" type="button" aria-label="Close notes">&times;</button></div>'
    '<textarea id="npText" spellcheck="false" placeholder="Jot it down &mdash; autosaves to a '
    'local file the server reads back next run. Not tracked in git."></textarea>'
    '<div class="np-prev" id="npPrevBox" hidden></div>'
    '<div class="np-todo" id="npTodo" hidden><form class="np-add" id="npAdd">'
    '<input id="npAddIn" type="text" autocomplete="off" spellcheck="false" '
    'placeholder="Add a to-do &mdash; Enter to save"></form>'
    '<div class="np-items" id="npItems"></div>'
    '<div class="np-foot"><button type="button" id="npDoneTgl" hidden></button></div></div></aside>'
)

_NOTES_JS = r"""
(function(){
  var panel=document.getElementById("notePanel");if(!panel)return;
  var ta=document.getElementById("npText"),
      todoBox=document.getElementById("npTodo"),itemsEl=document.getElementById("npItems"),
      addForm=document.getElementById("npAdd"),addIn=document.getElementById("npAddIn"),
      doneTgl=document.getElementById("npDoneTgl"),
      emo=document.getElementById("npEmo"),title=document.getElementById("npTitle"),
      file=document.getElementById("npFile"),status=document.getElementById("npStatus"),
      btns=document.querySelectorAll(".notebtn"),
      prevBtn=document.getElementById("npPrev"),prevBox=document.getElementById("npPrevBox"),
      keepBtn=document.getElementById("npKeep"),
      META={scratch:{emo:"📝",title:"Notes",file:".interfacile/scratchpad.md"},
            todo:{emo:"📌",title:"To-do",file:".interfacile/todo.md"}},
      cur=null,loaded="",timer=null,items=[],showDone=false,preview=false;
  var LS={get:function(k){try{return localStorage.getItem(k);}catch(e){return null;}},
          set:function(k,v){try{localStorage.setItem(k,v);}catch(e){}}};
  /* the eye: while on, clicking outside will NOT close the pop-out, and it
     re-opens itself on the next page — so it follows you around the site. */
  var keep=LS.get("ifcNotesKeep")==="1";
  function setKeep(on){
    keep=!!on;
    keepBtn.classList.toggle("on",keep);
    keepBtn.setAttribute("aria-pressed",keep?"true":"false");
    keepBtn.title=keep?"Staying open as you navigate — click to release"
                      :"Keep open while you navigate";
    LS.set("ifcNotesKeep",keep?"1":"0");
  }
  /* markdown preview of the scratchpad, rendered by the server */
  function setPrev(on){
    preview=!!on&&cur==="scratch";
    prevBtn.classList.toggle("on",preview);
    ta.hidden=preview||cur==="todo";
    prevBox.hidden=!preview;
    if(!preview)return;
    prevBox.innerHTML='<p class="np-empty">rendering…</p>';
    fetch("/api/render",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({content:ta.value})})
      .then(function(r){return r.json();})
      .then(function(j){prevBox.innerHTML=(j.ok&&j.html)?j.html
        :'<p class="np-empty">Nothing to preview.</p>';})
      .catch(function(){prevBox.innerHTML='<p class="np-empty">preview failed</p>';});
  }
  function setStatus(s){status.textContent=s;}
  function markBtns(){btns.forEach(function(b){b.classList.toggle("active",!panel.hidden&&b.dataset.which===cur);});}
  function parseTodo(s){
    var out=[];
    s.split("\n").forEach(function(ln){
      var t=ln.trim();if(!t)return;
      var m=t.match(/^[-*]\s*\[([ xX])\]\s*(.*)$/);
      if(m)out.push({done:m[1].toLowerCase()==="x",text:m[2]});
      else out.push({done:false,text:t.replace(/^[-*]\s+/,"")});
    });
    return out;
  }
  function serializeTodo(list){
    return list.length?list.map(function(i){return "- ["+(i.done?"x":" ")+"] "+i.text;}).join("\n")+"\n":"";
  }
  function content(){return cur==="todo"?serializeTodo(items):ta.value;}
  /* A captured item carries the id of the ticket it became. Resolve it against
     the board (fetched once, lazily) so the item shows what became of it and
     clicks through — the note stores the id, never the status, so this can't go
     stale. Only ids the board knows become links, so a link never 404s. */
  var TID=/\b[A-Za-z][A-Za-z0-9]*-[\w.\-]*\d[\w.\-]*\b/g,IDX=null;
  function loadIdx(){
    if(IDX)return;
    fetch("/api/ids",{cache:"no-store"}).then(function(r){return r.json();})
      .then(function(j){IDX=(j&&!j.error)?j:{};if(cur==="todo")renderTodo();})
      .catch(function(){IDX={};});
  }
  function tidLink(id){
    var m=IDX[id],a=document.createElement("a");
    a.className="np-tid"+(m.status==="OPEN"?"":" done");
    a.href="/ticket/"+encodeURIComponent(id);a.textContent=id;
    a.title=m.status+" · "+m.title;
    return a;
  }
  function fillText(el,text){
    el.textContent="";TID.lastIndex=0;
    var last=0,m;
    while((m=TID.exec(text))){
      if(!IDX||!IDX[m[0]])continue;                    /* not a ticket we know */
      if(m.index>last)el.appendChild(document.createTextNode(text.slice(last,m.index)));
      el.appendChild(tidLink(m[0]));last=m.index+m[0].length;
    }
    if(last<text.length)el.appendChild(document.createTextNode(text.slice(last)));
  }
  function todoRow(it){
    var d=document.createElement("div");d.className="np-it"+(it.done?" done":"");
    var cb=document.createElement("input");cb.type="checkbox";cb.checked=it.done;
    cb.addEventListener("change",function(){it.done=cb.checked;renderTodo();flush();});
    var t=document.createElement("span");t.className="t";fillText(t,it.text);
    var del=document.createElement("button");del.type="button";del.className="pin-x del";
    del.textContent="✕";del.title="Delete";
    del.addEventListener("click",function(){items.splice(items.indexOf(it),1);renderTodo();flush();});
    d.appendChild(cb);d.appendChild(t);d.appendChild(del);return d;
  }
  function renderTodo(){
    itemsEl.innerHTML="";
    var open=items.filter(function(i){return !i.done;}),done=items.filter(function(i){return i.done;});
    if(!open.length){var e=document.createElement("p");e.className="np-empty";
      e.textContent=done.length?"All done. 🎉":"Nothing yet — add one above.";itemsEl.appendChild(e);}
    open.forEach(function(it){itemsEl.appendChild(todoRow(it));});
    if(done.length&&showDone){var h=document.createElement("div");h.className="np-done-h";h.textContent="Done";
      itemsEl.appendChild(h);done.forEach(function(it){itemsEl.appendChild(todoRow(it));});}
    doneTgl.hidden=!done.length;doneTgl.textContent=(showDone?"hide":"show")+" done ("+done.length+")";
  }
  function flush(sync){
    if(timer){clearTimeout(timer);timer=null;}
    if(cur===null||content()===loaded)return;
    var body=JSON.stringify({which:cur,content:content()});loaded=content();
    if(sync&&navigator.sendBeacon){navigator.sendBeacon("/api/note",new Blob([body],{type:"application/json"}));return;}
    setStatus("saving…");
    fetch("/api/note",{method:"POST",headers:{"Content-Type":"application/json"},body:body})
      .then(function(r){return r.json();}).then(function(j){setStatus(j.ok?"saved":"save failed");})
      .catch(function(){setStatus("save failed — server down?");});
  }
  function close(){flush();panel.hidden=true;cur=null;setPrev(false);markBtns();LS.set("ifcNotesOpen","");}
  function open(which){
    if(cur===which){close();return;}
    flush();cur=which;loaded="";preview=false;
    emo.textContent=META[which].emo;title.textContent=META[which].title;file.textContent=META[which].file;
    todoBox.hidden=which!=="todo";panel.hidden=false;
    prevBtn.hidden=which!=="scratch";setPrev(false);markBtns();
    LS.set("ifcNotesOpen",which);
    ta.value="";ta.disabled=true;items=[];showDone=false;setStatus("loading…");
    fetch("/api/note?which="+which).then(function(r){return r.json();}).then(function(j){
      if(cur!==which)return;
      var c=j.ok?j.content:"";loaded=c;setStatus(j.ok?"":"load failed");
      if(which==="todo"){items=parseTodo(c);loadIdx();renderTodo();addIn.value="";addIn.focus();
        if(serializeTodo(items)!==c)flush();}
      else{ta.disabled=false;ta.value=c;ta.focus();}
    }).catch(function(){if(cur!==which)return;ta.disabled=false;setStatus("load failed — server down?");});
  }
  btns.forEach(function(b){b.addEventListener("click",function(){open(b.dataset.which);});});
  document.getElementById("npClose").addEventListener("click",close);
  addForm.addEventListener("submit",function(e){e.preventDefault();var t=addIn.value.trim();if(!t)return;
    items.push({done:false,text:t});addIn.value="";renderTodo();flush();});
  doneTgl.addEventListener("click",function(){showDone=!showDone;renderTodo();});
  ta.addEventListener("input",function(){setStatus("…");if(timer)clearTimeout(timer);
    timer=setTimeout(function(){flush();},700);});
  ta.addEventListener("blur",function(){flush();});
  ta.addEventListener("keydown",function(e){if((e.metaKey||e.ctrlKey)&&e.key==="s"){e.preventDefault();flush();}});
  document.addEventListener("keydown",function(e){if(e.key==="Escape"&&!panel.hidden)close();});
  window.addEventListener("beforeunload",function(){flush(true);});
  keepBtn.addEventListener("click",function(){setKeep(!keep);});
  prevBtn.addEventListener("click",function(){flush();setPrev(!preview);});
  /* click-outside closes the pop-out — unless the eye is on */
  document.addEventListener("mousedown",function(e){
    if(panel.hidden||keep)return;
    if(panel.contains(e.target))return;
    if(e.target.closest&&e.target.closest(".notebtn"))return;
    close();
  });
  setKeep(keep);
  var was=LS.get("ifcNotesOpen");
  if(keep&&(was==="scratch"||was==="todo"))open(was);   /* follow me across pages */
})();
"""

# One source of truth: the dashboard embeds the same panel + logic the sub-pages
# get injected, so the notes pop-out can never drift between them.
DASHBOARD_HTML = DASHBOARD_HTML.replace("<!--NOTES_PANEL-->", _NOTES_PANEL_HTML, 1)
DASHBOARD_HTML = DASHBOARD_HTML.replace("/*NOTES_JS*/", _NOTES_JS, 1)


def _gh_link_html():
    """A GitHub link for the sticky bar, from this repo's origin remote. '' if none."""
    g = _git_info() or {}
    url = g.get("url")
    if not url:
        return ""
    return ("<a class='ghbtn' href='%s' target='_blank' rel='noopener' title='Open the repository'>"
            "<svg viewBox='0 0 16 16' aria-hidden='true'><path d='%s'/></svg>GitHub</a>"
            % (html.escape(url, quote=True), _GH_PATH))


def _persist_controls_html():
    """(centre, right) markup for the bar on non-dashboard pages: the pocket and
    GitHub centred, then project links + the notes/to-do buttons on the right —
    so the whole set follows you from page to page."""
    notes = ("<span class='note-btns'>"
             "<button class='notebtn nb-notes' type='button' data-which='scratch' aria-label='Open scratchpad'>"
             "<span class='pl-emo'>&#128221;</span><span class='pl-tip' role='tooltip'>Scratchpad</span></button>"
             "<button class='notebtn nb-todo' type='button' data-which='todo' aria-label='Open to-do list'>"
             "<span class='pl-emo'>&#128204;</span><span class='pl-tip' role='tooltip'>To-do list</span></button>"
             "</span>")
    links = "<span class='proj-links'>" + _LINK_ADD_BTN + render_project_links() + "</span>"
    # The pocket is a pair of links into ticket lists — it belongs with the
    # ticket controls on the right, not with GitHub. GitHub keeps the centre.
    return _gh_link_html(), pocket_html() + links + notes


def _switcher_html(is_dash=True):
    """Top-left sticky bar, identical on every page so nothing jumps around: the
    interface dropdown is always leftmost, then (on non-dashboard pages) a
    prominent "← Dashboard" button back to *this* interface's board, then a
    right-hand slot for the page's GitHub + Regenerate controls (relocated by JS).
    '' when there is only one interface."""
    if len(INTERFACES) <= 1:
        return ""
    home = ("" if is_dash else
            "<a id='ifc-home' href='/' title='Back to dashboard'>&larr; Dashboard</a>")
    active = _IFACE_BY_SLUG.get(ACTIVE_SLUG) or INTERFACES[0]

    def _sub(it):
        pfx = (it.conf.get("ids", {}) or {}).get("prefix", "")
        base = os.path.basename(it.root.rstrip("/"))
        return html.escape(base + (" · " + pfx if pfx else ""))

    def _name(it):  # project name, emoji AFTER it
        return ("<span class='ifc-n'>%s<span class='ifc-i'>%s</span></span>"
                % (html.escape(it.name), html.escape(it.icon or "")))

    # Registry-driven hubs can be reordered by dragging; the keys follow the
    # order (first ten get 1-9 then 0), so there is nothing to configure.
    drag = " draggable='true' title='drag to reorder'" if REGISTRY_FILE else ""
    rows = []
    for it in INTERFACES:
        kbd = ("<span class='ifc-kbd'>%s</span>" % html.escape(it.shortcut)) if it.shortcut else ""
        rows.append(
            "<li role='option' data-slug='%s'%s%s%s>"
            "<span class='ifc-txt'>%s<span class='ifc-sub'>%s</span></span>%s"
            "<span class='ifc-chk' aria-hidden='true'>&#10003;</span></li>"
            % (html.escape(it.slug),
               " data-key='%s'" % html.escape(it.shortcut) if it.shortcut else "",
               " aria-selected='true'" if it.slug == ACTIVE_SLUG else "",
               drag, _name(it), _sub(it), kbd))
    hint = ("<span class='ifc-hint'>drag to reorder &middot; keys follow</span>"
            if REGISTRY_FILE else "")
    # On the dashboard these stay empty (its own controls relocate here via JS);
    # on every other page we render github + links + notes so they follow you.
    center, actions = ("", "")
    if not is_dash:
        center, actions = _persist_controls_html()
    return (
        "<div id='ifc-bar'><div id='ifc-switch'>"
        "<button id='ifc-cur' type='button' aria-haspopup='listbox' aria-expanded='false'>"
        + _name(active)
        + "<span class='ifc-caret' aria-hidden='true'>&#9662;</span></button>"
        "<ul id='ifc-menu' role='listbox'>"
        "<li class='ifc-hd' aria-hidden='true' style='cursor:default'>Switch interface"
        + hint + "</li>"
        + "".join(rows) + "</ul>"
        "</div>" + home
        + "<div id='ifc-center'>" + center + "</div>"
        + "<div id='ifc-actions'>" + actions + "</div></div>"
        "<style>" + _SWITCHER_CSS + "</style><script>" + _SWITCHER_JS
        # The dashboard fills its pocket from the board payload it fetches
        # anyway; every other page asks for just the counts.
        + ("" if is_dash else _BAR_POCKET_JS) + "</script>"
    )


_BAR_POCKET_JS = """
fetch('/api/pocket',{cache:'no-store'}).then(function(r){return r.json();})
  .then(function(j){if(window.ifcPocket&&j&&!j.error)window.ifcPocket(j);})
  .catch(function(){});
"""

_BODY_OPEN_RE = re.compile(r"(<body[^>]*>)", re.I)


def run(repo_roots, port=None, host="127.0.0.1", open_browser=True,
        registry_file=None):
    """Serve one or more repos from a single process. >1 repo shows the switcher.
    This is the heart the CLI (`interfacile serve` / `interfacile hub`) drives.
    When the hub was launched from the registry (rather than explicit --repo
    flags), pass `registry_file` and later registrations show up live."""
    global REGISTRY_FILE, _REGISTRY_MTIME
    REGISTRY_FILE = registry_file
    if registry_file:
        try:
            _REGISTRY_MTIME = os.path.getmtime(registry_file)
        except OSError:
            _REGISTRY_MTIME = 0.0
    build_registry(repo_roots)
    for it in INTERFACES:
        if not os.path.isdir(os.path.join(it.root, "tickets")):
            sys.exit("tickets/ not found at %s" % os.path.join(it.root, "tickets"))
        migrate_state(it.root)       # fold any legacy flat state into .interfacile/

    activate(INTERFACES[0])       # startup default; each request re-activates

    p = port if port is not None else int(os.environ.get("PORT", SERVER_PORT))
    # Pretty, branded loopback host. Browsers resolve *.localhost to 127.0.0.1,
    # so no /etc/hosts edit is needed; plain localhost is printed as a fallback.
    loopback = host in ("127.0.0.1", "0.0.0.0", "localhost", "::1")
    if loopback:
        brand_host = ("interfacile.localhost" if len(INTERFACES) > 1
                      else INTERFACES[0].slug + ".localhost")
        url = "http://%s:%d/" % (brand_host, p)
        plain = "http://localhost:%d/" % p
    else:
        url = "http://%s:%d/" % (host, p)
        plain = None
    srv = ThreadingHTTPServer((host, p), Handler)
    if len(INTERFACES) > 1:
        print("interfacile hub  ->  %s" % url, flush=True)
        for it in INTERFACES:
            print("  %s %-16s %s" % (it.icon, it.slug, it.root), flush=True)
    else:
        print("%s · interfacile  ->  %s" % (BRAND, url), flush=True)
        print("scanning: " + TICKETS_DIR, flush=True)
    if plain:
        print("     also:  %s" % plain, flush=True)
    print("Ctrl-C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        srv.shutdown()


def main(argv=None):
    """Flat CLI for `python -m interfacile.server`; the real UX is interfacile.cli."""
    ap = argparse.ArgumentParser(description="interfacile — ticket-portfolio dashboard.")
    ap.add_argument("--port", type=int, default=None,
                    help="listen port (overrides .interfacile/config.json; default 8787)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", action="store_true", help="do not auto-open a browser")
    ap.add_argument("--repo", action="append", default=None, metavar="PATH",
                    help="repo root to scan. Repeat to serve several interfaces "
                         "from one process (switcher order = flag order).")
    args = ap.parse_args(argv)
    default_root = os.environ.get("TICKET_DASHBOARD_REPO") or REPO_ROOT
    run(args.repo or [default_root], args.port, args.host, not args.no_open)


if __name__ == "__main__":
    main()

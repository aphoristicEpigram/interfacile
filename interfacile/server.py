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

EPIC_TITLES = {
    "E001": "PII Detection Core",
    "E002": "Product Surface & MCP",
    "E003": "Swift macOS App",
    "E004": "Launch Readiness",
    "E005": "Rust Production Port",
    "E006": "Developer Marketing",
    "E007": "Gazetteer & Detection Data",
    "E008": "Native Swift Layer",
    "E009": "Engine Architecture",
    "E010": "Entity Model & Adjacency",
    "E011": "Performance & Stability",
    "E012": "Conflict Resolution",
    "E013": "Commercialisation",
    "E014": "Distribution & Packaging",
    "E015": "Onboarding & Starter Packs",
    "E017": "Document Model & Domains",
    "E018": "Security & Adversarial",
    "E019": "Session Layer",
    "E020": "Process & Ticket Hygiene",
    "E021": "CI & Dev Tooling",
    "E022": "Output Formatting Modes",
    "E023": "Domain Implementations",
}

EPIC_EMOJI = {
    "E001": "🔍", "E002": "🔌", "E003": "🍎", "E004": "🚀", "E005": "🦀",
    "E006": "📣", "E007": "🗺️", "E008": "🐦", "E009": "⚙️", "E010": "🧩",
    "E011": "⚡", "E012": "⚖️", "E013": "💰", "E014": "📦", "E015": "🧭",
    "E017": "📄", "E018": "🛡️", "E019": "🧵", "E020": "🧹", "E021": "🔧",
    "E022": "🎨", "E023": "🏗️",
}
# --------------------------------------------------------------------------- #
# Per-interface identity. These module-level names are the *live* values the
# rest of the server reads; they start at the built-in defaults below and are
# overwritten from a repo's interfacile.json at startup by apply_config().
# --------------------------------------------------------------------------- #
PFX = "EM"                       # ticket id prefix: <PFX>-1234, <PFX>-E001
ID_DIGITS = 4                    # zero-padded ticket-number width
BRAND = "Clean Paste"            # <h1> / page-title name
FAVICON = "🎟️"                   # browser-tab icon glyph
HEADER_ICON = "🎟️"               # mark shown beside the title (defaults to favicon)
EYEBROW = "Ticket portfolio &middot; engineering program"
TAGLINE = "The PII-firewall build tracked as"
SERVER_PORT = 8787               # default listen port (config/--port override)
THEME_REMAP = {}                 # canonical(blue)->theme hex map; {} == blue
THEME_STRIP = None               # signature-strip colours, or None
THEME_OVERRIDE_CSS = ""          # custom-palette :root override, or ""

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
ADR_FILE_RE = re.compile(r"^ADR-(\d+)")
ADR_LINK_RE = re.compile(r"\bADR-(\d{1,4})\b")


def adr_index():
    """All ADR files plus per-number link targets.

    Returns (records, by_num). records is every ADR-*.md, newest number
    first. by_num maps int number -> /doc path when the number is
    unambiguous, or "" when several files share it (numbering collision:
    bare-text mentions of those link to /adrs so the reader can choose)."""
    recs = []
    for path in sorted(glob.glob(os.path.join(ADR_DIR, "ADR-*.md"))):
        m = ADR_FILE_RE.match(os.path.basename(path))
        if not m:
            continue
        head = read_text(path)[:1500]
        tm = re.search(r"^#\s+ADR-\d+\s*[:—–-]*\s*(.*)$", head, re.M)
        sm = re.search(r"\*\*Status:\*\*\s*([A-Za-z][\w /-]*)", head)
        dm = re.search(r"\*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2})", head)
        recs.append({
            "n": int(m.group(1)),
            "num": "ADR-" + m.group(1),
            "title": (tm.group(1).strip() if tm else os.path.basename(path)),
            "status": (sm.group(1).strip() if sm else ""),
            "date": dm.group(1) if dm else "",
            "path": os.path.relpath(path, REPO_ROOT),
        })
    recs.sort(key=lambda r: (-r["n"], r["path"]))
    by_num = {}
    for r in recs:
        by_num[r["n"]] = "" if r["n"] in by_num else r["path"]
    return recs, by_num


# Pinned tickets: id -> ISO timestamp of when it was pinned. Lives outside
# tickets/ so pinning never dirties a ticket file (which would flag it WIP).
PINS_FILE = os.path.join(REPO_ROOT, ".ticket-pins.json")


def load_pins():
    try:
        with open(PINS_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_pins(pins):
    tmp = PINS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(pins, fh, indent=1, sort_keys=True)
    os.replace(tmp, PINS_FILE)


# Scratch pad / to-do pop-outs: two free-form files at the repo root, outside
# git (see .gitignore). No history, no schema — the dashboard just reads and
# rewrites the whole file, so whatever was there greets you on the next run.
NOTE_FILES = {
    "scratch": os.path.join(REPO_ROOT, ".scratchpad.md"),
    "todo": os.path.join(REPO_ROOT, ".todo.md"),
}


def load_note(which):
    try:
        with open(NOTE_FILES[which], encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def save_note(which, content):
    path = NOTE_FILES[which]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    os.replace(tmp, path)


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


def scan():
    """Walk tickets/, aggregate per-epic counts, and build a weekly time-series."""
    epics = {}
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
                "pinned": tid in pins}
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
        ep = epics.setdefault(code, {
            "id": code, "title": EPIC_TITLES.get(code, code),
            "emoji": EPIC_EMOJI.get(code, "🎟️"),
            "open": 0, "closed": 0, "wf": 0, "standing": 0,
            "openTickets": [], "closedTickets": [], "wfTickets": [],
            "standingTickets": [],
            "lastCreated": None, "lastClosed": None,
            "effortOpenH": 0.0, "effortDoneH": 0.0,
        })
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

        if cdate:
            all_tix.append({"id": tid, "title": title, "created": _iso(cdate),
                            "status": status, "risk": meta["risk"],
                            "priority": meta["priority"], "effort": meta["effort"],
                            "effortH": effort_h, "epic": code})
            all_created.append(cdate)
            if ep["lastCreated"] is None or cdate > ep["lastCreated"]:
                ep["lastCreated"] = cdate
        if xdate and status == "CLOSED":
            all_closed.append(xdate)
            closed_effort.append((xdate, effort_h or 0.0))
            if ep["lastClosed"] is None or xdate > ep["lastClosed"]:
                ep["lastClosed"] = xdate

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
        "adrs": adr_index()[0],
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


def autolink_ids(html_text, known_ids=None, known_epics=None, adrs=None):
    """Hyperlink EM-#### ticket ids, EM-E### epic codes, and ADR-### numbers
    in rendered HTML.

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

    out = []
    for part in _SKIP_SEGMENT_RE.split(html_text):
        if part.startswith("<a") or part.startswith("<pre"):
            out.append(part)
        else:
            part = EPIC_LINK_RE.sub(_sub_epic, part)
            if adrs:
                part = ADR_LINK_RE.sub(_sub_adr, part)
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
        fm = {k: v for k, v in
              frontmatter_scalars(split_frontmatter(read_text(id_index[kid]))[0])}
        kids.append((kid, fm.get("status", "").upper(),
                     fm.get("title", "").strip().strip('"') or kid,
                     len(kids_of.get(kid, []))))
    return nearest(segs), kids


def _family_block(kids):
    """Sub-ticket table for the ticket page. Empty string when there are none."""
    if not kids:
        return ""
    rows = ""
    for kid, status, title, n_sub in kids:
        sub = (" <span class='subn'>%d sub</span>" % n_sub) if n_sub else ""
        rows += (
            "<tr><td class='k'><a href='/ticket/%s'>%s</a></td>"
            "<td class='v'><span class='badge %s'>%s</span> %s%s</td></tr>" % (
                urllib.parse.quote(kid), html.escape(kid),
                STATUS_BADGE.get(status, "b-wf"), html.escape(status or "?"),
                html.escape(title), sub))
    return ("<div class='fm fam'><div class='fm-h'>sub-tickets (%d)</div>"
            "<table><tbody>%s</tbody></table></div>" % (len(kids), rows))


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
                      + '">epic EM-' + code + ' &rarr;</a>')

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
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎟️</text></svg>\">"
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
        + ("1" if pinned else "0") + "'>&#128278;</button></div>"
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


def render_doc_page(path, known_ids=None, known_epics=None, adrs=None):
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
    rel = html.escape(os.path.relpath(path, REPO_ROOT))
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>" + html.escape(os.path.basename(path)) + " &middot; doc</title>"
        "<link rel=\"icon\" href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎟️</text></svg>\">"
        "<style>" + TICKET_CSS + "</style></head><body><div class='wrap'>"
        "<a class='back' href='/'>&larr; back to dashboard</a>"
        "<div class='tid'>" + rel + "</div>"
        "<h1 class='title'>" + html.escape(title) + "</h1>"
        + fm_html +
        "<div class='body'>" + body_html + "</div>"
        "<div class='path'>" + rel + "</div>"
        "</div></body></html>"
    )


def render_adr_page(recs, by_num):
    """Index of every architecture decision record, newest first."""
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
        "<title>ADRs &middot; Clean Paste</title>"
        "<link rel=\"icon\" href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎟️</text></svg>\">"
        "<style>" + EPIC_CSS + "</style></head><body><div class='wrap'>"
        "<a class='back' href='/'>&larr; back to dashboard</a>"
        "<div class='ehead'>"
        "<div class='tid'>ARCHITECTURE DECISION RECORDS</div>"
        "<h1 class='title'>ADRs</h1>"
        "<div class='estats'><span><b>" + str(len(recs)) + "</b> records</span>"
        "<span>docs/architecture/adr/</span></div></div>"
        "<div class='cols' style='grid-template-columns:1fr'>"
        "<div class='col'><div class='col-h'><span>Newest first</span>"
        "<span class='cnt'>" + str(len(recs)) + "</span></div>"
        "<ul>" + (lis or '<div class="empty">no ADRs found</div>') + "</ul></div>"
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
.sg-done{background:var(--done)}.sg-open{background:var(--accent)}.sg-wf{background:var(--wf)}
.estats{display:flex;flex-wrap:wrap;gap:6px 26px;font-family:var(--mono);font-size:.78rem;color:var(--mut)}
.estats b{color:var(--ink)}
.cols{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;align-items:start}
@media (max-width:900px){.cols{grid-template-columns:1fr !important}}
.col{background:var(--surface);border:1px solid var(--line);border-radius:9px;overflow:hidden}
.col-h{font-family:var(--mono);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;
padding:10px 14px;border-bottom:1px solid var(--line);background:var(--surface2);display:flex;justify-content:space-between}
.col-h .cnt{color:var(--ink)}
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
/* card view — single-status filter pages get roomier tickets in a grid */
.col.cards{background:transparent;border:0;overflow:visible}
.col.cards .col-h{background:var(--surface2);border:1px solid var(--line);border-radius:7px}
/* masonry columns: cards keep their natural height, so a tall family never
   stretches the tickets beside it */
.col.cards>ul{columns:330px;column-gap:12px;padding:12px 0}
.col.cards>ul>li{break-inside:avoid;margin:0 0 12px}
.col.cards li a{display:flex;flex-direction:column;align-items:stretch;gap:8px;
background:var(--surface);border:1px solid var(--line);border-radius:9px;padding:14px 16px}
.col.cards li a:hover{border-color:var(--accent);background:var(--surface)}
/* family card: parent + its EM-####-X sub-tickets joined in one cell */
.col.cards li.fam{border:1px solid var(--line);border-radius:9px;background:var(--surface);overflow:hidden}
.col.cards li.fam ul{display:block;margin:0;padding:0}
.col.cards li.fam li a{border:0;border-radius:0;height:auto}
.col.cards li.fam li:not(:first-child) a{border-top:1px solid var(--line)}
.col.cards li.fam li.child a{border-left:3px solid var(--accent);padding-left:13px}
.col.cards li.fam li a:hover{background:var(--surface2)}
.col.cards li.fam:not(:has(li[data-id]:not([hidden]))){display:none}
.card-top{display:flex;align-items:baseline;gap:8px}
.card-top .tk-id{min-width:0;font-size:.76rem}
.card-top .tk-date{margin-left:auto}
.card-ttl{font-size:.9rem;color:var(--ink);line-height:1.45;flex:1;text-wrap:pretty}
.col-closed.cards .card-ttl,.col-wf.cards .card-ttl{color:var(--ink2)}
.card-meta{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin-top:auto}
.card-sub{font-family:var(--mono);font-size:.66rem;color:var(--mut)}
"""


# Shared client-side filter bar for the epic + filtered-list pages. Mirrors the
# dashboard backlog controls (search / risk / priority / effort / blocked).
LIST_FILTER_BAR = """
<div class="fbar">
  <input id="lfSearch" class="searchbox" type="search" placeholder="search EM-#### or title"
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
  <label class="sortlab">sort <select id="lfSort" class="sortsel">
    <option value="">default</option><option value="risk">by risk</option>
    <option value="priority">by priority</option>
    <option value="effort">by effort</option></select></label>
  <button type="button" class="lnk" id="lfClear">clear</button>
</div>"""

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
  function attrKey(li,mode){
    if(mode==='risk'){var r=RISK_ORD[li.getAttribute('data-risk')];return r==null?9:r;}
    if(mode==='priority'){return parseInt(li.getAttribute('data-prio'),10)||9;}
    var h=li.getAttribute('data-effh');
    return (h===''||h==null)?1e9:parseFloat(h);
  }
  function liKey(li,mode){
    if(li.hasAttribute('data-id'))return attrKey(li,mode);
    var best=1e18; // .fam container: best value of its members
    li.querySelectorAll('li[data-id]').forEach(function(x){
      var k=attrKey(x,mode);if(k<best)best=k;});
    return best;
  }
  function applySort(){
    var mode=srt.value;
    uls.forEach(function(u){
      var groups=[],cur=null;
      u.orig.forEach(function(li){
        var fam=!li.hasAttribute('data-id');
        if(fam||!li.classList.contains('child')||!cur){cur=[];groups.push(cur);}
        cur.push(li);
        if(fam)cur=null;
      });
      if(mode){
        groups=groups.slice().sort(function(a,b){
          var ka=Math.min.apply(null,a.map(function(li){return liKey(li,mode);}));
          var kb=Math.min.apply(null,b.map(function(li){return liKey(li,mode);}));
          return ka-kb;
        });
      }
      groups.forEach(function(g){g.forEach(function(li){u.ul.appendChild(li);});});
    });
  }
  srt.addEventListener('change',applySort);
  [q,risk,prio,eff,blk].forEach(function(el){
    el.addEventListener('input',apply);el.addEventListener('change',apply);});
  q.addEventListener('keydown',function(e){if(e.key==='Escape'){q.value='';apply();}});
  document.getElementById('lfClear').addEventListener('click',function(){
    q.value='';risk.value='';prio.value='';eff.value='';blk.value='';
    srt.value='';applySort();apply();});
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
  // pinned chips unpin in place (they sit inside ticket links)
  document.addEventListener('click',function(ev){
    var ch=ev.target.closest('.mchip.pinned[data-unpin]');if(!ch)return;
    ev.preventDefault();ev.stopPropagation();
    fetch('/api/pin',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:ch.getAttribute('data-unpin'),pinned:false})})
      .then(function(){location.reload();});
  },true);
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

    out = []
    for root in sorted(groups, key=gdate, reverse=reverse):
        g = sorted(groups[root], key=lambda t: t["id"])
        for t in g:
            out.append((t, len(g) > 1 and bool(SUB_ID_RE.match(t["id"]))))
    return out


def _epic_ticket_li(t, is_child, datekey, today, show_epic=False):
    chips = ""
    if show_epic and t.get("epic"):
        ec = html.escape(t["epic"])
        chips += ('<span class="mchip epic-link" role="link" tabindex="0" data-epic="%s"'
                  ' title="open the EM-%s epic page">EM-%s</span>' % (ec, ec, ec))
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
    if t.get("pinned"):
        chips += _pin_chip(t["id"])
    date = t.get(datekey) or ""
    dlab = date
    if datekey == "created" and date:
        d = parse_date(date)
        if d:
            dlab = "%s · %dd old" % (date, (today - d).days)
    return ('<li class="%s"%s><a href="/ticket/%s">'
            '<span class="tk-id">%s</span><span class="tk-ttl">%s</span>'
            '<span class="tk-meta">%s%s</span></a></li>' % (
                "child" if is_child else "", _li_attrs(t), html.escape(t["id"]),
                html.escape(t["id"]), html.escape(t["title"]), chips,
                ('<span class="tk-date">%s</span>' % html.escape(dlab)) if dlab else ""))


def _pin_chip(tid):
    return ('<span class="mchip pinned" role="button" tabindex="0" data-unpin="%s"'
            ' title="pinned — click to unpin">&#128278;</span>'
            % html.escape(tid))


def _li_attrs(t):
    """data-* attributes the shared client-side filter/sort script reads."""
    return (' data-id="%s" data-risk="%s" data-prio="%s" data-eff="%s"'
            ' data-effh="%s" data-blocked="%s" data-search="%s"' % (
                html.escape(t["id"]), html.escape(t.get("risk") or ""),
                html.escape(str(t.get("priority") or "")),
                _effort_bucket(t.get("effortH")),
                "" if t.get("effortH") is None else ("%g" % t["effortH"]),
                "y" if t.get("blocked") else "n",
                html.escape((t["id"] + " " + t["title"]).lower())))


def _ticket_card(t, is_child, datekey, today, show_epic=True):
    """Roomier card rendering of one ticket, same data-* contract as the li."""
    chips = ""
    if show_epic and t.get("epic"):
        ec = html.escape(t["epic"])
        emo = EPIC_EMOJI.get(t["epic"], "")
        chips += ('<span class="mchip epic-link" role="link" tabindex="0" data-epic="%s"'
                  ' title="open the EM-%s epic page">%sEM-%s</span>'
                  % (ec, ec, (emo + " ") if emo else "", ec))
    if t.get("risk"):
        chips += ('<span class="mchip r-%s">%s risk</span>'
                  % (t["risk"].lower(), t["risk"].lower()))
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
    if t.get("pinned"):
        chips += _pin_chip(t["id"])
    date = t.get(datekey) or ""
    dlab = date
    if datekey == "created" and date:
        d = parse_date(date)
        if d:
            dlab = "created %s · %dd old" % (date, (today - d).days)
    elif date:
        dlab = "closed %s" % date
    sub = ""
    m = SUB_ID_RE.match(t["id"])
    if is_child and m:
        sub = ('<span class="card-sub">&#8627; sub-ticket of %s</span>'
               % html.escape(m.group(1)))
    return ('<li class="%s"%s><a href="/ticket/%s">'
            '<span class="card-top"><span class="tk-id">%s</span>%s</span>'
            '<span class="card-ttl">%s</span>%s'
            '<span class="card-meta">%s</span></a></li>' % (
                "child" if is_child else "", _li_attrs(t), html.escape(t["id"]),
                html.escape(t["id"]),
                ('<span class="tk-date">%s</span>' % html.escape(dlab)) if dlab else "",
                html.escape(t["title"]), sub, chips))


def _card_groups(items, datekey, reverse):
    """[(ticket, is_child)] regrouped into per-root lists, order preserved."""
    out, cur_root = [], None
    for t, child in _epic_group(items, datekey, reverse):
        m = SUB_ID_RE.match(t["id"])
        root = m.group(1) if m else t["id"]
        if root != cur_root:
            out.append([])
            cur_root = root
        out[-1].append((t, child))
    return out


def _status_col(kind, label, items, datekey, reverse, today, show_epic=False,
                cards=False):
    if cards:
        # Compound families (parent + EM-####-X subs) share one joined card
        # so the relationship stays visible in the grid.
        parts = []
        for g in _card_groups(items, datekey, reverse):
            if len(g) == 1:
                parts.append(_ticket_card(g[0][0], False, datekey, today, show_epic))
            else:
                parts.append('<li class="fam"><ul>' + "".join(
                    _ticket_card(t, c, datekey, today, show_epic)
                    for t, c in g) + "</ul></li>")
        lis = "".join(parts)
    else:
        lis = "".join(_epic_ticket_li(t, c, datekey, today, show_epic)
                      for t, c in _epic_group(items, datekey, reverse))
    body = "<ul>" + lis + "</ul>" if lis else '<div class="empty">none</div>'
    return ('<div class="col col-%s%s"><div class="col-h"><span>%s</span>'
            '<span class="cnt">%d</span></div>%s</div>'
            % (kind, " cards" if cards else "", label, len(items), body))


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
    charter = sorted(glob.glob(os.path.join(
        TICKETS_DIR, "EM-%s-*" % ep["id"], "EM-%s-*.md" % ep["id"])))
    charter_link = ""
    if charter:
        charter_link = ('&nbsp;&middot;&nbsp; <a class="back" href="/doc/%s">'
                        'epic charter &rarr;</a>'
                        % html.escape(os.path.relpath(charter[0], REPO_ROOT)))
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>" + html.escape("EM-" + ep["id"]) + " &middot; epic</title>"
        "<link rel=\"icon\" href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎟️</text></svg>\">"
        "<style>" + EPIC_CSS + "</style></head><body><div class='wrap'>"
        "<a class='back' href='/'>&larr; back to dashboard</a>" + charter_link +
        "<div class='ehead'>"
        "<div class='tid'>EM-" + html.escape(ep["id"]) + "</div>"
        "<h1 class='title'><span class='e-emoji'>" + ep.get("emoji", "🎟️")
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
        + LIST_FILTER_BAR
        # status groups stack full-width: open first, then closed, then won't-fix
        + "<div class='cols' style='grid-template-columns:1fr'>"
        + "".join(cols)
        + "</div></div>" + LIST_FILTER_SCRIPT + "</body></html>"
    )
    return page


# --------------------------------------------------------------------------- #
# Filtered ticket-list page (/filter?...)
# --------------------------------------------------------------------------- #
def render_filter_page(data, q):
    """Cross-epic ticket list for a metric click-through.

    Query params (all optional): status=open,closed,wf · risk · priority ·
    quick=1 · blocked=y|n · stale=<days> · closed_from/closed_to ·
    created_from/created_to · label=<heading>."""
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

    buckets = {"open": [], "closed": [], "wf": []}
    for ep in data["epics"]:
        buckets["open"].extend(t for t in ep["openTickets"] if keep("open", t))
        buckets["closed"].extend(t for t in ep["closedTickets"] if keep("closed", t))
        buckets["wf"].extend(t for t in ep["wfTickets"] if keep("wf", t))

    # One status selected -> roomier card grid; several -> status groups
    # stacked full-width, open on top, then closed, then won't-fix.
    cards = len(statuses) == 1
    cols = []
    if "open" in statuses and (buckets["open"] or statuses == {"open"}):
        cols.append(_status_col("open", "Open — oldest first", buckets["open"],
                                "created", False, today, show_epic=True,
                                cards=cards))
    if "closed" in statuses and (buckets["closed"] or statuses == {"closed"}):
        cols.append(_status_col("closed", "Closed — newest first", buckets["closed"],
                                "closed", True, today, show_epic=True,
                                cards=cards))
    if "wf" in statuses and (buckets["wf"] or statuses == {"wf"}):
        cols.append(_status_col("wf", "Won't fix", buckets["wf"],
                                "created", False, today, show_epic=True,
                                cards=cards))

    n = sum(len(v) for v in buckets.values())
    eff_d = sum(t["effortH"] for v in buckets.values() for t in v
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

    # Status groups always stack full-width; card pages use the whole viewport.
    style = "grid-template-columns:1fr;"
    extra_css = ".wrap{max-width:1500px}" if cards else ""
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>" + html.escape(label) + " &middot; Clean Paste</title>"
        "<link rel=\"icon\" href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎟️</text></svg>\">"
        "<style>" + EPIC_CSS + extra_css + "</style></head><body><div class='wrap'>"
        "<a class='back' href='/'>&larr; back to dashboard</a>"
        "<div class='ehead'>"
        "<div class='tid'>FILTERED VIEW</div>"
        "<h1 class='title'>" + html.escape(label) + "</h1>"
        "<div class='estats'>"
        "<span><b>" + str(n) + "</b> tickets</span>"
        "<span>effort <b>" + ("%g" % round(eff_d, 1)) + "d</b></span>"
        + "".join("<span>" + c + "</span>" for c in crit)
        + "</div></div>"
        + LIST_FILTER_BAR
        + "<div class='cols' style='" + style + "'>"
        + ("".join(cols) or "<div class='col'><div class='empty'>no tickets match</div></div>")
        + "</div></div>" + LIST_FILTER_SCRIPT + "</body></html>"
    )
    return page


# --------------------------------------------------------------------------- #
# Dashboard shell (static; data comes from /api/data)
# --------------------------------------------------------------------------- #
DASHBOARD_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clean Paste &middot; Ticket Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎟️</text></svg>">
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
.notebtn{font-size:1rem;border:1px solid var(--line);border-radius:100px;
width:38px;height:32px;cursor:pointer;line-height:1;padding:0;display:inline-flex;align-items:center;justify-content:center}
.nb-notes{background:rgba(234,179,8,.16)}
.nb-notes:hover,.nb-notes.active{border-color:#b45309;background:rgba(234,179,8,.32)}
.nb-todo{background:rgba(220,38,38,.12)}
.nb-todo:hover,.nb-todo.active{border-color:#c23b3b;background:rgba(220,38,38,.26)}
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
.np-it .del{opacity:0}
.np-it:hover .del,.np-it .del:focus-visible{opacity:1}
.np-empty{font-size:.82rem;color:var(--ink-mut);padding:10px 9px;margin:0}
.np-done-h{font-family:var(--font-mono);font-size:.62rem;letter-spacing:.08em;text-transform:uppercase;
color:var(--ink-mut);padding:12px 9px 4px;border-top:1px solid var(--line-2);margin-top:8px}
.np-foot{flex:none;padding:9px 14px;border-top:1px solid var(--line-2)}
.np-foot button{font-family:var(--font-mono);font-size:.68rem;color:var(--ink-mut);background:none;border:0;cursor:pointer;padding:0}
.np-foot button:hover{color:var(--accent-ink)}
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
      <span class="eyebrow">Ticket portfolio &middot; engineering program</span>
      <span class="head-btns">
        <a id="ghBtn" class="ghbtn" hidden target="_blank" rel="noopener">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
          GitHub <span class="gh-sub" id="ghSub"></span></a>
        <button class="regen" id="regen" type="button"><span class="ic">&#8635;</span> Regenerate</button>
      </span>
    </div>
    <h1>Clean Paste</h1>
    <div class="tag-row">
      <p class="tagline" id="tagline">Scanning tickets&hellip;</p>
      <span class="note-btns">
        <button class="notebtn nb-notes" type="button" data-which="scratch" title="Notes (.scratchpad.md)" aria-label="Open notes">&#128221;</button>
        <button class="notebtn nb-todo" type="button" data-which="todo" title="To-do list (.todo.md)" aria-label="Open to-do list">&#128204;</button>
      </span>
    </div>
    <div class="gsearch-row">
      <input id="gsearch" class="gsearch" type="search" autocomplete="off" spellcheck="false"
             placeholder="Jump to a ticket or epic &mdash; EM-1234, E019, or title&hellip;"
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
        <a class="hbtn" href="/adrs">ADRs &nearr;</a>
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
  <aside class="notepanel" id="notePanel" hidden aria-label="Notes pop-out">
    <div class="np-head">
      <span class="np-emo" id="npEmo">&#128221;</span>
      <span class="np-title" id="npTitle">Notes</span>
      <span class="np-file" id="npFile">.scratchpad.md</span>
      <span class="np-status" id="npStatus"></span>
      <button class="np-close" id="npClose" type="button" aria-label="Close notes">&times;</button>
    </div>
    <textarea id="npText" spellcheck="false"
              placeholder="Jot it down &mdash; autosaves to a local file the server reads back next run. Not tracked in git."></textarea>
    <div class="np-todo" id="npTodo" hidden>
      <form class="np-add" id="npAdd">
        <input id="npAddIn" type="text" autocomplete="off" spellcheck="false"
               placeholder="Add a to-do &mdash; Enter to save">
      </form>
      <div class="np-items" id="npItems"></div>
      <div class="np-foot"><button type="button" id="npDoneTgl" hidden></button></div>
    </div>
  </aside>
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
    <div class="recent" id="pinnedPanel"><div class="panel-h">&#128278; Pinned &middot; watching</div><div id="pinnedList"></div></div>
    <div class="recent" id="wipPanel"><div class="panel-h">&#128295; Work in progress &middot; git working tree</div><div id="wipList"></div></div>
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
      <div class="panel-h">Up next &mdash; unblocked, ranked by priority &middot; unblocks &middot; effort</div>
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
      <div class="recent"><div class="panel-h">Recently closed</div><div id="recentList"></div></div>
      <div class="recent"><div class="panel-h">Recently created &middot; still open</div><div id="createdList"></div></div>
    </div>
  </section>
  <section id="sec-backlog">
    <div class="sec-head"><h2><span class="h-num">04</span>Backlog</h2>
      <div class="backlog-tools">
        <input id="tsearch" class="searchbox" type="search" placeholder="search EM-#### or title" autocomplete="off" aria-label="Search tickets by number or title">
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
    <p><span class="mono">Live scan</span> of <span class="mono" id="foot-src">tickets/</span> frontmatter &mdash; counts every ticket file (<span class="mono">id: EM-####</span>), including compound sub-tickets, so totals reflect real files on disk rather than the generated index.</p>
    <p><span class="mono" id="foot-time">&nbsp;</span></p>
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
  function statusOf(e){
    if(e.id==="E019"&&e.open>0) return {cls:"chip-active",txt:"Active WIP"};
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
        '<span class="g-emo">'+(ep.emoji||"")+'</span><span class="b-tid">EM-'+ep.id+'</span>'+
        '<span class="b-ttl">'+esc(ep.title)+'</span>'+
        '<span class="b-date">'+(ep.open||0)+' open</span></a>':'')+
      '<a class="lw-row" href="/ticket/'+encodeURIComponent(lw.ticket)+'">'+
        '<span class="lw-k">ticket</span>'+
        '<span class="g-emo">🎟️</span>'+
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
      return '<a href="/ticket/'+encodeURIComponent(it.id)+'">'+
        statusChipHtml(it.status)+
        '<span class="b-tid">'+esc(it.id)+'</span>'+
        '<span class="b-ttl">'+esc(it.title)+'</span>'+
        '<span class="b-right">'+metaBadges(it,true)+extra+'</span></a>';
    }
    document.getElementById("pinnedList").innerHTML=pins.map(function(it){
      return row(it,'<button type="button" class="pin-x" data-id="'+esc(it.id)+
        '" title="unpin '+esc(it.id)+'">✕ unpin</button>');
    }).join("")||'<div class="chart-empty">Nothing pinned — use the 🔖 button at the top of any ticket page.</div>';
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
                 t.epic?"EM-"+t.epic:"",t.wip?"WIP":""].filter(Boolean).join(" · ");
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
      'The PII-firewall build tracked as <b>'+t.total.toLocaleString()+' tickets</b> across '+d.epics.length+
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
    var complete=d.epics.filter(function(e){return e.open===0;}).length;
    var finishing=d.epics.filter(function(e){return e.open===1;}).length;
    document.getElementById("k-complete").textContent=complete;
    document.getElementById("k-complete-sub").textContent=finishing+" more at 1 open";
  }
  function renderTracks(d){
    var complete=d.epics.filter(function(e){return e.open===0;});
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
        '<span class="et-id">EM-'+e.id+'</span>'+
        '<span class="et-name">'+esc(e.title)+'</span>'+
        '<span class="ebar" role="img" aria-label="'+e.closed+' closed, '+e.open+' open, '+e.wf+' won\'t-fix">'+
          '<span class="seg-done" style="width:'+pct(e.closed,t)+'%"></span>'+
          '<span class="seg-open" style="width:'+pct(e.open,t)+'%"></span>'+
          '<span class="seg-wf" style="width:'+pct(e.wf,t)+'%"></span></span>'+
        '<span class="et-sub">'+(e.open?('<b>'+e.open+'</b> open'):'complete')+' · '+t+' total</span>'+
        '<span class="e-chip '+st.cls+'">'+st.txt+'</span></a>';
    }).join("")+'</div>';
  }
  function ticketRow(it,view,withEpic){
    var isDec=/DECISION|Decision:|CONCEPT/.test(it.title);
    var name=esc(it.title.replace(/^(DECISION\s*[—-]\s*|Decision:\s*|CONCEPT:\s*)/,""));
    var date=view==="closed"?(it.closed||""):(it.created||"");
    var chip=isDec?'<span class="b-dec">decision</span>':
      view==="wf"?'<span class="b-dec">won\'t-fix</span>':
      view==="standing"?'<span class="b-dec">standing</span>':'';
    return '<li><a class="'+(view==="closed"?"closed":"")+'" href="/ticket/'+encodeURIComponent(it.id)+
      '"><span class="b-tid">'+esc(it.id)+'</span>'+
      '<span class="b-ttl">'+name+chip+'</span>'+
      '<span class="b-right">'+metaBadges(it,withEpic)+
      (date?'<span class="b-date">'+date+'</span>':'')+'</span></a></li>';
  }
  function recentRow(it,datefield,tidColor,extra){
    return '<a href="/ticket/'+encodeURIComponent(it.id)+'">'+
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
      det.className="bcard"+(e.id==="E019"&&e.open>0?" hl":"");
      det.open=(e.id in cardOpen)?cardOpen[e.id]:(filtering||items.length<=25);
      det.addEventListener("toggle",function(){cardOpen[e.id]=det.open;});
      det.innerHTML=
        '<summary><span class="b-caret">▶</span>'+
        '<span class="b-emo">'+(e.emoji||"")+'</span>'+
        '<span class="b-head-main"><a class="b-head-id" href="/epic/'+e.id+'" title="open the epic page">EM-'+e.id+' ↗</a>'+
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
      var r={kind:"epic",href:"/epic/"+e.id,id:"EM-"+e.id,title:e.title,emo:e.emoji,
             sub:e.open+" open · "+e.closed+" closed"};
      (eid===qe?exact:rest).push(r);
    });
    (d.adrs||[]).forEach(function(a){
      var idl=a.num.toLowerCase();
      if(idl.indexOf(q)===-1&&a.title.toLowerCase().indexOf(q)===-1)return;
      var r={kind:"adr",href:"/doc/"+encodeURI(a.path),id:a.num,title:a.title,
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
                 title:t.title,sub:(e.emoji?e.emoji+" ":"")+"EM-"+e.id};
          (t.id.toLowerCase()===q||t.id.toLowerCase()==="em-"+qe?exact:rest).push(r);
        });
      });
    });
    return exact.concat(rest);
  }
  function gRender(){
    var rows=gRes.slice(0,G_MAX).map(function(r,i){
      return '<a href="'+r.href+'"'+(i===gAct?' class="active"':'')+'>'+
        '<span class="g-kind k-'+r.kind+'">'+r.kind+'</span>'+
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

/* ---- notes / to-do pop-out (persisted to untracked files) ---- */
(function(){
  var panel=document.getElementById("notePanel"),ta=document.getElementById("npText"),
      todoBox=document.getElementById("npTodo"),itemsEl=document.getElementById("npItems"),
      addForm=document.getElementById("npAdd"),addIn=document.getElementById("npAddIn"),
      doneTgl=document.getElementById("npDoneTgl"),
      emo=document.getElementById("npEmo"),title=document.getElementById("npTitle"),
      file=document.getElementById("npFile"),status=document.getElementById("npStatus"),
      btns=document.querySelectorAll(".notebtn"),
      META={scratch:{emo:"📝",title:"Notes",file:".scratchpad.md"},
            todo:{emo:"📌",title:"To-do",file:".todo.md"}},
      cur=null,loaded="",timer=null,items=[],showDone=false;
  function setStatus(s){status.textContent=s;}
  function markBtns(){
    btns.forEach(function(b){b.classList.toggle("active",!panel.hidden&&b.dataset.which===cur);});
  }
  /* .todo.md is a real markdown task list: `- [ ] open` / `- [x] done`.
     Any stray plain line is adopted as an open item and normalised on save. */
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
    return list.length?list.map(function(i){
      return "- ["+(i.done?"x":" ")+"] "+i.text;}).join("\n")+"\n":"";
  }
  function content(){return cur==="todo"?serializeTodo(items):ta.value;}
  function todoRow(it){
    var d=document.createElement("div");d.className="np-it"+(it.done?" done":"");
    var cb=document.createElement("input");cb.type="checkbox";cb.checked=it.done;
    cb.addEventListener("change",function(){it.done=cb.checked;renderTodo();flush();});
    var t=document.createElement("span");t.className="t";t.textContent=it.text;
    var del=document.createElement("button");del.type="button";del.className="pin-x del";
    del.textContent="✕";del.title="Delete";
    del.addEventListener("click",function(){
      items.splice(items.indexOf(it),1);renderTodo();flush();});
    d.appendChild(cb);d.appendChild(t);d.appendChild(del);
    return d;
  }
  function renderTodo(){
    itemsEl.innerHTML="";
    var open=items.filter(function(i){return !i.done;}),
        done=items.filter(function(i){return i.done;});
    if(!open.length){
      var e=document.createElement("p");e.className="np-empty";
      e.textContent=done.length?"All done. 🎉":"Nothing yet — add one above.";
      itemsEl.appendChild(e);
    }
    open.forEach(function(it){itemsEl.appendChild(todoRow(it));});
    if(done.length&&showDone){
      var h=document.createElement("div");h.className="np-done-h";h.textContent="Done";
      itemsEl.appendChild(h);
      done.forEach(function(it){itemsEl.appendChild(todoRow(it));});
    }
    doneTgl.hidden=!done.length;
    doneTgl.textContent=(showDone?"hide":"show")+" done ("+done.length+")";
  }
  function flush(sync){
    if(timer){clearTimeout(timer);timer=null;}
    if(cur===null||content()===loaded)return;
    var body=JSON.stringify({which:cur,content:content()});
    loaded=content();
    if(sync&&navigator.sendBeacon){
      navigator.sendBeacon("/api/note",new Blob([body],{type:"application/json"}));
      return;
    }
    setStatus("saving…");
    fetch("/api/note",{method:"POST",headers:{"Content-Type":"application/json"},body:body})
      .then(function(r){return r.json();})
      .then(function(j){setStatus(j.ok?"saved":"save failed");})
      .catch(function(){setStatus("save failed — server down?");});
  }
  function close(){
    flush();
    panel.hidden=true;cur=null;
    markBtns();
  }
  function open(which){
    if(cur===which){close();return;}
    flush();
    cur=which;loaded="";
    emo.textContent=META[which].emo;title.textContent=META[which].title;
    file.textContent=META[which].file;
    ta.hidden=which==="todo";todoBox.hidden=which!=="todo";
    panel.hidden=false;markBtns();
    ta.value="";ta.disabled=true;items=[];showDone=false;setStatus("loading…");
    fetch("/api/note?which="+which)
      .then(function(r){return r.json();})
      .then(function(j){
        if(cur!==which)return;           // switched away while loading
        var c=j.ok?j.content:"";
        loaded=c;setStatus(j.ok?"":"load failed");
        if(which==="todo"){
          items=parseTodo(c);renderTodo();
          addIn.value="";addIn.focus();
          if(serializeTodo(items)!==c)flush();  // normalise legacy flat text
        }else{
          ta.disabled=false;ta.value=c;ta.focus();
        }
      })
      .catch(function(){
        if(cur!==which)return;
        ta.disabled=false;setStatus("load failed — server down?");});
  }
  btns.forEach(function(b){b.addEventListener("click",function(){open(b.dataset.which);});});
  document.getElementById("npClose").addEventListener("click",close);
  addForm.addEventListener("submit",function(e){
    e.preventDefault();
    var t=addIn.value.trim();if(!t)return;
    items.push({done:false,text:t});addIn.value="";
    renderTodo();flush();
  });
  doneTgl.addEventListener("click",function(){showDone=!showDone;renderTodo();});
  ta.addEventListener("input",function(){
    setStatus("…");
    if(timer)clearTimeout(timer);
    timer=setTimeout(function(){flush();},700);
  });
  ta.addEventListener("blur",function(){flush();});
  ta.addEventListener("keydown",function(e){
    if((e.metaKey||e.ctrlKey)&&e.key==="s"){e.preventDefault();flush();}
  });
  document.addEventListener("keydown",function(e){
    if(e.key==="Escape"&&!panel.hidden)close();
  });
  window.addEventListener("beforeunload",function(){flush(true);});
})();
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
        "<title>" + title + " &middot; Clean Paste</title>"
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
      html+='<option value="'+F.esc(c)+'">'+F.esc((e.emoji||"🎟️")+"  EM-"+(c||"?")+" · "+
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
      (ep?'<div class="tt-epic">'+(ep.emoji||"")+" EM-"+F.esc(ep.id)+" · "+F.esc(ep.title||"")+'</div>':"")+
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
      (ep?'<a class="fc-epic" href="/epic/'+F.esc(ep.id)+'">'+(ep.emoji||"")+" EM-"+F.esc(ep.id)+'</a>':"")+
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
      {v:t.status,l:"Status",s:t.epic?("epic EM-"+t.epic):"—",
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

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _activate_from_request(self):
        """Pick the interface for this request from the `ifc` cookie (default:
        the first registered)."""
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
            self._send(render_adr_page(recs, by_num))
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
            self._send(render_doc_page(full, set(id_index),
                                       {e["id"] for e in data["epics"]},
                                       adr_index()[1]))
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
# Config: read <repo>/interfacile.json and reskin the live server from it.
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


def _transform_html(s):
    """Reskin one HTML page for the active interface: theme colours + signature
    strip, then brand, favicon, tagline, eyebrow, and id prefix. Each guard
    skips work when the value still equals the built-in (blue/Clean Paste)
    default, so a repo with no interfacile.json pays nothing and looks unchanged."""
    if THEME_REMAP:
        for a, b in THEME_REMAP.items():
            s = s.replace(a, b)
    if THEME_STRIP:
        strip = ("html::before{content:'';display:block;height:3px;"
                 "background:linear-gradient(90deg,%s)}" % ",".join(THEME_STRIP))
        s = s.replace("*{box-sizing:border-box}",
                      strip + "*{box-sizing:border-box}", 1)
    if PFX != "EM":
        s = s.replace("EM-", PFX + "-")
    if BRAND != "Clean Paste":
        s = s.replace("Clean Paste", BRAND)
    if FAVICON != "🎟️":
        s = s.replace("🎟️", FAVICON)
    if TAGLINE != "The PII-firewall build tracked as":
        s = s.replace("The PII-firewall build tracked as", TAGLINE)
    if EYEBROW != "Ticket portfolio &middot; engineering program":
        s = s.replace("Ticket portfolio &middot; engineering program", EYEBROW)
    if THEME_OVERRIDE_CSS:
        s = s.replace("</head>", "<style>" + THEME_OVERRIDE_CSS + "</style></head>", 1)
    s = s.replace("</body>", _FOOTER + "</body>", 1)
    if len(INTERFACES) > 1:
        is_dash = 'id="regen"' in s          # the dashboard has the Regenerate button
        s = _BODY_OPEN_RE.sub(lambda m: m.group(1) + _switcher_html(is_dash), s, count=1)
    return s


def load_config(repo_root):
    """Read the repo's interface config, preferring the hidden `.interfacile.json`
    and falling back to a visible `interfacile.json`. {} when neither exists; a
    malformed file is warned about and ignored so a bad config never takes the
    dashboard down."""
    for name in (".interfacile.json", "interfacile.json"):
        path = os.path.join(repo_root, name)
        try:
            with open(path, encoding="utf-8") as fh:
                conf = json.load(fh)
            return conf if isinstance(conf, dict) else {}
        except FileNotFoundError:
            continue
        except Exception as exc:
            sys.stderr.write("%s ignored (%s)\n" % (name, exc))
            return {}
    return {}


def apply_config(conf):
    """Overwrite the live per-interface globals from a parsed config dict.
    Missing keys keep the current (default) value, so partial configs are fine."""
    global PFX, ID_DIGITS, BRAND, FAVICON, HEADER_ICON, EYEBROW, TAGLINE
    global SERVER_PORT, EPIC_TITLES, EPIC_EMOJI, THEME_REMAP, THEME_STRIP, _IDRE
    global THEME_OVERRIDE_CSS
    global TICKET_ID_RE, EPIC_CODE_RE, TICKET_PARTS_RE, DEP_ID_RE, SUB_ID_RE
    global TICKET_LINK_RE, EPIC_LINK_RE, _MD_EPIC_FILE_RE, _MD_TICKET_FILE_RE

    brand = conf.get("brand", {})
    BRAND = brand.get("name", _DEF["brand"])
    FAVICON = brand.get("favicon", _DEF["favicon"])
    HEADER_ICON = brand.get("icon", brand.get("favicon", _DEF["icon"]))
    EYEBROW = brand.get("eyebrow", _DEF["eyebrow"])
    TAGLINE = brand.get("tagline", _DEF["tagline"])

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

    epics = conf.get("epics", {})
    if epics:
        EPIC_TITLES = {k: (v.get("title", k) if isinstance(v, dict) else v)
                       for k, v in epics.items()}
        EPIC_EMOJI = {k: v["emoji"] for k, v in epics.items()
                      if isinstance(v, dict) and v.get("emoji")}
    else:
        EPIC_TITLES = dict(_DEF_EPIC_TITLES)
        EPIC_EMOJI = dict(_DEF_EPIC_EMOJI)

    THEME_REMAP, THEME_STRIP, THEME_OVERRIDE_CSS = resolve_theme(conf.get("theme", "blue"))
    SERVER_PORT = int(conf.get("server", {}).get("port", _DEF["port"]))


# --------------------------------------------------------------------------- #
# Multi-interface: one process serves several repos, switched by an `ifc` cookie
# and a centered header dropdown. Because the data layer reads module globals,
# each request activates its interface under a lock (fine for a local, single-
# user dashboard — requests are effectively serial anyway).
# --------------------------------------------------------------------------- #
class Interface:
    __slots__ = ("slug", "name", "icon", "root", "conf", "shortcut")

    def __init__(self, slug, name, icon, root, conf, shortcut=""):
        self.slug, self.name, self.icon, self.root, self.conf = slug, name, icon, root, conf
        self.shortcut = shortcut


INTERFACES = []                  # ordered list (CLI order == switcher order)
_IFACE_BY_SLUG = {}
ACTIVE_SLUG = ""
_LOCK = threading.Lock()


def _slugify(base, seen):
    s = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "iface"
    slug, n = s, 2
    while slug in seen:
        slug = "%s-%d" % (s, n); n += 1
    seen.add(slug)
    return slug


def build_registry(repo_roots):
    """Load each repo's config into an Interface and index by slug (CLI order)."""
    global INTERFACES, _IFACE_BY_SLUG
    INTERFACES, _IFACE_BY_SLUG, seen = [], {}, set()
    for r in repo_roots:
        root = os.path.abspath(r)
        conf = load_config(root)
        brand = conf.get("brand", {})
        name = brand.get("name") or os.path.basename(root)
        icon = brand.get("icon") or brand.get("favicon") or _DEF["icon"]
        sc = str(conf.get("shortcut", "") or "").strip()
        it = Interface(_slugify(os.path.basename(root), seen), name, icon, root, conf, sc)
        INTERFACES.append(it)
        _IFACE_BY_SLUG[it.slug] = it
    return INTERFACES


def activate(iface):
    """Point every per-interface global at one interface for the current request."""
    global REPO_ROOT, TICKETS_DIR, ADR_DIR, PINS_FILE, NOTE_FILES, ACTIVE_SLUG
    REPO_ROOT = iface.root
    TICKETS_DIR = os.path.join(REPO_ROOT, "tickets")
    ADR_DIR = os.path.join(REPO_ROOT, "docs", "architecture", "adr")
    PINS_FILE = os.path.join(REPO_ROOT, ".ticket-pins.json")
    NOTE_FILES = {"scratch": os.path.join(REPO_ROOT, ".scratchpad.md"),
                  "todo": os.path.join(REPO_ROOT, ".todo.md")}
    ACTIVE_SLUG = iface.slug
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

_SWITCHER_CSS = """
body{padding-top:52px}
#ifc-bar{position:fixed;top:0;left:0;right:0;z-index:200;display:flex;align-items:center;gap:12px;
padding:8px 18px;background:var(--surface,#fff);border-bottom:1px solid var(--line,#e2e2e2);
box-shadow:0 1px 3px rgba(0,0,0,.06)}
#ifc-actions{margin-left:auto;display:flex;align-items:center;gap:8px}
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
menu.addEventListener('click',function(e){var li=e.target.closest('li[data-slug]');if(li)go(li.getAttribute('data-slug'));});
document.addEventListener('click',function(){set(false);});
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
/* clicking outside the notes/todo pop-out closes it */
document.addEventListener('mousedown',function(e){
var np=document.getElementById('notePanel');
if(np&&!np.hidden&&!np.contains(e.target)&&!(e.target.closest&&e.target.closest('.notebtn'))){
var c=document.getElementById('npClose');if(c)c.click();}
});
function relocate(){
var acts=document.getElementById('ifc-actions');
if(acts){var gh=document.getElementById('ghBtn'),rg=document.getElementById('regen');
if(gh)acts.appendChild(gh);if(rg)acts.appendChild(rg);}
var head=document.querySelector('.head-btns'),notes=document.querySelector('.note-btns');
if(head&&notes){while(notes.firstChild)head.appendChild(notes.firstChild);
notes.parentNode.removeChild(notes);}
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',relocate);
else relocate();
})();
"""


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

    rows = []
    for it in INTERFACES:
        kbd = ("<span class='ifc-kbd'>%s</span>" % html.escape(it.shortcut)) if it.shortcut else ""
        rows.append(
            "<li role='option' data-slug='%s'%s%s>"
            "<span class='ifc-txt'>%s<span class='ifc-sub'>%s</span></span>%s"
            "<span class='ifc-chk' aria-hidden='true'>&#10003;</span></li>"
            % (html.escape(it.slug),
               " data-key='%s'" % html.escape(it.shortcut) if it.shortcut else "",
               " aria-selected='true'" if it.slug == ACTIVE_SLUG else "",
               _name(it), _sub(it), kbd))
    return (
        "<div id='ifc-bar'><div id='ifc-switch'>"
        "<button id='ifc-cur' type='button' aria-haspopup='listbox' aria-expanded='false'>"
        + _name(active)
        + "<span class='ifc-caret' aria-hidden='true'>&#9662;</span></button>"
        "<ul id='ifc-menu' role='listbox'>"
        "<li class='ifc-hd' aria-hidden='true' style='cursor:default'>Switch interface</li>"
        + "".join(rows) + "</ul>"
        "</div>" + home + "<div id='ifc-actions'></div></div>"
        "<style>" + _SWITCHER_CSS + "</style><script>" + _SWITCHER_JS + "</script>"
    )


_BODY_OPEN_RE = re.compile(r"(<body[^>]*>)", re.I)


def run(repo_roots, port=None, host="127.0.0.1", open_browser=True):
    """Serve one or more repos from a single process. >1 repo shows the switcher.
    This is the heart the CLI (`interfacile serve` / `interfacile hub`) drives."""
    build_registry(repo_roots)
    for it in INTERFACES:
        if not os.path.isdir(os.path.join(it.root, "tickets")):
            sys.exit("tickets/ not found at %s" % os.path.join(it.root, "tickets"))

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
    """Backward-compatible flat CLI, used by the scripts/dev shim."""
    ap = argparse.ArgumentParser(description="interfacile — ticket-portfolio dashboard.")
    ap.add_argument("--port", type=int, default=None,
                    help="listen port (overrides .interfacile.json; default 8787)")
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

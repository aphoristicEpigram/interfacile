"""The ticket flow: create, list, inspect, close, lint.

    interfacile new "Title" --epic E001 [--risk MEDIUM] [--priority 2]
                                        [--effort 4h] [--depends-on ID,ID]
    interfacile tickets [--epic E001] [--all]    list the board, grouped by epic
    interfacile ready   [--epic E001]            open tickets with no open deps
    interfacile show  ID                         print one ticket (path + contents)
    interfacile deps  ID                         what it waits on / what waits on it
    interfacile close ID [--note "..."]          set CLOSED + dates, move to closed/
    interfacile reopen ID                        undo a close, move back to open/
    interfacile drop  ID --why "..."             WONT_FIX: dropped on purpose
    interfacile lint                             validate every ticket file

`tickets`, `ready`, `show`, and `lint` take --json for machine-readable output.

Every command takes --repo PATH (default: current directory). Nothing here is
project-specific: the id scheme comes from the repo's .interfacile/config.json
(`ids.prefix` / `ids.digits`), or is inferred from existing ticket filenames.

Three rules keep the flow simple:
  1. A ticket's real state is its `status:` field; `open/` and `closed/` are
     for humans, and `close` keeps them in step automatically.
  2. "Blocked" is never a status — it is derived from `depends_on` pointing at
     a ticket that is still open, and clears itself when that ticket closes.
  3. `lint` is the referee: ids, fields, dates, and the dependency graph.
"""
import datetime
import glob
import json
import os
import re
import sys

from . import events
from . import server

VALID_STATUS = ("OPEN", "CLOSED", "WONT_FIX", "STANDING")
VALID_RISK = ("LOW", "MEDIUM", "HIGH")
DONE = ("CLOSED", "WONT_FIX")            # statuses that satisfy a dependency
EFFORT_RE = re.compile(r"^(?:[\d.]+(?:-[\d.]+)?\s*[mhdw]|N/A)$")

# Files under tickets/ that are documentation, not tickets.
NON_TICKET_FILES = ("README.md", "TICKET_INDEX.md")


# --------------------------------------------------------------------------- #
# Id scheme — config first, else inferred, never hardcoded.
# --------------------------------------------------------------------------- #
def infer_prefix(root):
    """Guess the ticket id prefix from existing ticket filenames (TH-0004 -> TH)."""
    for f in glob.glob(os.path.join(root, "tickets", "**", "*.md"), recursive=True):
        m = re.match(r"([A-Za-z]{1,6})-E?\d{2,}", os.path.basename(f))
        if m:
            return m.group(1).upper()
    return None


def fallback_prefix(root):
    """Initials of a multi-word folder name (Andy's Automates -> AA), else its
    first two letters. Only used when there are no tickets to infer from."""
    words = [w for w in re.split(r"[^A-Za-z]+", os.path.basename(root.rstrip("/")))
             if len(w) > 1]
    if len(words) >= 2:
        return "".join(w[0] for w in words[:3]).upper()
    return words[0][:2].upper() if words else "TK"


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return "-".join(slug.split("-")[:6]) or "ticket"


def id_scheme(root):
    """(prefix, digits) for a repo: config first, else infer, else initials."""
    ids = (server.load_config(root) or {}).get("ids") or {}
    prefix = ids.get("prefix") or infer_prefix(root) or fallback_prefix(root)
    try:
        digits = int(ids.get("digits") or 4)
    except (TypeError, ValueError):
        digits = 4
    return prefix, digits


def parse_ids(value):
    """Split an id list however it was written: `[A, B]`, `A, B`, or `A B`."""
    return [v for v in re.split(r"[\[\],\s]+", value or "") if v]


def unquote(value):
    """Frontmatter values are raw lines; tolerate hand-quoted ones on display."""
    value = (value or "").strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


# --------------------------------------------------------------------------- #
# Repo model
# --------------------------------------------------------------------------- #
class Card(object):
    """One parsed file under tickets/ — a ticket or an epic charter."""
    __slots__ = ("path", "tid", "fm", "body")

    def __init__(self, path, tid, fm, body):
        self.path, self.tid, self.fm, self.body = path, tid, fm, body

    def links(self, key):
        return parse_ids(self.fm.get(key, ""))

    @property
    def status(self):
        return (self.fm.get("status") or "").strip().upper()


class Repo(object):
    """A repo's tickets/ tree plus the id scheme it uses."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.tickets_dir = os.path.join(self.root, "tickets")
        self.prefix, self.digits = id_scheme(self.root)
        esc = re.escape(self.prefix)
        self.epic_re = re.compile(r"^%s-E\d+$" % esc)
        # A valid id: epic (PFX-E1), ticket (PFX-0012), sub-ticket (PFX-0012-B).
        self.id_re = re.compile(
            r"^%s-(?:E\d+|\d{%d}(?:[-.][A-Za-z0-9]+)*)$" % (esc, self.digits))
        self.num_re = re.compile(r"^%s-(\d+)" % esc)
        # The same id as it appears *inside* prose — a captured note names the
        # ticket it became, and the tail it names it in: `(PFX-0012, PFX-0013)`
        # on a to-do item, `→ PFX-0012` on a scratchpad block.
        ids = r"\b%s-\d{%d}(?:[-.][A-Za-z0-9]+)*\b" % (esc, self.digits)
        self.link_re = re.compile(ids)
        self.paren_tail_re = re.compile(
            r"\(\s*%s(?:\s*,\s*%s)*\s*\)\s*$" % (ids, ids))
        self.arrow_tail_re = re.compile(
            r"→\s*%s(?:\s*,\s*%s)*\s*$" % (ids, ids))

    def is_epic(self, tid):
        return bool(self.epic_re.match(tid or ""))

    def cards(self):
        """Parse every ticket file. Returns (cards, problems) where problems are
        (path, message) for files that can't even join the graph."""
        cards, problems = [], []
        pattern = os.path.join(self.tickets_dir, "**", "*.md")
        for path in sorted(glob.glob(pattern, recursive=True)):
            if os.path.basename(path) in NON_TICKET_FILES:
                continue
            fm_lines, body = server.split_frontmatter(server.read_text(path))
            if not fm_lines:
                problems.append((path, "no YAML frontmatter"))
                continue
            fm = dict(server.frontmatter_scalars(fm_lines))
            tid = (fm.get("id") or "").strip()
            if not tid:
                problems.append((path, "missing `id:` in frontmatter"))
                continue
            cards.append(Card(path, tid, fm, body))
        return cards, problems

    def epic_dirs(self):
        """{epic id: directory} from tickets/ subdirectory names."""
        out = {}
        if not os.path.isdir(self.tickets_dir):
            return out
        for name in sorted(os.listdir(self.tickets_dir)):
            m = re.match(r"(%s-E\d+)(?:-|$)" % re.escape(self.prefix), name)
            full = os.path.join(self.tickets_dir, name)
            if m and os.path.isdir(full):
                out[m.group(1)] = full
        return out

    def resolve_epic(self, text):
        """Accept E6, E006, PFX-E006, or the full directory name. Matches by
        epic number so users never have to remember the zero padding."""
        text = (text or "").strip().rstrip("/")
        dirs = self.epic_dirs()
        m = (re.match(r"^[Ee](\d+)$", text)
             or re.match(r"^%s-[Ee](\d+)$" % re.escape(self.prefix), text))
        for eid, path in dirs.items():
            if text == eid or text == os.path.basename(path):
                return eid, path
            if m and int(re.match(r".*-E(\d+)$", eid).group(1)) == int(m.group(1)):
                return eid, path
        return None, None

    def next_id(self, cards):
        highest = 0
        for c in cards:
            m = self.num_re.match(c.tid)
            if m:
                highest = max(highest, int(m.group(1)))
        return "%s-%0*d" % (self.prefix, self.digits, highest + 1)


def _root(args):
    return os.path.abspath(getattr(args, "repo", None) or os.getcwd())


def _by_id(cards):
    return {c.tid: c for c in cards}


def _find(repo, tid):
    """Find a card by id, case-insensitively and with or without the prefix."""
    cards, _ = repo.cards()
    want = tid.strip().upper()
    if not want.startswith(repo.prefix + "-"):
        want = "%s-%s" % (repo.prefix, want)
    for c in cards:
        if c.tid.upper() == want:
            return c, cards
    return None, cards


def _open_deps(card, by_id):
    """The depends_on entries that still block this card (unknown ids don't)."""
    return [d for d in card.links("depends_on")
            if d in by_id and by_id[d].status not in DONE]


def _epic_title(eid, cards):
    for c in cards:
        if c.tid == eid:
            return unquote(c.fm.get("title", ""))
    return ""


def _card_json(t, by_id, root):
    """One ticket as a stable dict for --json consumers (scripts, agents, CI)."""
    try:
        priority = int(t.fm.get("priority", ""))
    except (TypeError, ValueError):
        priority = None
    return {"id": t.tid,
            "title": unquote(t.fm.get("title", "")),
            "epic": t.fm.get("epic", ""),
            "status": t.status,
            "risk": (t.fm.get("risk") or "").strip().upper(),
            "priority": priority,
            "effort": t.fm.get("effort", ""),
            "created": t.fm.get("created", ""),
            "closed": t.fm.get("closed", ""),
            "depends_on": t.links("depends_on"),
            "blocks": t.links("blocks"),
            "blocked_by": _open_deps(t, by_id),
            "path": os.path.relpath(t.path, root)}


def _emit_json(payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# new
# --------------------------------------------------------------------------- #
_TICKET_TEMPLATE = """# %(tid)s · %(title)s

## Context

%(context)s

## Approach

_How to solve it. Rough direction is fine; refine before starting work._

## Acceptance criteria

- [ ] _Concrete, verifiable "done" checks._
"""

# What the Context section says until someone says something better.
_CONTEXT_STUB = "_Why this ticket exists — the problem or need._"

# How many times `create` will re-try for a free id before giving up. A number
# is only lost to a writer that beat us to it, so one or two rounds is realistic
# and anything approaching this is a bug somewhere else.
_ID_TRIES = 25


def _log(repo, kind, tid):
    """Record a CLI mutation in the hub-wide event log — the same
    stream the dashboard writes to, so an automation watching for a
    close hears it whether it came from a click or a command.

    The interface is named with the *same* slug the server uses, not the raw
    folder name: they used to disagree wherever a folder name had an underscore,
    which split one project's events across two keys."""
    events.record(kind, ticket=tid, repo=repo.root,
                  interface=server.iface_slug(repo.root))


def ticket_text(tid, title, eid, risk, priority, effort, depends=(), context=""):
    """The full text of one ticket file. The only place a ticket's shape is
    written down — the CLI and the dashboard's New-ticket form both come here."""
    today = datetime.date.today().isoformat()
    lines = ["id: %s" % tid,
             "title: %s" % title,
             "epic: %s" % eid,
             "status: OPEN",
             "risk: %s" % risk,
             "priority: %d" % int(priority),
             "effort: %s" % effort,
             "created: %s" % today]
    if depends:
        lines.append("depends_on: [%s]" % ", ".join(depends))
    lines.append("updated: %s" % today)
    body = _TICKET_TEMPLATE % {"tid": tid, "title": title,
                               "context": context.strip() or _CONTEXT_STUB}
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body


def create(repo, title, eid, edir, risk="LOW", priority=3, effort="2h",
           depends=(), context="", tid=None):
    """Write one new ticket and return (id, path).

    The id is allocated here, at the last possible moment, and never reserved
    ahead of time: an agent may file a ticket between a form opening and its
    save. So we take the next free number against a fresh scan, and create the
    file with O_EXCL — a lost race then means "take the next number", not an
    overwrite. Pass `tid` to demand a specific id, which raises if it is taken.

    Writing the file is all this does — recording the event is left to the caller,
    because the CLI and the server name the interface they act for differently.
    """
    title = " ".join(title.split())
    want = (tid or "").strip()
    for _ in range(_ID_TRIES):
        cards, _problems = repo.cards()
        by_id = _by_id(cards)
        tid = want or repo.next_id(cards)
        if not repo.id_re.match(tid):
            raise ValueError("malformed id %r (expected e.g. %s-%0*d)"
                             % (tid, repo.prefix, repo.digits, 1))
        if tid in by_id:
            if want:
                raise ValueError("%s already exists: %s" % (tid, by_id[tid].path))
            continue                    # somebody took the number; re-scan and move up
        for dep in depends:
            if dep not in by_id:
                raise ValueError("depends_on references unknown ticket %s" % dep)
        path = os.path.join(edir, "open", "%s-%s.md" % (tid, slugify(title)))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            if want:
                raise ValueError("%s already exists: %s" % (tid, path))
            continue                    # same race, caught one level down
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(ticket_text(tid, title, eid, risk, priority, effort,
                                 depends, context))
        return tid, path
    raise RuntimeError("no free ticket id after %d tries — is something else "
                       "writing tickets in a loop?" % _ID_TRIES)


def cmd_new(args):
    repo = Repo(_root(args))
    if not os.path.isdir(repo.tickets_dir):
        sys.stderr.write("no tickets/ folder here — run `interfacile init` first.\n")
        return 1
    cards, _ = repo.cards()

    eid, edir = repo.resolve_epic(args.epic)
    if not eid:
        sys.stderr.write("unknown epic %r. Epics here:\n" % args.epic)
        for known, path in sorted(repo.epic_dirs().items()):
            sys.stderr.write("  %s  %s\n" % (known, _epic_title(known, cards)))
        sys.stderr.write("(create one with `interfacile epics`)\n")
        return 1

    if args.dry_run:
        tid = (args.id or "").strip() or repo.next_id(cards)
        path = os.path.join(edir, "open",
                            "%s-%s.md" % (tid, slugify(" ".join(args.title.split()))))
        print("[dry-run] %s -> %s" % (tid, os.path.relpath(path, repo.root)))
        return 0

    try:
        tid, path = create(repo, args.title, eid, edir, args.risk, args.priority,
                           args.effort, parse_ids(args.depends_on), tid=args.id)
    except (ValueError, RuntimeError) as exc:
        sys.stderr.write("%s\n" % exc)
        return 1
    _log(repo, "new", tid)
    print("%s created: %s" % (tid, os.path.relpath(path, repo.root)))
    print("Fill in Context / Approach / Acceptance criteria, then `interfacile lint`.")
    return 0


# --------------------------------------------------------------------------- #
# tickets / ready
# --------------------------------------------------------------------------- #
def _ticket_line(t, by_id):
    open_deps = _open_deps(t, by_id)
    mark = "  ⛔ waits on %s" % ", ".join(open_deps) if open_deps else ""
    return "  %-12s %-8s %5s  P%-2s %s%s" % (
        t.tid, t.status or "?", t.fm.get("effort", ""),
        t.fm.get("priority", "?"), unquote(t.fm.get("title", ""))[:56], mark)


def cmd_tickets(args):
    repo = Repo(_root(args))
    cards, _ = repo.cards()
    by_id = _by_id(cards)
    only_epic = repo.resolve_epic(args.epic)[0] if args.epic else None
    if args.epic and not only_epic:
        sys.stderr.write("unknown epic %r\n" % args.epic)
        return 1

    groups, shown, closed = {}, 0, 0
    for t in cards:
        if repo.is_epic(t.tid):
            continue
        eid = t.fm.get("epic", "") or "(no epic)"
        if only_epic and eid != only_epic:
            continue
        if t.status in DONE:
            closed += 1
            if not args.all:
                continue
        groups.setdefault(eid, []).append(t)
        shown += 1

    if args.json:
        flat = [t for eid in sorted(groups)
                for t in sorted(groups[eid], key=lambda x: x.tid)]
        _emit_json([_card_json(t, by_id, repo.root) for t in flat])
        return 0

    for eid in sorted(groups):
        print("\n%s · %s" % (eid, _epic_title(eid, cards)))
        for t in sorted(groups[eid], key=lambda x: x.tid):
            print(_ticket_line(t, by_id))
    print("\n%d shown · %d closed%s" % (
        shown, closed, "" if args.all else "  (--all to include them)"))
    return 0


def cmd_ready(args):
    """Tickets you can start right now: OPEN, nothing open in depends_on."""
    repo = Repo(_root(args))
    cards, _ = repo.cards()
    by_id = _by_id(cards)
    only_epic = repo.resolve_epic(args.epic)[0] if args.epic else None

    ready = [t for t in cards
             if not repo.is_epic(t.tid) and t.status == "OPEN"
             and (not only_epic or t.fm.get("epic") == only_epic)
             and not _open_deps(t, by_id)]

    def prio(t):
        try:
            return int(t.fm.get("priority", 9))
        except ValueError:
            return 9

    ready.sort(key=lambda x: (prio(x), x.tid))
    if args.json:
        _emit_json([_card_json(t, by_id, repo.root) for t in ready])
        return 0
    if not ready:
        print("Nothing ready — every open ticket is waiting on a dependency.")
        return 0

    print("\n%d ticket(s) ready to start (P1 first):\n" % len(ready))
    for t in ready:
        print(_ticket_line(t, by_id))
    print()
    return 0


# --------------------------------------------------------------------------- #
# show / deps
# --------------------------------------------------------------------------- #
def cmd_show(args):
    repo = Repo(_root(args))
    card, cards = _find(repo, args.ticket_id)
    if not card:
        sys.stderr.write("%s not found\n" % args.ticket_id)
        return 1
    if args.json:
        payload = _card_json(card, _by_id(cards), repo.root)
        payload["frontmatter"] = dict(card.fm)
        payload["body"] = card.body
        _emit_json(payload)
        return 0
    print("# %s\n" % os.path.relpath(card.path, repo.root))
    print(server.read_text(card.path))
    return 0


def cmd_deps(args):
    repo = Repo(_root(args))
    card, cards = _find(repo, args.ticket_id)
    if not card:
        sys.stderr.write("%s not found\n" % args.ticket_id)
        return 1
    by_id = _by_id(cards)

    def label(tid):
        other = by_id.get(tid)
        if not other:
            return "%s (unknown)" % tid
        return "%s [%s] %s" % (tid, other.status,
                               unquote(other.fm.get("title", ""))[:48])

    print("\n%s\n" % label(card.tid))
    deps = card.links("depends_on")
    print("  waits on (depends_on):")
    print("\n".join("    ← %s" % label(d) for d in deps) if deps else "    ← (none)")

    # Reverse edges: explicit `blocks:` plus anything that depends on this card.
    waiting = set(card.links("blocks"))
    waiting.update(t.tid for t in cards if card.tid in t.links("depends_on"))
    print("\n  waiting on this:")
    print("\n".join("    → %s" % label(b) for b in sorted(waiting))
          if waiting else "    → (none)")
    print()
    return 0


# --------------------------------------------------------------------------- #
# close
# --------------------------------------------------------------------------- #
def _set_field(lines, key, value, after=None):
    """Set `key: value` in frontmatter lines, in place. New keys go after
    `after` when that key exists, else at the end — keeps files diff-friendly."""
    for i, line in enumerate(lines):
        if re.match(r"^%s:" % key, line):
            lines[i] = "%s: %s" % (key, value)
            return
    if after is not None:
        for i, line in enumerate(lines):
            if re.match(r"^%s:" % after, line):
                lines.insert(i + 1, "%s: %s" % (key, value))
                return
    lines.append("%s: %s" % (key, value))


def _del_field(lines, key):
    lines[:] = [ln for ln in lines if not re.match(r"^%s:" % key, ln)]


def _rewrite(card, fm_lines, body, bay):
    """Write the card back and file it in the right bay (open/ or closed/).
    Only moves files that live in a bay — companions at the epic root stay."""
    text = "---\n" + "\n".join(fm_lines) + "\n---\n" + body
    dest = card.path
    parent = os.path.dirname(card.path)
    if os.path.basename(parent) in ("open", "closed"):
        dest = os.path.join(os.path.dirname(parent), bay,
                            os.path.basename(card.path))
    with open(card.path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if dest != card.path:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.rename(card.path, dest)
    return dest


def cmd_close(args):
    repo = Repo(_root(args))
    card, cards = _find(repo, args.ticket_id)
    if not card:
        sys.stderr.write("%s not found\n" % args.ticket_id)
        return 1
    if repo.is_epic(card.tid):
        sys.stderr.write("%s is an epic charter — epics don't close, their "
                         "tickets do.\n" % card.tid)
        return 1
    if card.status == "CLOSED":
        print("%s is already closed (%s)." % (card.tid, card.fm.get("closed", "?")))
        return 1
    by_id = _by_id(cards)
    blockers = _open_deps(card, by_id)
    if blockers and not args.force:
        sys.stderr.write("%s still waits on %s — close those first, or pass "
                         "--force.\n" % (card.tid, ", ".join(blockers)))
        return 1

    today = datetime.date.today().isoformat()
    fm_lines, body = server.split_frontmatter(server.read_text(card.path))
    _set_field(fm_lines, "status", "CLOSED")
    _set_field(fm_lines, "closed", today, after="created")
    _set_field(fm_lines, "updated", today)
    if args.note:
        _set_field(fm_lines, "index_note", " ".join(args.note.split()))
    dest = _rewrite(card, fm_lines, body, "closed")
    print("%s closed: %s" % (card.tid, os.path.relpath(dest, repo.root)))
    _log(repo, "close", card.tid)
    _report_unblocked(card, cards, by_id)
    return 0


def _report_unblocked(card, cards, by_id):
    """Finishing a ticket can unblock others — say which, so momentum is visible."""
    unblocked = [t.tid for t in cards
                 if t.status == "OPEN" and card.tid in t.links("depends_on")
                 and not [d for d in _open_deps(t, by_id) if d != card.tid]]
    if unblocked:
        print("now unblocked: %s" % ", ".join(sorted(unblocked)))


def cmd_reopen(args):
    repo = Repo(_root(args))
    card, _ = _find(repo, args.ticket_id)
    if not card:
        sys.stderr.write("%s not found\n" % args.ticket_id)
        return 1
    if card.status not in DONE:
        print("%s is not closed (status %s)." % (card.tid, card.status or "?"))
        return 1

    fm_lines, body = server.split_frontmatter(server.read_text(card.path))
    _set_field(fm_lines, "status", "OPEN")
    # The closure record describes a closure that no longer happened.
    _del_field(fm_lines, "closed")
    _del_field(fm_lines, "index_note")
    _set_field(fm_lines, "updated", datetime.date.today().isoformat())
    dest = _rewrite(card, fm_lines, body, "open")
    print("%s reopened: %s" % (card.tid, os.path.relpath(dest, repo.root)))
    _log(repo, "reopen", card.tid)
    print("Anything that depended on it is blocked again — `interfacile ready` "
          "to see the effect.")
    return 0


def cmd_drop(args):
    """WONT_FIX: dropped on purpose, with the reason on the record. Satisfies
    dependencies exactly like CLOSED — droppers unblock, they don't strand."""
    repo = Repo(_root(args))
    card, cards = _find(repo, args.ticket_id)
    if not card:
        sys.stderr.write("%s not found\n" % args.ticket_id)
        return 1
    if repo.is_epic(card.tid):
        sys.stderr.write("%s is an epic charter — epics don't drop, their "
                         "tickets do.\n" % card.tid)
        return 1
    if card.status in DONE:
        print("%s is already %s." % (card.tid, card.status))
        return 1

    today = datetime.date.today().isoformat()
    fm_lines, body = server.split_frontmatter(server.read_text(card.path))
    _set_field(fm_lines, "status", "WONT_FIX")
    _set_field(fm_lines, "closed", today, after="created")
    _set_field(fm_lines, "updated", today)
    _set_field(fm_lines, "index_note", " ".join(args.why.split()))
    dest = _rewrite(card, fm_lines, body, "closed")
    print("%s dropped (WONT_FIX): %s" % (card.tid, os.path.relpath(dest, repo.root)))
    _log(repo, "drop", card.tid)
    _report_unblocked(card, cards, _by_id(cards))
    return 0


# --------------------------------------------------------------------------- #
# Capture — the on-ramp to a ticket. `todo` reads the checkbox list the pop-out
# writes and ticks an item off once it has become a ticket; `scratch` does the
# same for prose. Indices are 1-based over the *whole* file, done items and all,
# so item 3 is item 3 whichever view you asked for and a write never lands on
# the wrong line.
#
# A captured item stores the ticket's id and nothing else. Status is resolved
# from the board every time it's shown, so a note can't drift out of date.
# --------------------------------------------------------------------------- #
def _board_index(repo):
    cards, _ = repo.cards()
    return {c.tid: (c.status, unquote(c.fm.get("title", ""))) for c in cards}


def _refs(repo, text, index):
    """[(id, status, title)] for every ticket id named in a captured note.
    An id the board doesn't know is reported, not swallowed."""
    return [(tid,) + index.get(tid, ("unknown", ""))
            for tid in repo.link_re.findall(text)]


def _refs_line(refs):
    if not refs:
        return ""
    return "   → " + " · ".join("%s %s" % (tid, st) for tid, st, _ in refs)


def _refs_json(refs):
    return [{"id": tid, "status": st, "title": ti} for tid, st, ti in refs]


def _extend_tail(repo, text, tid, tail_re, opener, closer):
    """Name `tid` at the end of `text`, extending the ids already named there
    (`(A)` -> `(A, B)`) rather than starting a second group. Unchanged if the
    text already names that ticket."""
    if tid in repo.link_re.findall(text):
        return text
    m = tail_re.search(text)
    named = ", ".join(repo.link_re.findall(m.group(0)) + [tid]) if m else tid
    stem = (text[:m.start()] if m else text).rstrip()   # `opener` owns the space
    return "%s%s%s%s" % (stem, opener, named, closer)


def _valid_id(repo, tid):
    """A note is only worth marking with an id this repo could actually resolve
    — otherwise the pointer points nowhere and no listing will ever link it."""
    if repo.id_re.match(tid):
        return True
    sys.stderr.write("%r is not an id here — expected %s-%s.\n"
                     % (tid, repo.prefix, "0" * repo.digits))
    return False


def link_todo_text(repo, text, tid):
    """A ticketed to-do item: `feed the cat (XX-0012)`."""
    return _extend_tail(repo, text, tid, repo.paren_tail_re, " (", ")")


def link_scratch_text(repo, text, tid):
    """A ticketed scratchpad block's last line: `…and a date filter → XX-0012`."""
    return _extend_tail(repo, text, tid, repo.arrow_tail_re, " → ", "")


def cmd_todo(args):
    repo = Repo(_root(args))
    items = server.load_todo(repo.root)
    index = _board_index(repo)

    if args.action == "done":
        if args.n is None:
            sys.stderr.write("which item? `interfacile todo` lists them.\n")
            return 1
        if not 1 <= args.n <= len(items):
            sys.stderr.write("no item %d — the list has %d.\n" % (args.n, len(items)))
            return 1
        before = server.serialize_todo(items)
        it = items[args.n - 1]
        tid = (args.ticket or "").strip().upper()
        if tid:
            if not _valid_id(repo, tid):
                return 1
            it["text"] = link_todo_text(repo, it["text"], tid)
        was_done = it["done"]
        it["done"] = True
        if server.serialize_todo(items) != before:
            server.save_todo(repo.root, items)
        print("%s %d  %s" % ("•" if was_done else "✓", args.n, it["text"]))
        return 0

    shown = [(n, it) for n, it in enumerate(items, 1)
             if args.all or not it["done"]]
    if args.json:
        _emit_json([{"n": n, "done": it["done"], "text": it["text"],
                     "tickets": _refs_json(_refs(repo, it["text"], index))}
                    for n, it in shown])
        return 0
    if not items:
        print("no to-do list yet — add items from the dashboard's 📌 pop-out.")
        return 0
    for n, it in shown:
        print("  [%s] %2d  %s%s" % ("x" if it["done"] else " ", n, it["text"],
                                    _refs_line(_refs(repo, it["text"], index))))
    n_done = sum(1 for it in items if it["done"])
    n_open = len(items) - n_done
    if not shown:
        print("  all done. 🎉")
    print("\n%d open · %d done%s" % (
        n_open, n_done, "" if args.all else "  (--all to include them)"))
    return 0


# --------------------------------------------------------------------------- #
# scratch — the other half of capture. Prose, so the unit is the block (a run of
# non-blank lines) rather than the line, and a ticketed block is annotated in
# place: the note stays, with a pointer to what it became.
# --------------------------------------------------------------------------- #
def _preview(repo, body, width=44):
    """A block's first line, without the `→ ID` tail — ids get their own column."""
    rows = body.splitlines()
    first = repo.arrow_tail_re.sub("", rows[0]).rstrip() if rows else ""
    if len(first) > width:
        first = first[:width - 1].rstrip() + "…"
    more = len(rows) - 1
    return first + ("  (+%d line%s)" % (more, "" if more == 1 else "s")
                    if more > 0 else "")


def cmd_scratch(args):
    repo = Repo(_root(args))
    text = server.load_note_at(repo.root, "scratch")
    lines, spans = server.scratch_blocks(text)
    bodies = [server.block_text(lines, s) for s in spans]

    if args.action == "link":
        if args.n is None or not args.ticket:
            sys.stderr.write("usage: interfacile scratch link N --ticket ID\n")
            return 1
        if not 1 <= args.n <= len(spans):
            sys.stderr.write("no block %d — the scratchpad has %d.\n"
                             % (args.n, len(spans)))
            return 1
        span = spans[args.n - 1]
        body = server.block_text(lines, span)
        tid = args.ticket.strip().upper()
        if not _valid_id(repo, tid):
            return 1
        named = tid in repo.link_re.findall(body)   # anywhere in the block
        if not named:
            # The pointer goes on the block's last line; the rest of the file —
            # blank lines, indentation, trailing newline — is written back as-is.
            last = span[1]
            stem = link_scratch_text(repo, lines[last].rstrip("\r\n"), tid)
            server.save_note_at(repo.root, "scratch",
                                server.set_line(lines, last, stem))
        print("%s %d  %s" % ("•" if named else "✓", args.n, _preview(repo, body)))
        return 0

    index = _board_index(repo)
    if args.json:
        _emit_json([{"n": n, "text": body,
                     "tickets": _refs_json(_refs(repo, body, index))}
                    for n, body in enumerate(bodies, 1)])
        return 0
    if not bodies:
        print("nothing in the scratchpad — write in the dashboard's 📝 pop-out.")
        return 0
    linked = 0
    for n, body in enumerate(bodies, 1):
        refs = _refs(repo, body, index)
        linked += bool(refs)
        print("  %2d  %-46s%s" % (n, _preview(body), _refs_line(refs).lstrip()))
    print("\n%d block%s · %d ticketed" % (
        len(bodies), "" if len(bodies) == 1 else "s", linked))
    return 0


# --------------------------------------------------------------------------- #
# lint
# --------------------------------------------------------------------------- #
def cmd_lint(args):
    repo = Repo(_root(args))
    cards, problems = repo.cards()
    by_id = _by_id(cards)
    epic_dirs = repo.epic_dirs()
    findings = [("ERROR", os.path.basename(p), m, "frontmatter")
                for p, m in problems]

    seen = {}
    for c in cards:
        seen.setdefault(c.tid, []).append(c.path)
    for tid, paths in seen.items():
        if len(paths) > 1:
            findings.append(("ERROR", tid, "duplicate id in %d files: %s"
                             % (len(paths), ", ".join(sorted(paths))), "duplicate"))

    for c in cards:
        def add(level, message, check, _tid=c.tid):
            findings.append((level, _tid, message, check))

        if not repo.id_re.match(c.tid):
            add("ERROR", "malformed id `%s` (expected %s-%s or %s-E1)"
                % (c.tid, repo.prefix, "0" * repo.digits, repo.prefix), "id")
        if not os.path.basename(c.path).startswith(c.tid):
            add("ERROR", "filename `%s` does not start with id"
                % os.path.basename(c.path), "filename")

        required = ("id", "title", "status")
        if not repo.is_epic(c.tid):
            required += ("epic", "created")
        for key in required:
            if key not in c.fm:
                add("ERROR", "missing required field `%s`" % key, "fields")

        if c.status and c.status not in VALID_STATUS:
            add("ERROR", "invalid status `%s` (want %s)"
                % (c.status, "/".join(VALID_STATUS)), "status")

        for key in ("created", "closed", "updated"):
            if key in c.fm and not server.parse_date(c.fm[key]):
                add("ERROR", "`%s: %s` is not a YYYY-MM-DD date"
                    % (key, c.fm[key]), "dates")

        if c.status == "CLOSED" and "closed" not in c.fm:
            add("ERROR", "status CLOSED but no `closed:` date", "closed")
        if c.status in ("OPEN", "STANDING") and "closed" in c.fm:
            add("ERROR", "status %s must not carry `closed:`" % c.status, "closed")

        if repo.is_epic(c.tid):
            continue

        for key in ("risk", "priority", "effort"):
            if key not in c.fm:
                add("WARNING", "no `%s:` set" % key, "fields")
        risk = (c.fm.get("risk") or "").strip().upper()
        if risk and risk not in VALID_RISK:
            add("ERROR", "invalid risk `%s` (want %s)"
                % (risk, "/".join(VALID_RISK)), "risk")
        effort = (c.fm.get("effort") or "").strip()
        if effort and not EFFORT_RE.match(effort):
            add("WARNING", "odd effort `%s` (want 2h / 1d / 1-2d / N/A)"
                % effort, "effort")

        # The folder is presentation, status is truth — so disagreement is a
        # warning with the command that repairs it, not an error.
        parent = os.path.basename(os.path.dirname(c.path))
        if parent == "open" and c.status in DONE:
            add("WARNING", "%s ticket still filed in open/ (run "
                "`interfacile close` next time)" % c.status, "location")
        if parent == "closed" and c.status == "OPEN":
            add("WARNING", "OPEN ticket filed in closed/", "location")

        eid = c.fm.get("epic", "")
        if eid:
            if eid not in epic_dirs:
                add("ERROR", "epic `%s` has no tickets/%s-* directory"
                    % (eid, eid), "epic")
            elif not c.path.startswith(epic_dirs[eid] + os.sep):
                add("ERROR", "epic is `%s` but file is not under its folder"
                    % eid, "epic")

        for key in ("depends_on", "blocks"):
            for dep in c.links(key):
                if dep not in by_id:
                    add("ERROR", "`%s` references unknown %s" % (key, dep), key)
        for dep in c.links("depends_on"):
            other = by_id.get(dep)
            if dep in c.links("blocks") or (other and c.tid in other.links("depends_on")):
                add("ERROR", "circular dependency with %s" % dep, "deps")
            if c.status == "CLOSED" and other and other.status == "OPEN":
                add("WARNING", "CLOSED but depends on OPEN %s" % dep, "deps")

    errors = [f for f in findings if f[0] == "ERROR"]
    warnings = [f for f in findings if f[0] == "WARNING"]
    from . import scaffold
    notice = scaffold.stale_notice(repo.root)

    if args.json:
        def as_dict(f):
            return {"ticket": f[1], "check": f[3], "message": f[2]}
        _emit_json({"files": len(cards),
                    "errors": [as_dict(f) for f in errors],
                    "warnings": [as_dict(f) for f in warnings],
                    "skills_notice": notice})
        return 1 if errors else 0

    print("Linted %d ticket file(s)." % len(cards))
    for level, tid, message, check in errors + warnings:
        print("  %s %-14s [%s] %s"
              % ("✗" if level == "ERROR" else "!", tid, check, message))
    if not findings:
        print("  ✓ clean")
    print("%d error(s), %d warning(s)" % (len(errors), len(warnings)))
    if notice:
        print(notice)
    return 1 if errors else 0


# --------------------------------------------------------------------------- #
# CLI wiring — cli.py calls add_commands(sub) and dispatches via args.func.
# --------------------------------------------------------------------------- #
COMMANDS = ("new", "tickets", "ready", "show", "deps", "close", "reopen",
            "drop", "lint", "todo", "scratch")


def add_commands(sub):
    def parser(name, helptext, func):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--repo", default=None, metavar="PATH",
                       help="repo root (default: current directory)")
        p.set_defaults(func=func)
        return p

    np = parser("new", "create a ticket in an epic's open/ folder", cmd_new)
    np.add_argument("title")
    np.add_argument("--epic", required=True,
                    help="epic id — E1, E001, and PFX-E001 all work")
    np.add_argument("--id", default=None, help="ticket id (default: next free)")
    np.add_argument("--risk", default="LOW", choices=VALID_RISK)
    np.add_argument("--priority", default=3, type=int, choices=range(1, 6),
                    metavar="1-5")
    np.add_argument("--effort", default="2h", help="30m 1h 2h 4h 1d 2d 1w ...")
    np.add_argument("--depends-on", default="", metavar="ID,ID",
                    help="tickets that must close before this one starts")
    np.add_argument("--dry-run", action="store_true")

    tp = parser("tickets", "list the board, grouped by epic", cmd_tickets)
    tp.add_argument("--epic", default=None, help="only this epic")
    tp.add_argument("--all", action="store_true", help="include closed tickets")
    tp.add_argument("--json", action="store_true", help="machine-readable output")

    rp = parser("ready", "open tickets with no open dependencies", cmd_ready)
    rp.add_argument("--epic", default=None, help="only this epic")
    rp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = parser("show", "print one ticket (path + contents)", cmd_show)
    sp.add_argument("ticket_id")
    sp.add_argument("--json", action="store_true",
                    help="frontmatter + body as JSON")
    dp = parser("deps", "what a ticket waits on / what waits on it", cmd_deps)
    dp.add_argument("ticket_id")

    cp = parser("close", "set status CLOSED + dates, move to closed/", cmd_close)
    cp.add_argument("ticket_id")
    cp.add_argument("--note", default=None,
                    help="one line on what shipped (stored as index_note)")
    cp.add_argument("--force", action="store_true",
                    help="close even if dependencies are still open")

    op = parser("reopen", "undo a close: status OPEN, back to open/", cmd_reopen)
    op.add_argument("ticket_id")

    wp = parser("drop", "WONT_FIX: dropped on purpose, reason recorded", cmd_drop)
    wp.add_argument("ticket_id")
    wp.add_argument("--why", required=True,
                    help="one line on why this will not be done")

    lp = parser("lint", "validate every ticket against the standard", cmd_lint)
    lp.add_argument("--json", action="store_true", help="machine-readable output")

    tdp = parser("todo", "list the to-do pop-out's items, or tick one off", cmd_todo)
    tdp.add_argument("action", nargs="?", default="list", choices=("list", "done"))
    tdp.add_argument("n", nargs="?", type=int, metavar="N",
                     help="item number (from `interfacile todo`)")
    tdp.add_argument("--ticket", default=None, metavar="ID",
                     help="the ticket it became — appended to the item as (ID)")
    tdp.add_argument("--all", action="store_true", help="include done items")
    tdp.add_argument("--json", action="store_true", help="machine-readable output")

    scp = parser("scratch", "list the scratchpad's blocks, or point one at a "
                            "ticket", cmd_scratch)
    scp.add_argument("action", nargs="?", default="list", choices=("list", "link"))
    scp.add_argument("n", nargs="?", type=int, metavar="N",
                     help="block number (from `interfacile scratch`)")
    scp.add_argument("--ticket", default=None, metavar="ID",
                     help="the ticket it became — appended to the block as → ID")
    scp.add_argument("--json", action="store_true", help="machine-readable output")

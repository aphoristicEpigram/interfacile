"""Ticket interactions, on the record — the plumbing automations hang off.

Every interaction that changes a ticket — pinned, reviewed, moved, closed —
appends one event to a single hub-wide log:

    ~/.config/interfacile/events.json

Hub-wide, not per-repo, is the whole point: an automation wants to hear "a
ticket closed" across every project you track, not go looking in each one. The
server records the interactions it serves; the CLI records the ones it performs;
both write the same file, so `interfacile hub`, `interfacile close`, and a
dashboard click all land in one stream.

The file carries three things: a monotonic `seq` (a cursor an automation polls
from), `counts` (how many of each kind, ever — global and per interface), and
`recent` (the last few hundred events, newest last). Counts are cumulative and
never trimmed; `recent` is capped, because a log that grows forever is a leak,
and anything that needs full history should be reading the git log instead.

A poller asks `GET /api/events?since=<seq>` and gets whatever happened after
its cursor. Nothing here decides what an automation *does* — that's the point:
this is the plumbing, and the policy lives wherever you want it to.
"""
import datetime
import json
import os

# Kinds are open — a new interaction just starts recording its own name — but
# these are the ones we emit today, and the set an automation can rely on.
KINDS = ("pin", "unpin", "review", "unreview", "reorder",
         "new", "close", "reopen", "drop")

MAX_RECENT = 500

_EMPTY = {"seq": 0, "counts": {}, "interfaces": {}, "recent": []}


def events_file():
    base = (os.environ.get("XDG_CONFIG_HOME")
            or os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "interfacile", "events.json")


def load():
    """The whole log. A missing or unreadable file reads as an empty one — a
    counter that has never counted, which is exactly what it is."""
    try:
        with open(events_file(), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return dict(_EMPTY, counts={}, interfaces={}, recent=[])
    if not isinstance(d, dict):
        return dict(_EMPTY, counts={}, interfaces={}, recent=[])
    return {"seq": int(d.get("seq") or 0),
            "counts": d.get("counts") if isinstance(d.get("counts"), dict) else {},
            "interfaces": (d.get("interfaces")
                           if isinstance(d.get("interfaces"), dict) else {}),
            "recent": d.get("recent") if isinstance(d.get("recent"), list) else []}


def _save(log):
    path = events_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(log, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def record(kind, ticket=None, interface=None, repo=None, **extra):
    """Append one interaction and bump its counters. Returns the event.

    Never raises: an automation hook that cannot write its log must not be the
    reason a pin, a review tick, or a close fails."""
    try:
        log = load()
        log["seq"] += 1
        ev = {"seq": log["seq"],
              "at": datetime.datetime.now().isoformat(timespec="seconds"),
              "kind": kind}
        if ticket:
            ev["id"] = ticket
        if interface:
            ev["interface"] = interface
        if repo:
            ev["repo"] = repo
        ev.update(extra)
        log["counts"][kind] = log["counts"].get(kind, 0) + 1
        if interface:
            per = log["interfaces"].setdefault(interface, {})
            per[kind] = per.get(kind, 0) + 1
        log["recent"] = (log["recent"] + [ev])[-MAX_RECENT:]
        _save(log)
        return ev
    except Exception:
        return None


def counts(interface=None):
    """How many of each kind — hub-wide, or for one interface."""
    log = load()
    if interface is None:
        return dict(log["counts"])
    return dict(log["interfaces"].get(interface, {}))


def since(seq):
    """Events after a cursor, oldest first — what an automation polls for.

    A cursor older than the retained window can't be honoured from `recent`;
    the caller gets what we still hold, and `seq` tells them where they are."""
    return [e for e in load()["recent"] if int(e.get("seq") or 0) > int(seq or 0)]

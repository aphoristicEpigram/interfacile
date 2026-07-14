"""Every running server, on the record.

A tool that spawns processes owes you a way to find them again. Each server
writes one small file — pid, port, url, the repos it serves — into

    ~/.config/interfacile/servers/<pid>.json

and removes it on the way out. `interfacile ps` reads them; `interfacile stop`
ends them; `serve`/`hub` read them to avoid starting a second server for a repo
that already has one.

A record is a claim, not a fact — `kill -9` leaves one behind. So every read
checks the process is really there and deletes the record if it isn't: a stale
file is a lie, and lies are cleaned up rather than reported.
"""
import errno
import glob
import json
import os
import signal
import time


def servers_dir():
    base = (os.environ.get("XDG_CONFIG_HOME")
            or os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "interfacile", "servers")


def _record_path(pid):
    return os.path.join(servers_dir(), "%d.json" % pid)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def alive(pid):
    """Is this process still there? Signal 0 asks without sending anything."""
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM      # exists, just not ours to signal
    return True


def register(port, url, roots, pid=None):
    pid = os.getpid() if pid is None else pid
    rec = {"pid": pid, "port": port, "url": url,
           "roots": [os.path.abspath(r) for r in roots],
           "started": time.time()}
    os.makedirs(servers_dir(), exist_ok=True)
    path = _record_path(pid)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rec, fh, indent=1)
    os.replace(tmp, path)
    return rec


def unregister(pid=None):
    _remove(_record_path(os.getpid() if pid is None else pid))


def servers():
    """Every live server, newest last. Dead records are swept as we go."""
    out = []
    for path in glob.glob(os.path.join(servers_dir(), "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
            pid = int(rec["pid"])
        except (OSError, ValueError, TypeError, KeyError):
            _remove(path)                     # unreadable is as good as dead
            continue
        if alive(pid):
            out.append(rec)
        else:
            _remove(path)
    return sorted(out, key=lambda r: r.get("started", 0))


def serving(root):
    """The live servers that serve this repo."""
    root = os.path.abspath(root)
    return [r for r in servers() if root in r.get("roots", [])]


def stop(records, sig=signal.SIGTERM):
    """Ask each server to stop. It removes its own record on the way out; if it
    can't, the next read sweeps it — either way you don't have to care."""
    stopped = []
    for rec in records:
        try:
            os.kill(int(rec["pid"]), sig)
        except (OSError, ValueError, TypeError):
            _remove(_record_path(rec.get("pid", -1)))
        else:
            stopped.append(rec)
    return stopped

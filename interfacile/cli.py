"""interfacile command-line interface.

    interfacile                       serve the current directory
    interfacile serve  [--repo P]     serve one repo (default: current directory)
    interfacile hub    [--repo P ...] serve several repos with a switcher
                                      (no --repo => every registered interface)

    interfacile init   [PATH]         scaffold a repo's .interfacile/config.json + register it
    interfacile register   [PATH]     add a repo to the hub registry
    interfacile unregister [PATH]     remove a repo from the registry
    interfacile list                  list registered interfaces

Common serve flags: --port N  --host H  --no-open
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

from . import __version__
from . import server


# --------------------------------------------------------------------------- #
# Registry — the set of repos `interfacile hub` serves by default.
# --------------------------------------------------------------------------- #
def registry_path():
    base = (os.environ.get("XDG_CONFIG_HOME")
            or os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "interfacile", "registry.json")


def _registry_load():
    try:
        with open(registry_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError):
        data = {}
    if isinstance(data, list):          # tolerate a bare list
        data = {"repos": data}
    data.setdefault("repos", [])
    data["repos"] = [r for r in data["repos"] if isinstance(r, str)]
    return data


def _registry_save(data):
    path = registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def registry_repos():
    return _registry_load()["repos"]


def _register(root):
    root = os.path.abspath(root)
    data = _registry_load()
    if root in data["repos"]:
        return False
    data["repos"].append(root)
    _registry_save(data)
    return True


def _unregister(root):
    root = os.path.abspath(root)
    data = _registry_load()
    if root not in data["repos"]:
        return False
    data["repos"].remove(root)
    _registry_save(data)
    return True


# --------------------------------------------------------------------------- #
# init — scaffold a repo's config and register it
# --------------------------------------------------------------------------- #
def _infer_prefix(root):
    """Guess the ticket id prefix from existing ticket filenames (TH-0004 -> TH)."""
    for f in glob.glob(os.path.join(root, "tickets", "**", "*.md"), recursive=True):
        m = re.match(r"([A-Za-z]{1,6})-E?\d{2,}", os.path.basename(f))
        if m:
            return m.group(1).upper()
    return None


def _default_config(root):
    name = os.path.basename(root.rstrip("/")).replace("-", " ").replace("_", " ").title()
    prefix = _infer_prefix(root) or (re.sub(r"[^A-Za-z]", "", name)[:2].upper() or "TK")
    return {
        "brand": {"name": name, "favicon": "🎟️", "icon": "🎟️",
                  "eyebrow": "Ticket portfolio · engineering program",
                  "tagline": "This project is tracked as"},
        "ids": {"prefix": prefix, "digits": 4},
        "epics": {},
        "theme": "blue",
        "server": {"port": 8787},
    }


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


_GITIGNORE_BLOCK = (
    "# interfacile: commit the config, ignore the runtime state it writes\n"
    "# (pins, scratchpad, to-do)\n"
    ".interfacile/*\n"
    "!.interfacile/config.json\n"
)


def _ensure_gitignore(root):
    """Keep the pins/scratchpad/to-do interfacile writes out of git, while still
    tracking config.json. Idempotent — appends only if the rule isn't there."""
    path = os.path.join(root, ".gitignore")
    try:
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
    except FileNotFoundError:
        existing = ""
    if ".interfacile/*" in existing:
        return False
    lead = "" if (not existing or existing.endswith("\n")) else "\n"
    if existing:
        lead += "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(lead + _GITIGNORE_BLOCK)
    return True


def _seed_tickets(root, prefix):
    """Create a minimal, valid starter tickets/ tree so a fresh repo has a
    populated board immediately: one epic charter + one open ticket."""
    today = datetime.date.today().isoformat()
    epic_dir = os.path.join(root, "tickets", prefix + "-E001-getting-started")
    os.makedirs(os.path.join(epic_dir, "open"), exist_ok=True)
    os.makedirs(os.path.join(epic_dir, "closed"), exist_ok=True)
    _write(os.path.join(epic_dir, prefix + "-E001-getting-started.md"),
           "---\nid: %s-E001\ntitle: Getting Started\nstatus: OPEN\n"
           "index_exempt: true\n---\n\n# %s-E001 · Getting Started\n\n"
           "Your first epic. Group related tickets in one epic folder whose name "
           "starts with the epic id. Delete this once you've added your own.\n"
           % (prefix, prefix))
    _write(os.path.join(epic_dir, "open", prefix + "-0001-your-first-ticket.md"),
           "---\nid: %s-0001\ntitle: Your first ticket\nepic: %s-E001\n"
           "status: OPEN\nrisk: LOW\npriority: 2\neffort: 4h\ncreated: %s\n"
           "updated: %s\n---\n\n# %s-0001 · Your first ticket\n\n"
           "Edit this file and refresh the dashboard — it re-scans on every "
           "request. The `open/` and `closed/` folders are just for humans; a "
           "ticket's real state is its `status:` field.\n"
           % (prefix, prefix, today, today, prefix))
    return epic_dir


def cmd_init(args):
    root = os.path.abspath(args.path or os.getcwd())
    if not os.path.isdir(root):
        sys.exit("interfacile init: no such directory: " + root)
    cfg = os.path.join(root, server.CONFIG_REL)
    existing = server.load_config(root)
    if existing:
        conf = existing
        print("• config already present — leaving it untouched")
    else:
        conf = _default_config(root)
        _write(cfg, json.dumps(conf, indent=2, ensure_ascii=False) + "\n")
        print("• wrote %s  (prefix=%s, brand=%r, theme=blue)"
              % (cfg, conf["ids"]["prefix"], conf["brand"]["name"]))
    if _ensure_gitignore(root):
        print("• .gitignore: ignoring .interfacile/ state (pins, scratchpad, to-do), "
              "keeping config.json")
    prefix = conf.get("ids", {}).get("prefix", "TK")
    if not os.path.isdir(os.path.join(root, "tickets")):
        _seed_tickets(root, prefix)
        print("• seeded a starter tickets/ tree (%s-E001-getting-started, %s-0001)"
              % (prefix, prefix))
    print("• registered with the hub" if _register(root) else "• already registered")
    print("\nDone. Run `interfacile` here, or `interfacile hub` from anywhere.")


def cmd_register(args):
    root = os.path.abspath(args.path or os.getcwd())
    print(("registered" if _register(root) else "already registered") + "  ->  " + root)


def cmd_unregister(args):
    root = os.path.abspath(args.path or os.getcwd())
    print(("unregistered" if _unregister(root) else "not in registry") + "  ->  " + root)


def cmd_list(args):
    repos = registry_repos()
    if not repos:
        print("no interfaces registered.  (registry: %s)" % registry_path())
        return
    print("registered interfaces (%s):" % registry_path())
    for r in repos:
        conf = server.load_config(r)
        name = conf.get("brand", {}).get("name") or os.path.basename(r)
        icon = conf.get("brand", {}).get("icon") or conf.get("brand", {}).get("favicon") or "•"
        flag = "" if os.path.isdir(os.path.join(r, "tickets")) else "   [!] missing tickets/"
        print("  %s  %-22s %s%s" % (icon, name, r, flag))


# --------------------------------------------------------------------------- #
# serve / hub
# --------------------------------------------------------------------------- #
def _add_serve_flags(p):
    p.add_argument("--port", type=int, default=None, help="listen port")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    p.add_argument("--no-open", action="store_true", help="do not auto-open a browser")


def _serve(repos, args):
    valid = [r for r in repos if os.path.isdir(os.path.join(r, "tickets"))]
    for r in repos:
        if r not in valid:
            sys.stderr.write("skipping (no tickets/): %s\n" % r)
    if not valid:
        sys.exit("interfacile: no repos with a tickets/ folder to serve")
    server.run(valid, args.port, args.host, not args.no_open)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        prog="interfacile",
        description="Themeable ticket-portfolio interfaces, one switchable hub.")
    ap.add_argument("--version", action="version", version="interfacile " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("serve", help="serve one repo (default: current directory)")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="repo root to serve (default: current directory)")
    _add_serve_flags(sp)

    hp = sub.add_parser("hub", help="serve several repos with a switcher")
    hp.add_argument("--repo", action="append", default=None, metavar="PATH",
                    help="repo root; repeat for several (switcher order = flag "
                         "order). If omitted, serves every registered interface.")
    _add_serve_flags(hp)

    ip = sub.add_parser("init", help="scaffold a repo's .interfacile/config.json and register it")
    ip.add_argument("path", nargs="?", default=None, help="repo root (default: current dir)")

    rp = sub.add_parser("register", help="add a repo to the hub registry")
    rp.add_argument("path", nargs="?", default=None)
    up = sub.add_parser("unregister", help="remove a repo from the hub registry")
    up.add_argument("path", nargs="?", default=None)
    sub.add_parser("list", help="list registered interfaces")

    # Bare `interfacile` (or leading flags with no subcommand) serves the cwd.
    known = ("serve", "hub", "init", "register", "unregister", "list",
             "-h", "--help", "--version")
    if not argv or argv[0] not in known:
        args = ap.parse_args(["serve"] + argv)
    else:
        args = ap.parse_args(argv)

    if args.cmd == "hub":
        repos = args.repo or registry_repos()
        if not repos:
            sys.exit("interfacile hub: no repos. Pass --repo PATH (repeatable), or "
                     "`interfacile init`/`register` some (registry: %s)." % registry_path())
        _serve([os.path.abspath(r) for r in repos], args)
    elif args.cmd == "init":
        cmd_init(args)
    elif args.cmd == "register":
        cmd_register(args)
    elif args.cmd == "unregister":
        cmd_unregister(args)
    elif args.cmd == "list":
        cmd_list(args)
    else:  # serve
        _serve([os.path.abspath(args.repo or os.getcwd())], args)


if __name__ == "__main__":
    main()

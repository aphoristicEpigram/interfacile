"""interfacile command-line interface.

    interfacile                       serve the current directory
    interfacile serve  [--repo P]     serve one repo (default: current directory)
    interfacile hub    [--repo P ...] serve several repos with a switcher
                                      (no --repo => every registered interface)

    interfacile init   [PATH]         scaffold a repo's .interfacile/config.json + register it
                                      (offers a wizard to create your first epics)
    interfacile epics  [PATH]         create more epics interactively (folder + charter)
    interfacile skills [PATH]         install/refresh the ticket-flow skills + process doc
    interfacile shortcut [N]          show this repo's hub key, or move it to position N
                                      (keys are positional: first ten repos get 1-9, 0)
    interfacile register   [PATH]     add a repo to the hub registry
    interfacile unregister [PATH]     remove a repo from the registry
    interfacile list                  list registered interfaces

The ticket flow (see `interfacile <cmd> -h`):
    new · tickets · ready · show · deps · close · lint
    todo · scratch                    capture: the pop-out notes, and the
                                      tickets they became

Common serve flags: --port N  --host H  --no-open
"""
import argparse
import datetime
import json
import os
import re
import sys

from . import __version__
from . import scaffold
from . import server
from . import ticket
from .ticket import fallback_prefix as _fallback_prefix
from .ticket import infer_prefix as _infer_prefix
from .ticket import slugify as _slugify


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
def _default_config(root, prefix=None):
    name = os.path.basename(root.rstrip("/")).replace("-", " ").replace("_", " ").title()
    prefix = prefix or _infer_prefix(root) or _fallback_prefix(root)
    # Name the epics this repo actually has. Leaving this empty makes the engine
    # fall back to *its* defaults, which is how one project's epic names would
    # end up on another project's board.
    epics = {code: {"title": title, "emoji": "🎟️"} for code, title
             in server.discover_epic_meta(os.path.join(root, "tickets"), prefix).items()}
    return {
        "brand": {"name": name, "favicon": "🎟️", "icon": "🎟️",
                  "eyebrow": "Ticket portfolio · engineering program",
                  "tagline": "This project is tracked as"},
        "ids": {"prefix": prefix, "digits": 4},
        "epics": epics,
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


# --------------------------------------------------------------------------- #
# Epic wizard — create bare-bones epics (folder + charter) interactively.
# --------------------------------------------------------------------------- #
def _next_code(taken):
    n = 1
    while ("E%03d" % n) in taken:
        n += 1
    return "E%03d" % n


def _create_epic(root, prefix, code, title, emoji):
    """A bare-bones epic: the folder, its open/ + closed/ bays, and a charter
    whose front-matter title is what the board will show."""
    slug = "%s-%s-%s" % (prefix, code, _slugify(title))
    epic_dir = os.path.join(root, "tickets", slug)
    os.makedirs(os.path.join(epic_dir, "open"), exist_ok=True)
    os.makedirs(os.path.join(epic_dir, "closed"), exist_ok=True)
    charter = os.path.join(epic_dir, slug + ".md")
    if not os.path.exists(charter):
        _write(charter,
               "---\nid: %s-%s\ntitle: %s\nstatus: OPEN\nindex_exempt: true\n---\n\n"
               "# %s-%s · %s %s\n\n_What this epic covers, and what \"done\" looks "
               "like._\n\nDrop tickets in `open/`; move them to `closed/` when you "
               "like — a ticket's real state is its `status:` field, not the folder.\n"
               % (prefix, code, title, prefix, code, emoji, title))
    return epic_dir


def _ask(question, default=""):
    try:
        return input(question).strip() or default
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _ask_yn(question, default=True):
    if not sys.stdin.isatty():
        return False                      # never prompt in a pipe/CI
    ans = _ask("%s [%s] " % (question, "Y/n" if default else "y/N")).lower()
    return default if not ans else ans.startswith("y")


def _epic_wizard(root, prefix, taken):
    """Prompt for epics until a blank line. Returns {"E001": {title, emoji}, ...}."""
    print("\n  One epic per line, blank line when you're done.")
    print("  Format:  Title            (or)   Title | 🎭\n")
    made = {}
    taken = set(taken)
    while True:
        code = _next_code(taken)
        line = _ask("  %s-%s  title> " % (prefix, code))
        if not line:
            break
        title, _, emoji = line.partition("|")
        title, emoji = title.strip(), emoji.strip() or "🎟️"
        if not title:
            continue
        _create_epic(root, prefix, code, title, emoji)
        made[code] = {"title": title, "emoji": emoji}
        taken.add(code)
        print("      ✓ created tickets/%s-%s-%s/  %s %s"
              % (prefix, code, _slugify(title), emoji, title))
    return made


def _merge_epics_into_config(root, made):
    """Fold wizard-created epics into an existing config.json, preserving the rest."""
    path = os.path.join(root, server.CONFIG_REL)
    try:
        with open(path, encoding="utf-8") as fh:
            conf = json.load(fh)
    except (FileNotFoundError, ValueError):
        return False
    epics = conf.get("epics") or {}
    epics.update(made)
    conf["epics"] = epics
    _write(path, json.dumps(conf, indent=2, ensure_ascii=False) + "\n")
    return True


def cmd_epics(args):
    """Add epics to a repo that's already set up."""
    root = os.path.abspath(args.path or os.getcwd())
    conf = server.load_config(root)
    if not conf:
        sys.exit("interfacile epics: no .interfacile/config.json here — run "
                 "`interfacile init` first.")
    prefix = conf.get("ids", {}).get("prefix", "TK")
    taken = server.discover_epic_meta(os.path.join(root, "tickets"), prefix)
    if taken:
        print("existing epics: " + ", ".join(
            "%s-%s %s" % (prefix, c, t) for c, t in sorted(taken.items())))
    made = _epic_wizard(root, prefix, taken)
    if not made:
        print("nothing to do.")
        return
    _merge_epics_into_config(root, made)
    print("\n• created %d epic(s) and added them to %s"
          % (len(made), os.path.join(server.CONFIG_REL)))
    print("Refresh the dashboard — no restart needed.")


def _install_skills(root, prefix, force=False):
    """Drop the ticket-flow skills + process doc into the repo and report."""
    done = scaffold.install(root, prefix, force=force)
    changed = [(a, p) for a, p in done if a != "kept"]
    for action, rel in changed:
        print("• %s %s" % (action, rel))
    if not changed:
        print("• skills + process doc already up to date")


def cmd_skills(args):
    root = os.path.abspath(args.path or os.getcwd())
    if not os.path.isdir(root):
        sys.exit("interfacile skills: no such directory: " + root)
    prefix, _ = ticket.id_scheme(root)
    _install_skills(root, prefix, force=args.force)


def _position_key(i):
    """Hub keys are positional: 1-9 then 0 for the first ten, nothing after."""
    return "1234567890"[i] if i < 10 else ""


def _print_order(repos, root=None):
    for i, r in enumerate(repos):
        key = _position_key(i)
        mark = "  <- here" if root and os.path.abspath(r) == root else ""
        print("  [%s] %s%s" % (key or " ", r, mark))


def cmd_shortcut(args):
    """Show or set this repo's hub key. Keys are positional — `shortcut 3`
    means "move to position 3" — so this is just registry reordering; the
    same thing as dragging the interface in the hub's switcher menu."""
    root = os.path.abspath(args.repo or os.getcwd())
    data = _registry_load()
    repos = data["repos"]
    if root not in repos:
        sys.exit("interfacile shortcut: %s is not registered — run "
                 "`interfacile init` or `interfacile register` first." % root)

    if args.key is None:
        key = _position_key(repos.index(root))
        print("shortcut: %s  (keys follow hub order; move with "
              "`interfacile shortcut N` or drag in the switcher)"
              % (key or "none — position %d" % (repos.index(root) + 1)))
        _print_order(repos, root)
        return

    key = args.key.strip()
    if key not in "1234567890" or len(key) != 1:
        sys.exit("interfacile shortcut: pass a digit 1-9, or 0 for the tenth slot.")
    pos = 9 if key == "0" else int(key) - 1
    repos.remove(root)
    repos.insert(min(pos, len(repos)), root)
    _registry_save(data)
    print("press '%s' anywhere in the hub to switch here. New order:" % key)
    _print_order(repos, root)
    print("(a running hub picks this up on the next refresh)")


def cmd_init(args):
    root = os.path.abspath(args.path or os.getcwd())
    if not os.path.isdir(root):
        sys.exit("interfacile init: no such directory: " + root)
    cfg = os.path.join(root, server.CONFIG_REL)
    existing = server.load_config(root)
    prefix = ((existing.get("ids", {}).get("prefix") if existing else None)
              or _infer_prefix(root) or _fallback_prefix(root))

    # Epics/tickets first: the config we write below names the epics it finds, so
    # it can only do that once they exist.
    have = server.discover_epic_meta(os.path.join(root, "tickets"), prefix)
    made = {}
    if not have:
        if not args.no_wizard and _ask_yn("\nCreate some epics now?", default=True):
            made = _epic_wizard(root, prefix, have)
        if not made:
            _seed_tickets(root, prefix)
            print("• seeded a starter tickets/ tree (%s-E001-getting-started, %s-0001)"
                  % (prefix, prefix))

    if existing:
        print("• config already present — leaving it untouched")
        if made:
            _merge_epics_into_config(root, made)
            print("• added %d epic(s) to the existing config" % len(made))
    else:
        conf = _default_config(root, prefix)
        conf["epics"].update(made)        # keep the emoji the wizard collected
        _write(cfg, json.dumps(conf, indent=2, ensure_ascii=False) + "\n")
        print("• wrote %s  (prefix=%s, brand=%r, theme=blue, epics=%d)"
              % (cfg, prefix, conf["brand"]["name"], len(conf["epics"])))
    if not args.no_skills:
        _install_skills(root, prefix)
    if _ensure_gitignore(root):
        print("• .gitignore: ignoring .interfacile/ state (pins, scratchpad, to-do), "
              "keeping config.json")
    print("• registered with the hub" if _register(root) else "• already registered")
    print("\nDone. Run `interfacile` here, or `interfacile hub` from anywhere.")
    print("The flow: interfacile new / tickets / ready / close / lint "
          "(tickets/README.md explains it).")


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
    for i, r in enumerate(repos):
        conf = server.load_config(r)
        name = conf.get("brand", {}).get("name") or os.path.basename(r)
        icon = conf.get("brand", {}).get("icon") or conf.get("brand", {}).get("favicon") or "•"
        key = _position_key(i)
        flag = "" if os.path.isdir(os.path.join(r, "tickets")) else "   [!] missing tickets/"
        print("  [%s] %s %-22s %s%s" % (key or " ", icon, name, r, flag))


# --------------------------------------------------------------------------- #
# serve / hub
# --------------------------------------------------------------------------- #
def _add_serve_flags(p):
    p.add_argument("--port", type=int, default=None, help="listen port")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    p.add_argument("--no-open", action="store_true", help="do not auto-open a browser")


def _serve(repos, args, registry_file=None):
    valid = [r for r in repos if os.path.isdir(os.path.join(r, "tickets"))]
    for r in repos:
        if r not in valid:
            sys.stderr.write("skipping (no tickets/): %s\n" % r)
    if not valid:
        sys.exit("interfacile: no repos with a tickets/ folder to serve")
    server.run(valid, args.port, args.host, not args.no_open,
               registry_file=registry_file)


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
    ip.add_argument("--no-wizard", action="store_true",
                    help="don't offer the epic wizard; just seed a starter tree")
    ip.add_argument("--no-skills", action="store_true",
                    help="don't install the ticket-flow skills + process doc")

    ep = sub.add_parser("epics", help="create epics interactively (folder + charter)")
    ep.add_argument("path", nargs="?", default=None, help="repo root (default: current dir)")

    kp = sub.add_parser("skills", help="install/refresh the ticket-flow skills + process doc")
    kp.add_argument("path", nargs="?", default=None, help="repo root (default: current dir)")
    kp.add_argument("--force", action="store_true",
                    help="also overwrite tickets/README.md with the packaged version")

    cp = sub.add_parser("shortcut", help="show/set this repo's hub key (keys are "
                                         "positional: 1-9 then 0)")
    cp.add_argument("key", nargs="?", default=None,
                    help="digit 1-9 (0 = tenth slot) — moves this repo to that "
                         "position; omit to show the current key")
    cp.add_argument("--repo", default=None, metavar="PATH",
                    help="repo root (default: current directory)")

    ticket.add_commands(sub)

    rp = sub.add_parser("register", help="add a repo to the hub registry")
    rp.add_argument("path", nargs="?", default=None)
    up = sub.add_parser("unregister", help="remove a repo from the hub registry")
    up.add_argument("path", nargs="?", default=None)
    sub.add_parser("list", help="list registered interfaces")

    # Bare `interfacile` (or leading flags with no subcommand) serves the cwd.
    known = (("serve", "hub", "init", "epics", "skills", "shortcut",
              "register", "unregister", "list",
              "-h", "--help", "--version") + ticket.COMMANDS)
    if not argv or argv[0] not in known:
        args = ap.parse_args(["serve"] + argv)
    else:
        args = ap.parse_args(argv)

    if args.cmd == "hub":
        repos = args.repo or registry_repos()
        if not repos:
            sys.exit("interfacile hub: no repos. Pass --repo PATH (repeatable), or "
                     "`interfacile init`/`register` some (registry: %s)." % registry_path())
        # Registry-driven hubs follow registry edits live; explicit --repo
        # lists are pinned to exactly what was asked for.
        _serve([os.path.abspath(r) for r in repos], args,
               registry_file=None if args.repo else registry_path())
    elif args.cmd in ticket.COMMANDS:
        sys.exit(args.func(args))
    elif args.cmd == "init":
        cmd_init(args)
    elif args.cmd == "epics":
        cmd_epics(args)
    elif args.cmd == "skills":
        cmd_skills(args)
    elif args.cmd == "shortcut":
        cmd_shortcut(args)
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

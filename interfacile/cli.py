"""interfacile command-line interface.

    interfacile                      serve the current directory
    interfacile serve  [--repo P]    serve one repo (default: current directory)
    interfacile hub    [--repo P ...] serve several repos with a switcher
                                     (no --repo => use the registry file)

Common flags: --port N  --host H  --no-open
"""
import argparse
import json
import os
import sys

from . import __version__
from . import server


def registry_path():
    base = (os.environ.get("XDG_CONFIG_HOME")
            or os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "interfacile", "registry.json")


def registry_repos():
    """Repo roots recorded in the user's registry file (`interfacile init` in a
    later phase writes it). Accepts either a bare JSON list or {"repos": [...]}."""
    try:
        with open(registry_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError):
        return []
    repos = data.get("repos", []) if isinstance(data, dict) else data
    return [r for r in repos if isinstance(r, str)]


def _add_serve_flags(p):
    p.add_argument("--port", type=int, default=None, help="listen port")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    p.add_argument("--no-open", action="store_true", help="do not auto-open a browser")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        prog="interfacile",
        description="Themeable ticket-portfolio interfaces, one switchable hub.")
    ap.add_argument("--version", action="version",
                    version="interfacile " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("serve", help="serve one repo (default: current directory)")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="repo root to serve (default: current directory)")
    _add_serve_flags(sp)

    hp = sub.add_parser("hub", help="serve several repos with a switcher")
    hp.add_argument("--repo", action="append", default=None, metavar="PATH",
                    help="repo root; repeat for several (switcher order = flag "
                         "order). If omitted, uses your registry file.")
    _add_serve_flags(hp)

    # Bare `interfacile` (or `interfacile --port ...` with no subcommand) serves
    # the current directory.
    if not argv or argv[0] not in ("serve", "hub", "-h", "--help", "--version"):
        args = ap.parse_args(["serve"] + argv)
    else:
        args = ap.parse_args(argv)

    if args.cmd == "hub":
        repos = args.repo or registry_repos()
        if not repos:
            sys.exit("interfacile hub: no repos to serve. Pass --repo PATH "
                     "(repeatable), or register some at " + registry_path())
    else:  # serve
        repos = [os.path.abspath(args.repo or os.getcwd())]

    server.run(repos, args.port, args.host, not args.no_open)


if __name__ == "__main__":
    main()

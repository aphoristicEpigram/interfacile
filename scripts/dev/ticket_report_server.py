#!/usr/bin/env python3
"""Backwards-compatible shim.

The engine now lives in the installable ``interfacile`` package; this keeps the
old invocation working:

    python scripts/dev/ticket_report_server.py --repo /path/to/repo

Prefer the installed command once you've ``pip install``-ed the package:

    interfacile serve            # serve the current directory
    interfacile hub --repo A --repo B
"""
import os
import sys

# Make the sibling `interfacile` package importable even without installation.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from interfacile.server import main  # noqa: E402

if __name__ == "__main__":
    main()

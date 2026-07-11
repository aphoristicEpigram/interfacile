#!/usr/bin/env python3
"""Extract the ceremony commit hash from a closed ticket's retro.

Run from the project root:
    python .agents/skills/rollback/scripts/extract_hash.py EM-XXXX

Exits 0 and prints the hash on success.
Exits 1 and prints an error to stderr if the ticket is not found, not closed,
or has no commit hash in its retro.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Matches **Commit:** `<7-40 hex chars>`
_COMMIT_RE = re.compile(r"\*\*Commit:\*\*\s*`([0-9a-f]{7,40})`")


def _find_ticket(ticket_id: str) -> Path | None:
    result = subprocess.run(
        [sys.executable, "scripts/ticket_hygiene/ticket.py", "find", ticket_id],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip())


def extract_hash(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    m = _COMMIT_RE.search(text)
    return m.group(1) if m else None


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: extract_hash.py EM-XXXX", file=sys.stderr)
        return 1

    ticket_id = args[0].strip()
    path = _find_ticket(ticket_id)

    if path is None:
        print(f"Ticket not found: {ticket_id}", file=sys.stderr)
        return 1

    if "/closed/" not in str(path):
        print(
            f"Ticket {ticket_id} is not in closed/ — found at: {path}",
            file=sys.stderr,
        )
        print("Rollback only applies to closed tickets.", file=sys.stderr)
        return 1

    hash_val = extract_hash(path)
    if hash_val is None:
        print(
            f"No commit hash found in {ticket_id}'s retro.",
            file=sys.stderr,
        )
        print("Expected format: **Commit:** `<hash>`", file=sys.stderr)
        print(
            f"To find the hash manually: git log --oneline -- {path}",
            file=sys.stderr,
        )
        return 1

    print(hash_val)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Cross-ticket file impact analysis — find open tickets sharing files with a target ticket.

Run from the project root:
    python scripts/ticket_hygiene/show_impact.py EM-XXXX
"""

from __future__ import annotations

import json as _json
import re
import sys
from dataclasses import asdict, dataclass

from audit_tickets import Ticket, _collect_tickets

_FILES_TOUCHED_SECTION_RE = re.compile(
    r"^##\s*Files\s*Touched\b[^\n]*\n(.*?)(?=^##\s|\Z)",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)


def _extract_files_touched(ticket: Ticket) -> list[str]:
    """Return paths from the ticket's ## Files Touched section.

    Reads the ticket file from disk. Returns [] if the section is absent
    or contains no backtick-quoted paths.
    """
    try:
        text = ticket.path.read_text(encoding="utf-8")
    except OSError:
        return []

    m = _FILES_TOUCHED_SECTION_RE.search(text)
    if not m:
        return []

    files: list[str] = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line[0] not in "-*":
            continue
        quoted = re.search(r"`([^`]+)`", line)
        if quoted:
            candidate = quoted.group(1).strip()
            if candidate and not candidate.startswith("#"):
                files.append(candidate)
    return files


@dataclass
class ImpactResult:
    ticket_id: str
    title: str
    epic: str
    effort: str
    shared_files: list[str]


def compute_impact(target_id: str, tickets: list[Ticket]) -> list[ImpactResult]:
    """Return open tickets (excluding target) that share ≥ 1 file with target_id."""
    target = next((t for t in tickets if t.ticket_id == target_id), None)
    if target is None:
        return []

    target_files = set(_extract_files_touched(target))
    if not target_files:
        return []

    results: list[ImpactResult] = []
    for ticket in tickets:
        if ticket.ticket_id == target_id:
            continue
        if ticket.frontmatter.get("status", "").upper() != "OPEN":
            continue
        other_files = set(_extract_files_touched(ticket))
        if not other_files:
            continue
        shared = sorted(target_files & other_files)
        if not shared:
            continue
        results.append(
            ImpactResult(
                ticket_id=ticket.ticket_id,
                title=ticket.frontmatter.get("title", "(no title)"),
                epic=ticket.frontmatter.get("epic", "NO-EPIC"),
                effort=str(ticket.frontmatter.get("effort", "?h")),
                shared_files=shared,
            )
        )

    results.sort(key=lambda r: (len(r.shared_files) * -1, r.ticket_id))
    return results


def show_impact(target_id: str, tickets: list[Ticket], json_out: bool = False) -> bool:
    """Print collision table for target_id. Returns True if overlaps found, False if none.

    Prints nothing on False — callers are responsible for any "no collisions" message.
    """
    results = compute_impact(target_id, tickets)
    if not results:
        return False

    if json_out:
        print(_json.dumps({"target": target_id, "collisions": [asdict(r) for r in results]}, indent=2))
        return True

    print(f"File collisions for {target_id}:")
    for r in results:
        title = r.title[:52] + "…" if len(r.title) > 53 else r.title
        print(f"  {r.ticket_id:<16} {title}  [{r.effort}]")
        for f in r.shared_files:
            print(f"    shared: {f}")
    return True


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Cross-ticket file impact analysis")
    parser.add_argument("ticket_id", help="Target ticket ID (e.g. EM-2020)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args(argv)

    tickets = _collect_tickets()
    found = show_impact(args.ticket_id, tickets, json_out=args.json)
    if not found:
        if args.json:
            print(_json.dumps({"target": args.ticket_id, "collisions": []}))
        else:
            print(f"No file collisions found for {args.ticket_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

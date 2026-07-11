#!/usr/bin/env python3
"""Regenerate the Epic quick reference table in docs/guides/contributing/engineering-reference.md from epic files.

Usage:
    python scripts/ticket_hygiene/update_agents_epic_table.py

Reads every `tickets/EM-E*/EM-E*.md` epic file, extracts `id` and `title`
from the YAML frontmatter, and replaces the table between the markers
`<!-- BEGIN EPIC TABLE -->` and `<!-- END EPIC TABLE -->` in
docs/guides/contributing/engineering-reference.md §3.5.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EPICS_DIR = PROJECT_ROOT / "tickets"
AGENTS_PATH = PROJECT_ROOT / "docs" / "guides" / "contributing" / "engineering-reference.md"

BEGIN_MARKER = "<!-- BEGIN EPIC TABLE -->"
END_MARKER = "<!-- END EPIC TABLE -->"


_FrontmatterRe = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Return a dict of the YAML frontmatter keys (string values only)."""
    match = _FrontmatterRe.match(text)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        result[key] = value
    return result


def _collect_epics() -> list[tuple[str, str]]:
    """Return sorted (id, title) pairs for every epic file."""
    epics: list[tuple[str, str]] = []
    for epic_file in sorted(EPICS_DIR.glob("EM-E*/EM-E*.md")):
        if "-SUMMARY" in epic_file.name:
            continue
        text = epic_file.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(text)
        epic_id = frontmatter.get("id", "")
        title = frontmatter.get("title", "")
        if not epic_id or not title:
            print(f"Warning: skipping {epic_file} (missing id/title)", file=sys.stderr)
            continue
        epics.append((epic_id, title))
    return sorted(epics, key=lambda pair: pair[0])


def _render_table(epics: list[tuple[str, str]]) -> str:
    """Render the markdown table for AGENTS.md."""
    lines = ["| Epic | Scope |", "|---|---|"]
    for epic_id, title in epics:
        lines.append(f"| {epic_id} | {title} |")
    return "\n".join(lines)


def _update_agents(epics: list[tuple[str, str]]) -> None:
    """Replace the epic table in AGENTS.md between the markers."""
    if not AGENTS_PATH.exists():
        raise FileNotFoundError(f"AGENTS.md not found: {AGENTS_PATH}")

    original = AGENTS_PATH.read_text(encoding="utf-8")
    table = _render_table(epics)
    replacement = f"{BEGIN_MARKER}\n{table}\n{END_MARKER}"

    pattern = re.compile(
        f"{re.escape(BEGIN_MARKER)}.*?{re.escape(END_MARKER)}",
        re.DOTALL,
    )
    if not pattern.search(original):
        raise ValueError(
            f"Could not find {BEGIN_MARKER} ... {END_MARKER} markers in {AGENTS_PATH}"
        )

    updated = pattern.sub(replacement, original)
    AGENTS_PATH.write_text(updated, encoding="utf-8")
    print(f"Updated Epic quick reference in {AGENTS_PATH} ({len(epics)} epics).")


def main() -> int:
    epics = _collect_epics()
    if not epics:
        print("No epic files found.", file=sys.stderr)
        return 1
    _update_agents(epics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

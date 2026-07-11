#!/usr/bin/env python3
"""Generate TICKET_INDEX.md from ticket frontmatter.

Source of truth: ticket files.
This script is the build step. Run it after any ticket change:

    python scripts/ticket_hygiene/generate_index.py > tickets/TICKET_INDEX.md

Never edit TICKET_INDEX.md by hand. Edit the ticket files instead.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit_tickets import TICKETS_DIR, _parse_frontmatter

DOCS_DIR = Path("docs")

_STATUS_EMOJI = {
    "OPEN": "⬜",
    "CLOSED": "✅",
    "STANDING": "🏛️",
    "WONT_FIX": "🚫",
}

_EPIC_STATUS_ORDER = {"OPEN": 0, "STANDING": 1, "WONT_FIX": 2, "CLOSED": 3}


def _collect() -> tuple[dict[str, list[dict]], dict[str, dict]]:
    epics: dict[str, list[dict]] = defaultdict(list)
    epic_meta: dict[str, dict] = {}

    for path in TICKETS_DIR.rglob("*.md"):
        if path.name == "TICKET_INDEX.md":
            continue
        text = path.read_text(encoding="utf-8")
        fm, _err = _parse_frontmatter(text)
        if not fm:
            continue

        epic = fm.get("epic", "")
        status = fm.get("status", "")
        tid = fm.get("id", "")
        if not tid or not epic:
            continue

        # Epic files (id starts with EM-E and matches epic)
        if tid.startswith("EM-E") and tid == epic:
            epic_meta[epic] = {
                "id": tid,
                "title": fm.get("title", ""),
                "status": status,
                "path": str(path.relative_to(TICKETS_DIR)),
            }
            continue

        # Skip index-exempt companion docs
        if fm.get("index_exempt"):
            continue

        entry = {
            "id": tid,
            "title": fm.get("title", ""),
            "status": status,
            "closed": fm.get("closed", ""),
            "path": str(path.relative_to(TICKETS_DIR)),
        }
        epics[epic].append(entry)

    return dict(epics), epic_meta


def _collect_docs() -> list[dict]:
    docs: list[dict] = []
    if not DOCS_DIR.exists():
        return docs
    for path in DOCS_DIR.glob("EM-*.md"):
        text = path.read_text(encoding="utf-8")
        fm, _err = _parse_frontmatter(text)
        if not fm:
            continue
        tid = fm.get("id", "")
        if not tid:
            continue
        docs.append({
            "id": tid,
            "title": fm.get("title", ""),
            "status": fm.get("status", ""),
            "path": str(path),
        })
    return sorted(docs, key=lambda d: d["id"])


def _sort_key(entry: dict) -> tuple:
    status = entry.get("status", "").upper()
    return (_EPIC_STATUS_ORDER.get(status, 99), entry["id"])


def _fmt_row(entry: dict) -> str:
    tid = entry["id"]
    title = entry["title"]
    status = entry.get("status", "").upper()
    emoji = _STATUS_EMOJI.get(status, "⬜")
    closed = entry.get("closed", "")
    note = f"**CLOSED {closed}.**" if closed and status == "CLOSED" else ""
    return f"| {tid} | {title} | {emoji} | {note} |"


# Regex for the ID/title/status columns of existing TICKET_INDEX.md rows.
_INDEX_ROW_RE = re.compile(r"^\| (EM-[\w.-]+) \| (.+?) \| ([✅⬜❌🚫🏛️➡️⬆️🔄]+)")


def _parse_existing_index(path: Path) -> dict[str, list[tuple[str, str]]]:
    rows: dict[str, list[tuple[str, str]]] = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _INDEX_ROW_RE.match(line)
        if m:
            tid = m.group(1)
            title = m.group(2).strip()
            emoji = m.group(3).strip()
            rows.setdefault(tid, []).append((title, emoji))
    return rows


def _warn_index_drift(epics: dict[str, list[dict]], old_rows: dict[str, list[tuple[str, str]]]) -> None:
    """Print warnings to stderr when the old index drifts from generated data."""
    import sys

    generated: dict[str, list[tuple[str, str]]] = {}
    for tickets in epics.values():
        for t in tickets:
            status = t.get("status", "").upper()
            emoji = _STATUS_EMOJI.get(status, "⬜")
            generated.setdefault(t["id"], []).append((t["title"], emoji))

    for tid, old_entries in old_rows.items():
        if tid not in generated:
            print(f"WARNING: existing index references '{tid}' but no ticket file exists", file=sys.stderr)
            continue
        gen_entries = generated[tid]
        gen_titles = {title for title, _ in gen_entries}
        gen_emojis = {emoji for _, emoji in gen_entries}
        for old_title, old_emoji in old_entries:
            if old_title not in gen_titles:
                print(
                    f"WARNING: existing index title for '{tid}' ('{old_title}') does not match generated title",
                    file=sys.stderr,
                )
            if old_emoji not in gen_emojis:
                print(
                    f"WARNING: existing index status emoji for '{tid}' ('{old_emoji}') does not match generated status",
                    file=sys.stderr,
                )


def main() -> None:
    epics, epic_meta = _collect()

    # Header
    print("# TICKET_INDEX.md")
    print("")
    print("> **AUTO-GENERATED.** Do not edit by hand. Run:")
    print("> `python scripts/ticket_hygiene/generate_index.py > tickets/TICKET_INDEX.md`")
    print("")

    # Detect duplicate IDs (only report when at least one duplicate is OPEN)
    seen_ids: dict[str, list[tuple[str, str]]] = {}
    for tickets in epics.values():
        for t in tickets:
            seen_ids.setdefault(t["id"], []).append((t["path"], t["status"].upper()))
    duplicate_ids = {
        tid
        for tid, items in seen_ids.items()
        if len(items) > 1 and any(status == "OPEN" for _, status in items)
    }

    # Summary stats
    total = sum(len(v) for v in epics.values())
    open_count = sum(1 for tickets in epics.values() for t in tickets if t["status"].upper() == "OPEN")
    closed_count = sum(1 for tickets in epics.values() for t in tickets if t["status"].upper() == "CLOSED")
    standing_count = sum(1 for tickets in epics.values() for t in tickets if t["status"].upper() == "STANDING")
    wontfix_count = sum(1 for tickets in epics.values() for t in tickets if t["status"].upper() == "WONT_FIX")
    print(f"**Dashboard:** {total} tickets  |  ⬜ Open: {open_count}  |  ✅ Closed: {closed_count}  |  🏛️ Standing: {standing_count}  |  🚫 Won't Fix: {wontfix_count}")
    if duplicate_ids:
        print("")
        print("> ⚠️ **Duplicate IDs detected:** " + ", ".join(sorted(duplicate_ids)))
    print("")

    # Drift warnings against existing index (stderr, so stdout redirect stays clean)
    old_index_rows = _parse_existing_index(Path("tickets/TICKET_INDEX.md"))
    _warn_index_drift(epics, old_index_rows)

    # Reference docs in docs/ — listed as plain text to avoid scanner
    # treating them as ticket orphans.
    docs = _collect_docs()
    if docs:
        print("## Reference Docs")
        print("")
        print("Standing process documents moved from `tickets/EM-E020/` to `docs/`:")
        print("")
        for d in docs:
            print(f"- **{d['id']}** — {d['title']} (`{d['path']}`)")
        print("")

    # Rules enforced
    print("## Rules Enforced by Scanner")
    print("")
    print("| Rule | Check | Enforcement |")
    print("|---|---|---|")
    print("| **Filename format** | `EM-NNNN-*.md` or compound `EM-NNNN-X-*.md` | `_check_filename` — ERROR if filename does not start with frontmatter `id` |")
    print("| **Frontmatter validity** | Valid YAML, no duplicate keys | `_check_yaml_valid` — ERROR on invalid YAML or duplicate keys |")
    print("| **Required fields** | `id`, `title`, `epic`, `status`, `risk`, `effort`, `depends_on`, `blocks`, `created` | `_check_required_fields` — ERROR on missing fields |")
    print("| **Status values** | `OPEN`, `CLOSED`, `STANDING`, `WONT_FIX` only | `_check_allowed_values` — ERROR on invalid status |")
    print("| **Status/location** | `OPEN` in `open/`, `CLOSED` in `closed/` | `_check_status_location` — ERROR on mismatch |")
    print("| **Closed field** | `closed:` only when `status: CLOSED` | `_check_closed_field` — ERROR if `closed:` on OPEN/STANDING |")
    print("| **Epic existence** | `epic:` must match a directory in `tickets/` | `_check_epic` — ERROR if epic dir missing |")
    print("| **Retro section** | `status: CLOSED` requires `## Retro` in body | `_check_retro` — ERROR if missing |")
    print("| **Index scope** | Sub-tickets marked `index_exempt: true` skip index check | `_check_index` — WARNING if primary ticket not in index |")
    print("| **Cross-references** | `depends_on`/`blocks` must point to existing tickets or epics | `_check_cross_references` — WARNING for OPEN tickets with stale refs |")
    print("| **Commit ceremony** | Commit touching `closed/` must contain `Ceremony:` | Pre-commit hook — blocks commit if missing |")
    print("")

    # Epics — open first, then closed
    epic_order = sorted(
        epics.keys(),
        key=lambda e: (
            0 if epic_meta.get(e, {}).get("status", "").upper() == "OPEN" else 1,
            e,
        ),
    )

    for epic_id in epic_order:
        meta = epic_meta.get(epic_id, {})
        epic_title = meta.get("title", epic_id)
        epic_status = meta.get("status", "")
        epic_emoji = _STATUS_EMOJI.get(epic_status.upper(), "⬜")

        print(f"## {epic_id} — {epic_title} {epic_emoji}")
        print("")
        print("| ID | Title | Status | Notes |")
        print("|---|---|---|---|")

        for entry in sorted(epics[epic_id], key=_sort_key):
            print(_fmt_row(entry))

        print("")


if __name__ == "__main__":
    main()

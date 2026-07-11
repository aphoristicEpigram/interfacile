#!/usr/bin/env python3
"""Ticket lookup CLI — find, list, show, and trace dependencies.

Run from the project root:
    python scripts/ticket_hygiene/ticket.py find EM-1158
    python scripts/ticket_hygiene/ticket.py open
    python scripts/ticket_hygiene/ticket.py deps EM-1158
    python scripts/ticket_hygiene/ticket.py show EM-1158
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from audit_tickets import (
    _EFFORT_RE,
    _STATUS_TO_EMOJI,
    Ticket,
    _collect_epic_dirs,
    _collect_tickets,
)
from classifier import Classification, classify_tickets

_TICKET_ID_RE = re.compile(r"^EM-(?:E\d+|\d+[A-Z]*)(?:-[A-Z0-9.]+)*$")

# Matches ticket IDs embedded in prose/tables (looser — allows trailing punctuation, parens, etc.)
_TICKET_ID_IN_TEXT_RE = re.compile(r"\b(EM-(?:E\d+|\d+[A-Z]*)(?:-[A-Z0-9.]+)*)\b")

_DOC_FILES = ["README.md", "AGENTS.md"]

# IDs referenced in docs that are intentionally not ticket files (standing docs, process docs)
_KNOWN_NON_TICKETS = {"EM-1146", "EM-1147", "EM-1148", "EM-1149"}


def _regenerate_index() -> None:
    """Regenerate TICKET_INDEX.md from ticket frontmatter."""
    index_path = Path("tickets/TICKET_INDEX.md")
    result = subprocess.run(
        [sys.executable, "scripts/ticket_hygiene/generate_index.py"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"WARNING: Index regeneration failed: {result.stderr}", file=sys.stderr)
        return
    index_path.write_text(result.stdout, encoding="utf-8")
    print(f"Regenerated {index_path}")


def _next_ticket_id(tickets: list[Ticket], epic: str | None = None) -> str:
    """Find the next available numeric ticket ID.
    If epic is given, scopes to tickets in that epic."""
    candidates = tickets
    if epic:
        candidates = [t for t in tickets if t.frontmatter.get("epic") == epic]

    max_num = 0
    for t in candidates:
        tid = t.ticket_id
        m = re.match(r"EM-(\d+)", tid)
        if m:
            num = int(m.group(1))
            max_num = max(max_num, num)

    # Collision detection: if someone manually created a higher ID, skip ahead
    all_ids = {t.ticket_id for t in tickets}
    candidate = max_num + 1
    while f"EM-{candidate:04d}" in all_ids:
        candidate += 1
    return f"EM-{candidate:04d}"


def _kebab_case(text: str) -> str:
    """Convert a title to kebab-case for filenames."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = text.strip()
    return text.replace(" ", "-")


def _ticket_map(tickets: list[Ticket]) -> dict[str, Ticket]:
    return {t.ticket_id: t for t in tickets}


def _epic_name(ticket: Ticket) -> str:
    return ticket.frontmatter.get("epic", "NO-EPIC")


def _status_emoji(ticket: Ticket) -> str:
    return _STATUS_TO_EMOJI.get(ticket.frontmatter.get("status", "").upper(), "❓")


def _title(ticket: Ticket) -> str:
    return ticket.frontmatter.get("title", "(no title)")


def _priority_str(ticket: Ticket) -> str:
    p = ticket.frontmatter.get("priority")
    if p is None:
        return "P?"
    return f"P{p}"


def _effort_str(ticket: Ticket) -> str:
    e = ticket.frontmatter.get("effort")
    if not e or str(e).upper() == "N/A":
        return "?h"
    return str(e)


def _ticket_summary(ticket: Ticket) -> str:
    return f"{_status_emoji(ticket)} {_priority_str(ticket)} {_effort_str(ticket):>4}  {ticket.ticket_id} — {_title(ticket)}"


def _priority_sort_key(ticket_id: str, ticket_map: dict[str, Ticket]) -> tuple:
    t = ticket_map.get(ticket_id)
    if t is None:
        return (99, ticket_id)
    p = t.frontmatter.get("priority")
    if isinstance(p, int) and 1 <= p <= 5:
        return (p, ticket_id)
    return (99, ticket_id)


def _find(tickets: list[Ticket], ticket_id: str) -> Ticket | None:
    for t in tickets:
        if t.ticket_id == ticket_id:
            return t
    return None


def cmd_find(tickets: list[Ticket], ticket_id: str) -> int:
    t = _find(tickets, ticket_id)
    if t is None:
        print(f"Ticket not found: {ticket_id}", file=sys.stderr)
        return 1
    print(t.path)
    return 0


def cmd_open(tickets: list[Ticket]) -> int:
    open_tickets = [t for t in tickets if t.frontmatter.get("status", "").upper() == "OPEN"]
    if not open_tickets:
        print("No open tickets.")
        return 0

    by_epic: dict[str, list[Ticket]] = {}
    for t in open_tickets:
        by_epic.setdefault(_epic_name(t), []).append(t)

    for epic in sorted(by_epic.keys()):
        print(f"\n{epic}")
        # Sort by priority ascending, then by ticket ID
        for t in sorted(by_epic[epic], key=lambda x: (x.frontmatter.get("priority") or 99, x.ticket_id)):
            print(f"  {_ticket_summary(t)}")
    return 0


def _trace_deps(
    ticket_map: dict[str, Ticket],
    ticket_id: str,
    direction: str,
    depth: int = 0,
    max_depth: int = 3,
    visited: set[str] | None = None,
) -> list[str]:
    """Return indented lines tracing depends_on or blocks recursively.
    Siblings are sorted by priority (P1 first)."""
    if visited is None:
        visited = set()
    if depth > max_depth or ticket_id in visited:
        return []
    visited.add(ticket_id)

    t = ticket_map.get(ticket_id)
    if t is None:
        return []

    refs: list[str] = []
    if direction == "depends_on":
        raw = t.frontmatter.get("depends_on", [])
    else:
        raw = t.frontmatter.get("blocks", [])

    if not isinstance(raw, list):
        return []

    for ref in sorted(raw, key=lambda rid: _priority_sort_key(rid, ticket_map)):
        if not ref:
            continue
        indent = "    " * (depth + 1)
        ref_ticket = ticket_map.get(ref)
        if ref_ticket:
            refs.append(f"{indent}{_ticket_summary(ref_ticket)}")
        else:
            refs.append(f"{indent}❓     {ref} — (unknown)")
        refs.extend(_trace_deps(ticket_map, ref, direction, depth + 1, max_depth, visited))
    return refs


def cmd_deps(tickets: list[Ticket], ticket_id: str) -> int:
    t = _find(tickets, ticket_id)
    if t is None:
        print(f"Ticket not found: {ticket_id}", file=sys.stderr)
        return 1

    ticket_map = _ticket_map(tickets)
    print(_ticket_summary(t))

    depends = t.frontmatter.get("depends_on", [])
    blocks = t.frontmatter.get("blocks", [])

    if not depends and not blocks:
        print("\n  No dependencies or blockers.")
        return 0

    if depends:
        print("\n  depends_on:")
        for ref in sorted(depends, key=lambda rid: _priority_sort_key(rid, ticket_map)):
            ref_ticket = ticket_map.get(ref)
            if ref_ticket:
                print(f"      {_ticket_summary(ref_ticket)}")
            else:
                print(f"      ❓     {ref} — (unknown)")
            sub = _trace_deps(ticket_map, ref, "depends_on", depth=1, max_depth=3)
            for line in sub:
                print(line)

    if blocks:
        print("\n  blocks:")
        for ref in sorted(blocks, key=lambda rid: _priority_sort_key(rid, ticket_map)):
            ref_ticket = ticket_map.get(ref)
            if ref_ticket:
                print(f"      {_ticket_summary(ref_ticket)}")
            else:
                print(f"      ❓     {ref} — (unknown)")
            sub = _trace_deps(ticket_map, ref, "blocks", depth=1, max_depth=3)
            for line in sub:
                print(line)

    return 0


def cmd_show(tickets: list[Ticket], ticket_id: str) -> int:
    t = _find(tickets, ticket_id)
    if t is None:
        print(f"Ticket not found: {ticket_id}", file=sys.stderr)
        return 1
    print(t.path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_create(
    tickets: list[Ticket],
    epic_dirs: dict[str, Path],
    ticket_id: str | None,
    title: str,
    epic: str,
    effort: str,
    risk: str,
    priority: int,
    today: str | None = None,
    auto_id: bool = False,
    epic_sequence: bool = False,
    dry_run: bool = False,
) -> int:
    if auto_id:
        scope_epic = epic if epic_sequence else None
        ticket_id = _next_ticket_id(tickets, epic=scope_epic)
        print(f"Auto-assigned ID: {ticket_id}")

    if not ticket_id:
        print("ERROR: ticket_id required unless --auto-id is used", file=sys.stderr)
        return 1

    if not _TICKET_ID_RE.match(ticket_id):
        print(f"ERROR: Invalid ticket ID format: {ticket_id}", file=sys.stderr)
        print("Expected: EM-NNNN, EM-NNNN-A, EM-E019, EM-E019-SUMMARY, etc.", file=sys.stderr)
        return 1

    existing = {t.ticket_id for t in tickets}
    if ticket_id in existing:
        print(f"ERROR: Ticket ID already exists: {ticket_id}", file=sys.stderr)
        return 1

    if epic not in epic_dirs:
        print(f"ERROR: Epic '{epic}' does not match any directory in tickets/", file=sys.stderr)
        print(f"Known epics: {', '.join(sorted(epic_dirs))}", file=sys.stderr)
        return 1

    epic_dir = epic_dirs[epic]
    open_dir = epic_dir / "open"
    if not open_dir.exists():
        print(f"ERROR: No open/ directory in {epic_dir}", file=sys.stderr)
        return 1

    if today is None:
        today = datetime.date.today().isoformat()

    kebab = _kebab_case(title)
    filename = f"{ticket_id}-{kebab}.md"
    filepath = open_dir / filename

    if filepath.exists():
        print(f"ERROR: File already exists: {filepath}", file=sys.stderr)
        return 1

    if dry_run:
        print(f"[DRY RUN] Would create: {filepath}")
        return 0

    frontmatter = f"""---
id: {ticket_id}
title: "{title}"
epic: {epic}
status: OPEN
risk: {risk}
effort: {effort}
priority: {priority}
depends_on: []
blocks: []
created: {today}
---

# {ticket_id} — {title}

**Epic:** {epic}
**Status:** OPEN
**Risk:** {risk}

## Context

## Acceptance Criteria

- [ ] Criterion 1

## Effort

~{effort}
"""

    filepath.write_text(frontmatter, encoding="utf-8")
    print(f"Created {filepath}")
    _regenerate_index()
    return 0


def _format_frontmatter_value(key: str, value: Any) -> str:
    """Serialize a frontmatter value in our compact format."""
    if isinstance(value, list):
        inner = ", ".join(str(v) for v in value)
        return f"[{inner}]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and (
        ":" in value or '"' in value or "#" in value or value.strip() == ""
    ):
        # Defensive quoting
        if '"' in value:
            return f"'{value}'"
        return f'"{value}"'
    return str(value)


def _load_frontmatter(text: str) -> tuple[dict[str, Any], str, str] | None:
    """Parse ticket frontmatter.

    Returns (data, raw_frontmatter, body) or None if the file has no valid
    frontmatter.  `data` is the YAML mapping, `raw_frontmatter` is the literal
    text between the --- markers (used to preserve key order on dump), and
    `body` is everything after the closing ---.
    """
    if not text.startswith("---"):
        return None
    fm_end = text.find("\n---", 3)
    if fm_end == -1:
        return None
    after = fm_end + 4
    if after < len(text) and text[after] not in "\n\r":
        return None
    raw_fm = text[3:fm_end].strip()
    body = text[after:]
    if body.startswith("\n"):
        body = body[1:]
    elif body.startswith("\r\n"):
        body = body[2:]
    try:
        data = yaml.safe_load(raw_fm)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    return data, raw_fm, body


def _dump_frontmatter(data: dict[str, Any], raw_fm: str, body: str) -> str:
    """Re-serialize frontmatter while preserving the original key order."""
    lines = ["---"]
    remaining = dict(data)
    for key_line in raw_fm.splitlines():
        key_name = key_line.split(":", 1)[0].strip()
        if key_name in remaining:
            lines.append(f"{key_name}: {_format_frontmatter_value(key_name, remaining[key_name])}")
            del remaining[key_name]
    for key, value in remaining.items():
        lines.append(f"{key}: {_format_frontmatter_value(key, value)}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def _update_frontmatter(text: str, updates: dict[str, Any]) -> str | None:
    """Parse frontmatter, apply key updates, and re-serialize preserving key order."""
    parsed = _load_frontmatter(text)
    if parsed is None:
        return None
    data, raw_fm, body = parsed
    for key, value in updates.items():
        data[key] = value
    return _dump_frontmatter(data, raw_fm, body)


def _rewrite_frontmatter(text: str, closed_id: str, closed_date: str) -> str | None:
    """Remove closed_id from depends_on/blocks and append a closure note.
    Returns the rewritten text, or None if no changes needed.
    """
    parsed = _load_frontmatter(text)
    if parsed is None:
        return None
    fm, raw_fm, body = parsed

    changed = False
    for key in ("depends_on", "blocks"):
        val = fm.get(key, [])
        if isinstance(val, list) and closed_id in val:
            fm[key] = [v for v in val if v != closed_id]
            changed = True

    if not changed:
        return None

    # Append closure note before ## Retro or at end
    note = f"> **Note:** {closed_id} closed {closed_date}. Unblocked.\n"
    if "## Retro" in body:
        body = body.replace("## Retro", note + "\n## Retro", 1)
    else:
        body = body.rstrip("\n") + "\n\n" + note + "\n"

    return _dump_frontmatter(fm, raw_fm, body)


def cmd_clean_deps(tickets: list[Ticket], write: bool = False) -> int:
    """Bulk-remove depends_on/blocks entries that point to CLOSED or WONT_FIX tickets."""
    ticket_map = _ticket_map(tickets)
    closed_statuses = {"CLOSED", "WONT_FIX"}
    changed_count = 0
    affected_tickets: list[tuple[str, list[str], list[str]]] = []

    for ticket in tickets:
        if ticket.frontmatter.get("status", "").upper() not in ("OPEN", "STANDING"):
            continue

        parsed = _load_frontmatter(ticket.path.read_text(encoding="utf-8"))
        if parsed is None:
            continue
        fm, raw_fm, body = parsed

        removed_deps: list[str] = []
        removed_blocks: list[str] = []
        changed = False

        for key, removed_list in (("depends_on", removed_deps), ("blocks", removed_blocks)):
            val = fm.get(key, [])
            if not isinstance(val, list):
                continue
            new_val = []
            for ref in val:
                ref_ticket = ticket_map.get(ref)
                if ref_ticket and ref_ticket.frontmatter.get("status", "").upper() in closed_statuses:
                    removed_list.append(ref)
                    changed = True
                else:
                    new_val.append(ref)
            if changed:
                fm[key] = new_val

        if not changed:
            continue

        affected_tickets.append((ticket.ticket_id, removed_deps, removed_blocks))

        if not write:
            continue

        note_lines: list[str] = []
        for ref in removed_deps:
            ref_ticket = ticket_map.get(ref)
            status = ref_ticket.frontmatter.get("status", "") if ref_ticket else ""
            note_lines.append(f"> **Note:** {ref} is {status}. Removed from depends_on.\n")
        for ref in removed_blocks:
            ref_ticket = ticket_map.get(ref)
            status = ref_ticket.frontmatter.get("status", "") if ref_ticket else ""
            note_lines.append(f"> **Note:** {ref} is {status}. Removed from blocks.\n")

        if note_lines:
            if "## Retro" in body:
                body = body.replace("## Retro", "".join(note_lines) + "\n## Retro", 1)
            else:
                body = body.rstrip("\n") + "\n\n" + "".join(note_lines) + "\n"

        ticket.path.write_text(_dump_frontmatter(fm, raw_fm, body), encoding="utf-8")
        changed_count += 1

    if not affected_tickets:
        print("No stale dependencies found.")
        return 0

    def _ref_status(ref_id: str) -> str:
        ref = ticket_map.get(ref_id)
        return ref.frontmatter.get("status", "").upper() if ref else ""

    # Separate WONT_FIX from CLOSED for review
    wontfix_affected: list[tuple[str, list[str], list[str]]] = []
    closed_affected: list[tuple[str, list[str], list[str]]] = []
    for tid, removed_deps, removed_blocks in affected_tickets:
        is_wontfix = any(_ref_status(ref) == "WONT_FIX" for ref in removed_deps + removed_blocks)
        if is_wontfix:
            wontfix_affected.append((tid, removed_deps, removed_blocks))
        else:
            closed_affected.append((tid, removed_deps, removed_blocks))

    print(f"Stale dependencies found in {len(affected_tickets)} ticket(s):")
    for tid, removed_deps, removed_blocks in affected_tickets:
        print(f"  {tid}")
        for ref in removed_deps:
            marker = " 🚫" if _ref_status(ref) == "WONT_FIX" else ""
            print(f"    - depends_on: {ref}{marker}")
        for ref in removed_blocks:
            marker = " 🚫" if _ref_status(ref) == "WONT_FIX" else ""
            print(f"    - blocks: {ref}{marker}")

    if wontfix_affected:
        print()
        print("⚠️  Tickets with WONT_FIX dependencies — review whether these should also be WONT_FIX:")
        for tid, removed_deps, removed_blocks in wontfix_affected:
            print(f"  {tid}")
            for ref in removed_deps:
                if _ref_status(ref) == "WONT_FIX":
                    print(f"    - depended on WONT_FIX: {ref}")
            for ref in removed_blocks:
                if _ref_status(ref) == "WONT_FIX":
                    print(f"    - blocked by WONT_FIX: {ref}")

    if not write:
        print(f"\nDry run. Use --write to apply changes to {changed_count} ticket(s).")
        return 0

    print(f"\nCleaned {changed_count} ticket(s).")
    _regenerate_index()
    return 0


def cmd_edit(tickets: list[Ticket], ticket_id: str, **fields: Any) -> int:
    """Edit frontmatter fields of a ticket."""
    t = _find(tickets, ticket_id)
    if t is None:
        print(f"Ticket not found: {ticket_id}", file=sys.stderr)
        return 1

    parsed = _load_frontmatter(t.path.read_text(encoding="utf-8"))
    if parsed is None:
        print("ERROR: Invalid frontmatter", file=sys.stderr)
        return 1
    fm, raw_fm, body = parsed

    changed = False
    for key, value in fields.items():
        if value is None:
            continue
        if key == "depends_on" or key == "blocks":
            if isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
        if key == "priority":
            try:
                value = int(value)
            except (ValueError, TypeError):
                print(f"ERROR: Invalid priority: {value!r}", file=sys.stderr)
                return 1
        fm[key] = value
        changed = True
        print(f"  {key}: {_format_frontmatter_value(key, value)}")

    if not changed:
        print("No fields to update.")
        return 0

    t.path.write_text(_dump_frontmatter(fm, raw_fm, body), encoding="utf-8")
    print(f"Updated {t.path}")
    _regenerate_index()
    return 0


def _is_epic_root(ticket_id: str) -> bool:
    return bool(re.match(r"^EM-E\d+$", ticket_id))


def _valid_effort(value: str) -> bool:
    if not value or value.upper() == "N/A":
        return False
    return bool(_EFFORT_RE.match(str(value).strip().lower()))


def cmd_estimate(tickets: list[Ticket], dry_run: bool = False) -> int:
    """Interactive TUI for bulk-adding effort estimates to unestimated open tickets."""
    candidates = []
    for t in tickets:
        if t.frontmatter.get("status", "").upper() != "OPEN":
            continue
        if _is_epic_root(t.ticket_id):
            continue
        effort = t.frontmatter.get("effort", "")
        if _valid_effort(str(effort)):
            continue
        candidates.append(t)

    if not candidates:
        print("✅ No unestimated open tickets found.")
        return 0

    print(f"Found {len(candidates)} open ticket(s) without valid effort estimates.")
    print("Commands: <effort> = set estimate, s = skip, p = set priority, q = quit")
    print("Effort format: 2h, 1d, 30m, 1-2h")
    print()

    changed_count = 0
    for t in candidates:
        tid = t.ticket_id
        title = t.frontmatter.get("title", "(no title)")
        current_effort = t.frontmatter.get("effort", "")
        current_priority = t.frontmatter.get("priority", "")
        epic = t.frontmatter.get("epic", "")

        print(f"⬜ {tid} | P{current_priority} | epic:{epic}")
        print(f"   {title}")
        if current_effort:
            print(f"   Current effort: {current_effort!r} (invalid)")

        while True:
            try:
                user_input = input("   effort> ").strip()
            except EOFError:
                print()
                break

            if user_input.lower() == "q":
                print("Quitting.")
                break
            if user_input.lower() == "s":
                print("   Skipped.")
                break
            if user_input.lower().startswith("p "):
                try:
                    new_p = int(user_input[2:].strip())
                    if new_p not in {1, 2, 3, 4, 5}:
                        print("   Invalid priority. Use 1-5.")
                        continue
                    t.frontmatter["priority"] = new_p
                    print(f"   Priority set to P{new_p}.")
                except ValueError:
                    print("   Invalid input. Use 'p <number>'.")
                continue
            if _valid_effort(user_input):
                t.frontmatter["effort"] = user_input
                print(f"   Effort set to {user_input}.")
                break
            print("   Invalid effort. Format: 2h, 1d, 30m, 1-2h. Try again.")

        if user_input.lower() == "q":
            break

        if t.frontmatter.get("effort") != current_effort or t.frontmatter.get("priority") != current_priority:
            if dry_run:
                print(f"   [DRY RUN] Would update {tid}")
                changed_count += 1
                continue

            parsed = _load_frontmatter(t.path.read_text(encoding="utf-8"))
            if parsed is None:
                print(f"   ERROR: Invalid frontmatter in {tid}", file=sys.stderr)
                continue
            fm, raw_fm, body = parsed

            fm["effort"] = t.frontmatter["effort"]
            if "priority" in t.frontmatter:
                fm["priority"] = t.frontmatter["priority"]

            t.path.write_text(_dump_frontmatter(fm, raw_fm, body), encoding="utf-8")
            print(f"   Updated {t.path}")
            changed_count += 1

    print()
    if dry_run:
        print(f"Dry run. Would update {changed_count} ticket(s).")
    else:
        print(f"Updated {changed_count} ticket(s).")
    if changed_count > 0:
        _regenerate_index()
    return 0


def cmd_close_cleanup(tickets: list[Ticket], ticket_id: str, write: bool = False) -> int:
    t = _find(tickets, ticket_id)
    if t is None:
        print(f"Ticket not found: {ticket_id}", file=sys.stderr)
        return 1

    closed_date = t.frontmatter.get("closed", "")
    if not closed_date:
        closed_date = "(unknown date)"

    affected: list[tuple[Ticket, str]] = []
    for ticket in tickets:
        if ticket.ticket_id == ticket_id:
            continue
        for key in ("depends_on", "blocks"):
            val = ticket.frontmatter.get(key, [])
            if isinstance(val, list) and ticket_id in val:
                affected.append((ticket, key))
                break

    if not affected:
        print(f"No tickets reference {ticket_id} in depends_on or blocks.")
        return 0

    print(f"Tickets referencing {ticket_id}:")
    for ticket, key in affected:
        print(f"  {ticket.ticket_id} ({key})")
    print()

    if not write:
        print("Dry run. Use --write to apply changes.")
        return 0

    changed_count = 0
    for ticket, _key in affected:
        text = ticket.path.read_text(encoding="utf-8")
        new_text = _rewrite_frontmatter(text, ticket_id, closed_date)
        if new_text is None:
            continue
        ticket.path.write_text(new_text, encoding="utf-8")
        changed_count += 1
        print(f"Updated {ticket.path}")

    print(f"\nUpdated {changed_count} ticket(s).")
    _regenerate_index()
    return 0


def cmd_close(
    tickets: list[Ticket],
    epic_dirs: dict[str, Path],
    ticket_id: str,
    date: str | None = None,
    dry_run: bool = False,
    skip_cross_refs: bool = False,
    force: bool = False,
) -> int:
    """Atomically close a ticket: update frontmatter, move open/ -> closed/, update cross-refs, regenerate index."""
    t = _find(tickets, ticket_id)
    if t is None:
        print(f"ERROR: Ticket not found: {ticket_id}", file=sys.stderr)
        return 1

    if _is_epic_root(ticket_id):
        print(f"ERROR: {ticket_id} is an epic root and cannot be closed.", file=sys.stderr)
        return 1

    path_str = str(t.path).replace("\\", "/")
    in_open = "/open/" in path_str
    in_closed = "/closed/" in path_str
    status = t.frontmatter.get("status", "").upper()

    if status == "CLOSED" and in_closed:
        print(f"{ticket_id} is already CLOSED.")
        return 0
    if status == "CLOSED" and in_open:
        print(f"ERROR: {ticket_id} has status CLOSED but is still in open/. Run close-cleanup or move manually.", file=sys.stderr)
        return 1
    if status not in ("OPEN", "STANDING"):
        print(f"ERROR: {ticket_id} has status {status}; only OPEN or STANDING tickets can be closed.", file=sys.stderr)
        return 1
    if not in_open:
        print(f"ERROR: {ticket_id} is not in an open/ directory ({t.path}).", file=sys.stderr)
        return 1

    text = t.path.read_text(encoding="utf-8")
    if not force and not t.frontmatter.get("retro_exempt"):
        if "## Retro" not in text:
            print(f"ERROR: {ticket_id} is missing a '## Retro' section. Close with --force to override.", file=sys.stderr)
            return 1
        if "If I Had 3 More Hours" not in text:
            print(f"ERROR: {ticket_id} retro is missing 'If I Had 3 More Hours'. Close with --force to override.", file=sys.stderr)
            return 1

    ticket_map = _ticket_map(tickets)
    depends_on = t.frontmatter.get("depends_on", [])
    if isinstance(depends_on, list) and depends_on:
        open_deps = [
            ref
            for ref in depends_on
            if ref in ticket_map and ticket_map[ref].frontmatter.get("status", "").upper() == "OPEN"
        ]
        if open_deps:
            print(f"WARNING: {ticket_id} depends on OPEN tickets: {', '.join(open_deps)}", file=sys.stderr)
            if not force:
                print("ERROR: Use --force to close anyway.", file=sys.stderr)
                return 1

    if date is None:
        date = datetime.date.today().isoformat()

    epic = t.frontmatter.get("epic", "")
    epic_dir = epic_dirs.get(epic)
    if epic_dir is None:
        print(f"ERROR: Epic '{epic}' does not match any directory in tickets/.", file=sys.stderr)
        return 1
    closed_dir = epic_dir / "closed"
    if not closed_dir.exists():
        closed_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created {closed_dir}")
    dest_path = closed_dir / t.path.name
    if dest_path.exists():
        print(f"ERROR: Destination file already exists: {dest_path}", file=sys.stderr)
        return 1

    affected: list[tuple[Ticket, str]] = []
    for ticket in tickets:
        if ticket.ticket_id == ticket_id:
            continue
        for key in ("depends_on", "blocks"):
            val = ticket.frontmatter.get(key, [])
            if isinstance(val, list) and ticket_id in val:
                affected.append((ticket, key))
                break

    if dry_run:
        print(f"[DRY RUN] Would close {ticket_id}:")
        print(f"  update frontmatter: status: CLOSED, closed: {date}")
        if "**Status:**" in text:
            print("  update body status line: OPEN -> CLOSED")
        print(f"  move {t.path} -> {dest_path}")
        for ticket, key in affected:
            print(f"  remove {ticket_id} from {ticket.ticket_id} {key} and append closure note")
        if not affected:
            print("  no cross-references to update")
        print("  regenerate TICKET_INDEX.md")
        return 0

    new_text = _update_frontmatter(text, {"status": "CLOSED", "closed": date})
    if new_text is None:
        print(f"ERROR: Could not parse frontmatter in {t.path}", file=sys.stderr)
        return 1

    # Update the human-readable status line in the body if present.
    new_text = re.sub(r"(\*\*Status:\*\*)\s*OPEN", r"\1 CLOSED", new_text, count=1, flags=re.IGNORECASE)

    t.path.write_text(new_text, encoding="utf-8")
    t.path.rename(dest_path)

    if not skip_cross_refs:
        for ticket, _key in affected:
            aff_text = ticket.path.read_text(encoding="utf-8")
            rewritten = _rewrite_frontmatter(aff_text, ticket_id, date)
            if rewritten is not None:
                ticket.path.write_text(rewritten, encoding="utf-8")
                print(f"Updated {ticket.path}")

    _regenerate_index()

    audit_result = subprocess.run(
        [sys.executable, "scripts/ticket_hygiene/audit_tickets.py"],
        capture_output=True,
        text=True,
    )
    if audit_result.returncode != 0:
        print(f"WARNING: Ticket hygiene audit reported errors after close:\n{audit_result.stdout}", file=sys.stderr)
    else:
        print("Ticket hygiene audit passed.")

    print(f"\nClosed {ticket_id} and moved to {dest_path}")
    print("\nSuggested commit message:\n")
    print(f'git commit -m "Close {ticket_id} — <title>\n\nCeremony: <retro summary>\n\nApproved: <who approved>"')
    return 0


def cmd_rename(
    tickets: list[Ticket],
    epic_dirs: dict[str, Path],
    old_id: str,
    new_id: str,
    write: bool = False,
) -> int:
    """Rename a ticket file, update its frontmatter id, and patch references."""
    ticket_map = _ticket_map(tickets)

    old_ticket = ticket_map.get(old_id)
    if old_ticket is None:
        print(f"ERROR: Ticket not found: {old_id}", file=sys.stderr)
        return 1

    if not _TICKET_ID_RE.match(new_id):
        print(f"ERROR: Invalid new ticket ID format: {new_id}", file=sys.stderr)
        return 1

    existing_ids = set(ticket_map.keys()) | set(epic_dirs.keys())
    if new_id in existing_ids:
        print(f"ERROR: Ticket ID already exists: {new_id}", file=sys.stderr)
        return 1

    old_path = old_ticket.path
    old_name = old_path.name
    if not old_name.startswith(old_id):
        print(f"ERROR: Filename '{old_name}' does not start with ticket id '{old_id}'", file=sys.stderr)
        return 1

    suffix = old_name[len(old_id):]
    new_name = f"{new_id}{suffix}"
    new_path = old_path.parent / new_name
    if new_path.exists():
        print(f"ERROR: Destination file already exists: {new_path}", file=sys.stderr)
        return 1

    text = old_path.read_text(encoding="utf-8")
    new_text = _update_frontmatter(text, {"id": new_id})
    if new_text is None:
        print(f"ERROR: Could not parse frontmatter in {old_path}", file=sys.stderr)
        return 1

    # Replace references to the old ID in the body (word-boundary safe).
    new_text = re.sub(rf"\b{re.escape(old_id)}\b", new_id, new_text)

    # Collect reference updates in other tickets.
    reference_updates: list[tuple[Path, str]] = []
    for t in tickets:
        if t.ticket_id == old_id:
            continue
        updates: dict[str, Any] = {}
        for key in ("depends_on", "blocks"):
            val = t.frontmatter.get(key, [])
            if isinstance(val, list) and old_id in val:
                updates[key] = [new_id if v == old_id else v for v in val]
        if not updates:
            continue
        updated_text = _update_frontmatter(t.path.read_text(encoding="utf-8"), updates)
        if updated_text is None:
            print(f"WARNING: Could not parse frontmatter in {t.path}; skipping reference update", file=sys.stderr)
            continue
        reference_updates.append((t.path, updated_text))

    if not write:
        print(f"[DRY RUN] Would rename {old_path} -> {new_path}")
        for path, _ in reference_updates:
            print(f"  update references in {path}")
        return 0

    new_path.write_text(new_text, encoding="utf-8")
    old_path.unlink()
    for path, updated_text in reference_updates:
        path.write_text(updated_text, encoding="utf-8")

    _regenerate_index()
    print(f"Renamed {old_path} -> {new_path}")
    print(f"Updated references in {len(reference_updates)} other ticket(s)")
    return 0


def cmd_history(tickets: list[Ticket], ticket_id: str, oneline: bool = False) -> int:
    t = _find(tickets, ticket_id)
    if t is None:
        print(f"Ticket not found: {ticket_id}", file=sys.stderr)
        return 1

    path = str(t.path)
    # Check if file is tracked by git
    check = subprocess.run(
        ["git", "ls-files", "--error-unmatch", path],
        capture_output=True,
    )
    if check.returncode != 0:
        print(f"ERROR: {path} is not tracked by git", file=sys.stderr)
        return 1

    fmt = "%h %s" if oneline else "%ad  %h  %s"
    result = subprocess.run(
        ["git", "log", "--follow", f"--format={fmt}", "--date=short", "--", path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: git log failed: {result.stderr}", file=sys.stderr)
        return 1

    lines = result.stdout.strip().splitlines()
    if not lines:
        print("No commits found.")
        return 0

    for line in lines:
        print(line)
    return 0


def cmd_impact(tickets: list[Ticket], target_id: str, json_out: bool = False) -> int:
    from show_impact import show_impact

    found = show_impact(target_id, tickets, json_out=json_out)
    if not found:
        if json_out:
            import json as _json_mod
            print(_json_mod.dumps({"target": target_id, "collisions": []}))
        else:
            print(f"No file collisions found for {target_id}.")
    return 0


def cmd_health(
    tickets: list[Ticket],
    epic: str | None = None,
    today: str | None = None,
    threshold_yellow: int | None = None,
    threshold_red: int | None = None,
    json_out: bool = False,
) -> int:
    from show_health import show_health_table

    today_date = datetime.date.today()
    if today:
        try:
            today_date = datetime.date.fromisoformat(today)
        except ValueError:
            print(f"ERROR: Invalid date: {today!r}", file=sys.stderr)
            return 1

    kwargs: dict[str, Any] = {}
    if threshold_yellow is not None:
        kwargs["threshold_yellow"] = threshold_yellow
    if threshold_red is not None:
        kwargs["threshold_red"] = threshold_red

    show_health_table(tickets, today_date, epic=epic, json_out=json_out, **kwargs)
    return 0


_LAST_RUN_FILE = Path(".ticket_hygiene_last_run")


def _parse_effort(value: str) -> float | None:
    """Parse effort string like '2h', '30m', '1-2h', '1d' into hours."""
    if not value or value.upper() == "N/A":
        return None
    v = value.strip().lower()
    # Single value: 2h, 1.5h, 2d
    m = re.match(r"^([\d.]+)\s*([hdm])$", v)
    if m:
        num, unit = float(m.group(1)), m.group(2)
        if unit == "d":
            return num * 8
        if unit == "m":
            return num / 60
        return num
    # Range: 1-2h, 1–2h, 2-4d (return midpoint)
    m = re.match(r"^([\d.]+)\s*[-–]\s*([\d.]+)\s*([hd])$", v)
    if m:
        low, high, unit = float(m.group(1)), float(m.group(2)), m.group(3)
        num = (low + high) / 2
        if unit == "d":
            return num * 8
        return num
    return None


def cmd_stats(tickets: list[Ticket], epic: str | None = None, week: bool = False, json_out: bool = False) -> int:
    if epic:
        tickets = [t for t in tickets if t.frontmatter.get("epic") == epic]

    total = len(tickets)
    by_status: dict[str, int] = {}
    for t in tickets:
        status = t.frontmatter.get("status", "UNKNOWN")
        by_status[status] = by_status.get(status, 0) + 1

    # Effort averages
    open_efforts: list[float] = []
    closed_efforts: list[float] = []
    for t in tickets:
        effort = _parse_effort(t.frontmatter.get("effort", ""))
        if effort is None:
            continue
        status = t.frontmatter.get("status", "").upper()
        if status == "OPEN":
            open_efforts.append(effort)
        elif status == "CLOSED":
            closed_efforts.append(effort)

    # Velocity
    today = datetime.date.today()
    velocity: int | str = 0
    closed_dates: list[datetime.date] = []
    for t in tickets:
        if t.frontmatter.get("status", "").upper() == "CLOSED":
            date_val = t.frontmatter.get("closed", "")
            if isinstance(date_val, datetime.date):
                closed_dates.append(date_val)
                continue
            if not isinstance(date_val, str) or not date_val:
                continue
            try:
                closed_dates.append(datetime.date.fromisoformat(date_val))
            except ValueError:
                pass

    if week:
        recent = [d for d in closed_dates if (today - d).days <= 7]
        velocity = len(recent)
    else:
        # All-time velocity: total closed / days since first closure
        if closed_dates:
            first = min(closed_dates)
            days = max(1, (today - first).days)
            velocity = f"{len(closed_dates)} closed over {days} days (~{len(closed_dates) / days:.1f}/day)"
        else:
            velocity = "0"

    # By epic (open count)
    epic_open: dict[str, int] = {}
    for t in tickets:
        if t.frontmatter.get("status", "").upper() == "OPEN":
            e = t.frontmatter.get("epic", "NO-EPIC")
            epic_open[e] = epic_open.get(e, 0) + 1

    # Priority / Risk breakdown (open tickets only)
    open_by_priority: dict[str, int] = {}
    open_by_risk: dict[str, int] = {}
    for t in tickets:
        if t.frontmatter.get("status", "").upper() == "OPEN":
            p = str(t.frontmatter.get("priority", "N/A")).upper()
            open_by_priority[p] = open_by_priority.get(p, 0) + 1
            r = str(t.frontmatter.get("risk", "N/A")).upper()
            open_by_risk[r] = open_by_risk.get(r, 0) + 1

    # Burndown: tickets closed per day
    burndown: dict[str, int] = {}
    for d in closed_dates:
        key = d.isoformat()
        burndown[key] = burndown.get(key, 0) + 1

    if json_out:
        import json
        data = {
            "total": total,
            "by_status": by_status,
            "velocity": velocity if isinstance(velocity, str) else f"{velocity} tickets in last 7 days",
            "average_effort_open": round(sum(open_efforts) / len(open_efforts), 1) if open_efforts else None,
            "average_effort_closed": round(sum(closed_efforts) / len(closed_efforts), 1) if closed_efforts else None,
            "total_remaining_effort": round(sum(open_efforts), 1) if open_efforts else None,
            "epic_open": epic_open,
            "open_by_priority": open_by_priority,
            "open_by_risk": open_by_risk,
            "burndown": burndown,
        }
        print(json.dumps(data, indent=2))
        return 0

    # Print
    print(f"Total: {total} tickets")
    status_parts = []
    for s in ["OPEN", "CLOSED", "STANDING", "WONT_FIX"]:
        if s in by_status:
            status_parts.append(f"{s}: {by_status[s]}")
    if status_parts:
        print("  " + "  |  ".join(status_parts))
    print()

    if week:
        print(f"Velocity (last 7 days): {velocity} tickets closed")
    else:
        print(f"Velocity: {velocity}")

    if open_efforts:
        print(f"Average effort (open): {sum(open_efforts) / len(open_efforts):.1f}h")
        print(f"Total remaining effort: {sum(open_efforts):.1f}h")
    if closed_efforts:
        print(f"Average effort (closed): {sum(closed_efforts) / len(closed_efforts):.1f}h")
    print()

    if open_by_priority:
        print("By priority (open tickets):")
        for p, count in sorted(open_by_priority.items()):
            print(f"  {p}: {count}")
        print()

    if open_by_risk:
        print("By risk (open tickets):")
        for r, count in sorted(open_by_risk.items(), key=lambda x: {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "N/A": 3}.get(x[0], 4)):
            print(f"  {r}: {count}")
        print()

    if epic_open and not epic:
        print("By epic (open count):")
        for e, count in sorted(epic_open.items(), key=lambda x: -x[1]):
            print(f"  {e}: {count}")
        print()

    if burndown:
        print("Burndown (tickets closed per day):")
        max_count = max(burndown.values())
        scale = max(1, max_count)
        for day in sorted(burndown.keys())[-14:]:  # last 14 days
            count = burndown[day]
            bar = "█" * int(30 * count / scale)
            print(f"  {day}  {bar}  {count}")

    return 0


def _classify_sort_key(c: Classification, ticket_map: dict[str, Ticket]) -> tuple:
    t = ticket_map.get(c.ticket_id)
    priority = t.frontmatter.get("priority") if t else None
    return (priority if isinstance(priority, int) else 99, c.ticket_id)


def cmd_classify(tickets: list[Ticket], json_out: bool = False) -> int:
    """Classify open tickets into active/stale/likely-zombie/confirmed-zombie buckets."""
    ticket_map = _ticket_map(tickets)
    results = classify_tickets(tickets)

    if json_out:
        import json
        data: dict[str, Any] = {"schema_version": "1.0"}
        categories: dict[str, list[dict[str, Any]]] = {}
        for c in results:
            categories.setdefault(c.category, []).append({
                "ticket_id": c.ticket_id,
                "subcategory": c.subcategory,
                "rationale": c.rationale,
                "signals": c.signals,
            })
        data["categories"] = categories
        print(json.dumps(data, indent=2))
        return 0

    category_order = [
        "4 — Confirmed Zombie / Structural Defect",
        "3 — Likely Zombie",
        "2 — Stale / Needs Review",
        "1 — Active / Legitimate",
    ]

    by_category: dict[str, list[Classification]] = {}
    for c in results:
        by_category.setdefault(c.category, []).append(c)

    for cat in category_order:
        items = by_category.get(cat, [])
        if not items:
            continue
        print(f"\n{cat} ({len(items)})")
        print("-" * 80)
        for c in sorted(items, key=lambda x: _classify_sort_key(x, ticket_map)):
            t = ticket_map.get(c.ticket_id)
            p = f"P{t.frontmatter.get('priority', '?')}" if t else "P?"
            effort = _effort_str(t) if t else "?h"
            epic = _epic_name(t) if t else "NO-EPIC"
            print(f"  {c.ticket_id:<20} {epic:<10} {p:<3} {effort:>5}  {c.subcategory}")
            print(f"    → {c.rationale}")

    print(f"\nTotal open tickets classified: {len(results)}")
    return 0


_OPEN_INDICATOR_RE = re.compile(
    r"(⬜|🔄|📋|\bopen\b|\bOPEN\b|\bActive\b|\bBlocked\b|\bblocked\b)",
    re.IGNORECASE,
)


def _lint_docs(tickets: list[Ticket]) -> int:
    """Scan README.md and AGENTS.md for stale ticket references.

    Flags:
    - Ticket IDs referenced on lines with 'open' indicators (⬜, 🔄, etc.) when
      the ticket is actually CLOSED or WONT_FIX.
    - Ticket IDs that do not exist as ticket files.
    - WONT_FIX tickets referenced anywhere (these should not be in status tables).
    """
    ticket_map = _ticket_map(tickets)
    found_issues = False

    for doc_name in _DOC_FILES:
        doc_path = Path(doc_name)
        if not doc_path.exists():
            continue
        lines = doc_path.read_text(encoding="utf-8").splitlines()
        seen: set[str] = set()
        for lineno, line in enumerate(lines, start=1):
            for m in _TICKET_ID_IN_TEXT_RE.finditer(line):
                tid = m.group(1)
                if tid in seen:
                    continue
                # Skip IDs that are part of a range like EM-0175–0178 or EM-1506–1512
                start, end = m.span()
                prefix = line[max(0, start - 1) : start]
                suffix = line[end : min(len(line), end + 1)]
                if prefix == "–" or suffix == "–":
                    continue
                seen.add(tid)
                if tid in _KNOWN_NON_TICKETS:
                    continue
                if tid not in ticket_map:
                    print(
                        f"  [{tid}] [docs] referenced in {doc_name}:{lineno} "
                        "but ticket file does not exist"
                    )
                    found_issues = True
                    continue
                ticket = ticket_map[tid]
                status = ticket.frontmatter.get("status", "").upper()
                if status == "CLOSED" and _OPEN_INDICATOR_RE.search(line):
                    print(
                        f"  [{tid}] [docs] referenced in {doc_name}:{lineno} "
                        "with open indicator but ticket is CLOSED"
                    )
                    found_issues = True
                    continue
                if status == "WONT_FIX" and _OPEN_INDICATOR_RE.search(line):
                    print(
                        f"  [{tid}] [docs] referenced in {doc_name}:{lineno} "
                        "with open indicator but ticket is WONT_FIX"
                    )
                    found_issues = True

    if not found_issues:
        print("  No stale ticket references found in README.md / AGENTS.md")
    return 1 if found_issues else 0


def cmd_lint(new: bool = False, docs: bool = False, json_out: bool = False, docs_drift: bool = False) -> int:
    if docs:
        tickets = _collect_tickets()
        print("=== Ticket Hygiene Audit (docs) ===")
        return _lint_docs(tickets)

    cmd = [sys.executable, "scripts/ticket_hygiene/audit_tickets.py"]
    if json_out:
        cmd.append("--json")
    if docs_drift:
        cmd.append("--docs-drift")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    output = result.stdout
    if new and _LAST_RUN_FILE.exists() and not json_out:
        previous = _LAST_RUN_FILE.read_text(encoding="utf-8")
        prev_lines = set(previous.splitlines())
        new_lines = [line for line in output.splitlines() if line not in prev_lines]
        if new_lines:
            print("\n".join(new_lines))
        else:
            print("No new findings.")
    else:
        print(output, end="")

    _LAST_RUN_FILE.write_text(output, encoding="utf-8")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ticket lookup CLI")
    subparsers = parser.add_subparsers(dest="command")

    find_p = subparsers.add_parser("find", help="Find ticket file path")
    find_p.add_argument("ticket_id")

    subparsers.add_parser("open", help="List all OPEN tickets by epic")

    deps_p = subparsers.add_parser("deps", help="Show depends_on / blocks chain")
    deps_p.add_argument("ticket_id")

    show_p = subparsers.add_parser("show", help="Show ticket file content")
    show_p.add_argument("ticket_id")

    create_p = subparsers.add_parser("create", help="Scaffold a new ticket")
    create_p.add_argument("ticket_id", nargs="?", help="Ticket ID (e.g., EM-1171). Omit with --auto-id.")
    create_p.add_argument("title")
    create_p.add_argument("--epic", required=True, help="Epic ID (e.g., EM-E020)")
    create_p.add_argument("--effort", default="2h", help="Effort estimate (default: 2h)")
    create_p.add_argument("--risk", default="LOW", choices=["LOW", "MEDIUM", "HIGH"], help="Risk level (default: LOW)")
    create_p.add_argument("--priority", type=int, default=3, choices=[1, 2, 3, 4, 5], help="Priority (default: 3)")
    create_p.add_argument("--auto-id", action="store_true", help="Auto-assign next available numeric ID")
    create_p.add_argument("--epic-sequence", action="store_true", help="Scope auto-id to this epic's tickets only")
    create_p.add_argument("--dry-run", action="store_true", help="Show what would be created without writing")

    cleanup_p = subparsers.add_parser("close-cleanup", help="Update cross-references when a ticket closes")
    cleanup_p.add_argument("ticket_id")
    cleanup_p.add_argument("--write", action="store_true", help="Apply changes (default: dry run)")

    close_p = subparsers.add_parser("close", help="Atomically close a ticket (frontmatter, move, cross-refs, index)")
    close_p.add_argument("ticket_id")
    close_p.add_argument("--date", help="Override closure date (default: today)")
    close_p.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    close_p.add_argument("--skip-cross-refs", action="store_true", help="Skip updating tickets that reference this one")
    close_p.add_argument("--force", action="store_true", help="Close even if retro is missing or OPEN dependencies remain")

    clean_deps_p = subparsers.add_parser("clean-deps", help="Bulk-remove stale dependencies (closed/WONT_FIX) from all open tickets")
    clean_deps_p.add_argument("--write", action="store_true", help="Apply changes (default: dry run)")

    edit_p = subparsers.add_parser("edit", help="Edit frontmatter fields of a ticket")
    edit_p.add_argument("ticket_id")
    edit_p.add_argument("--title", help="Update title")
    edit_p.add_argument("--status", choices=["OPEN", "CLOSED", "STANDING", "WONT_FIX"], help="Update status")
    edit_p.add_argument("--effort", help="Update effort (e.g., '4h', '1d')")
    edit_p.add_argument("--priority", type=int, choices=[1, 2, 3, 4, 5], help="Update priority")
    edit_p.add_argument("--risk", choices=["LOW", "MEDIUM", "HIGH"], help="Update risk")
    edit_p.add_argument("--epic", help="Update epic")
    edit_p.add_argument("--depends-on", dest="depends_on", help="Update depends_on (comma-separated)")
    edit_p.add_argument("--blocks", help="Update blocks (comma-separated)")

    estimate_p = subparsers.add_parser("estimate", help="Interactive TUI for bulk-adding effort estimates to unestimated open tickets")
    estimate_p.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing")

    history_p = subparsers.add_parser("history", help="Show git history for a ticket")
    history_p.add_argument("ticket_id")
    history_p.add_argument("--oneline", action="store_true", help="Compact output")

    lint_p = subparsers.add_parser("lint", help="Run ticket hygiene scanner")
    lint_p.add_argument("--new", action="store_true", help="Only show new findings since last run")
    lint_p.add_argument("--docs", action="store_true", help="Check README.md and AGENTS.md for stale ticket references")
    lint_p.add_argument("--docs-drift", action="store_true", help="Check open tickets for contradictions with locked AGENTS.md architecture")
    lint_p.add_argument("--json", action="store_true", help="Output findings as JSON")

    stats_p = subparsers.add_parser("stats", help="Ticket statistics dashboard")
    stats_p.add_argument("--epic", help="Scope to one epic (e.g., EM-E020)")
    stats_p.add_argument("--week", action="store_true", help="Show last-7-days velocity")
    stats_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    classify_p = subparsers.add_parser("classify", help="Classify open tickets by health / zombie likelihood")
    classify_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    impact_p = subparsers.add_parser("impact", help="Cross-ticket file impact: find open tickets sharing files with a target")
    impact_p.add_argument("ticket_id", help="Target ticket ID (e.g. EM-2020)")
    impact_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    health_p = subparsers.add_parser("health", help="Ticket health dashboard: age, blocking duration, severity")
    health_p.add_argument("--epic", help="Filter to one epic (e.g. EM-E020)")
    health_p.add_argument("--today", help="Override today's date (YYYY-MM-DD)")
    health_p.add_argument("--threshold-yellow", type=int, metavar="DAYS", help="Days before 🟡 (default: 14)")
    health_p.add_argument("--threshold-red", type=int, metavar="DAYS", help="Days before 🔴 (default: 30)")
    health_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    rename_p = subparsers.add_parser("rename", help="Rename a ticket and update references")
    rename_p.add_argument("old_id", help="Current ticket ID")
    rename_p.add_argument("new_id", help="New ticket ID")
    rename_p.add_argument("--write", action="store_true", help="Apply changes (default: dry run)")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    tickets = _collect_tickets()
    epic_dirs = _collect_epic_dirs()

    if args.command == "find":
        return cmd_find(tickets, args.ticket_id)
    if args.command == "open":
        return cmd_open(tickets)
    if args.command == "deps":
        return cmd_deps(tickets, args.ticket_id)
    if args.command == "show":
        return cmd_show(tickets, args.ticket_id)
    if args.command == "create":
        epic_dirs = _collect_epic_dirs()
        return cmd_create(
            tickets,
            epic_dirs,
            args.ticket_id,
            args.title,
            args.epic,
            args.effort,
            args.risk,
            args.priority,
            auto_id=args.auto_id,
            epic_sequence=args.epic_sequence,
            dry_run=args.dry_run,
        )
    if args.command == "close-cleanup":
        return cmd_close_cleanup(tickets, args.ticket_id, write=args.write)
    if args.command == "close":
        return cmd_close(
            tickets,
            epic_dirs,
            args.ticket_id,
            date=args.date,
            dry_run=args.dry_run,
            skip_cross_refs=args.skip_cross_refs,
            force=args.force,
        )
    if args.command == "clean-deps":
        return cmd_clean_deps(tickets, write=args.write)
    if args.command == "edit":
        return cmd_edit(
            tickets, args.ticket_id,
            title=args.title,
            status=args.status,
            effort=args.effort,
            priority=args.priority,
            risk=args.risk,
            epic=args.epic,
            depends_on=args.depends_on,
            blocks=args.blocks,
        )
    if args.command == "estimate":
        return cmd_estimate(tickets, dry_run=args.dry_run)
    if args.command == "history":
        return cmd_history(tickets, args.ticket_id, oneline=args.oneline)
    if args.command == "lint":
        return cmd_lint(new=args.new, docs=args.docs, json_out=args.json, docs_drift=args.docs_drift)
    if args.command == "stats":
        return cmd_stats(tickets, epic=args.epic, week=args.week, json_out=args.json)
    if args.command == "classify":
        return cmd_classify(tickets, json_out=args.json)
    if args.command == "impact":
        return cmd_impact(tickets, args.ticket_id, json_out=args.json)
    if args.command == "health":
        return cmd_health(
            tickets,
            epic=args.epic,
            today=args.today,
            threshold_yellow=args.threshold_yellow,
            threshold_red=args.threshold_red,
            json_out=args.json,
        )
    if args.command == "rename":
        epic_dirs = _collect_epic_dirs()
        return cmd_rename(tickets, epic_dirs, args.old_id, args.new_id, write=args.write)
    return 1


if __name__ == "__main__":
    sys.exit(main())

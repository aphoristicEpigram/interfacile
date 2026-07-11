#!/usr/bin/env python3
"""Precondition helper for the fix-now workflow.

Usage:
    python3 fix_now_ticket.py EM-XXXX

Checks:
- ticket exists and is OPEN
- all depends_on tickets are CLOSED
- files in Files Touched exist (or flags missing ones)

Does NOT check for a Pre-Work Staff Review block — this skill assumes scope
has already been approved. Outputs an implementation-plan scaffold if
preconditions pass.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

TICKETS_ROOT = Path("tickets")


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        return raw[1:-1]
    lower = raw.lower()
    if lower in ("true", "yes"):
        return True
    if lower in ("false", "no"):
        return False
    if lower in ("null", "~"):
        return None
    return raw


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        items: list[Any] = []
        for part in inner.split(","):
            part = part.strip()
            if part:
                items.append(_parse_scalar(part))
        return items
    return _parse_scalar(raw)


def parse_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fm_text = parts[1]
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_lines
        if current_key is not None:
            value_text = " ".join(current_lines)
            result[current_key] = _parse_value(value_text)
            current_key = None
            current_lines = []

    for line in fm_text.splitlines():
        if not line.strip():
            continue
        match = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if match:
            flush()
            current_key = match.group(1)
            current_lines = [match.group(2)]
        elif current_key is not None:
            current_lines.append(line.strip())
    flush()
    return result


def find_ticket(ticket_id: str) -> Path | None:
    pattern = f"*{ticket_id}*.md"
    matches = list(TICKETS_ROOT.rglob(pattern))
    exact = [m for m in matches if m.stem == ticket_id]
    return exact[0] if exact else (matches[0] if matches else None)


def dependency_status(paths: list[Any]) -> list[tuple[str, str, Path | None]]:
    rows: list[tuple[str, str, Path | None]] = []
    for dep in paths:
        dep_id = str(dep)
        dep_path = find_ticket(dep_id)
        if dep_path is None:
            rows.append((dep_id, "NOT FOUND", None))
            continue
        fm = parse_frontmatter(dep_path)
        status = fm.get("status", "UNKNOWN")
        rows.append((dep_id, status, dep_path))
    return rows


def extract_files_touched(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"##\s*Files\s*(?:Touched|touched|Changed|changed|changed/added).*?\n(.*?)(?:\n----|\n## |\Z)",
        text,
        re.DOTALL,
    )
    if not match:
        return []
    section = match.group(1)
    files: list[str] = []
    for line in section.splitlines():
        line = line.strip()
        if not line or not line.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
            continue
        quoted = re.search(r"`([^`]+)`", line)
        if quoted:
            candidate = quoted.group(1).strip()
            if candidate and not candidate.startswith("#"):
                files.append(candidate)
    return files


def file_exists_status(files: list[str]) -> list[str]:
    rows: list[str] = []
    for f in files:
        p = Path(f)
        marker = "✅" if p.exists() else "❌ MISSING"
        rows.append(f"- {marker} `{f}`")
    return rows


def recent_git_log(n: int = 20) -> str:
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{n}"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "_Unable to retrieve git log._"


def format_frontmatter(fm: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in fm.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif value is None:
            lines.append(f"{key}: null")
        else:
            text = str(value)
            if any(c in text for c in [":", "#", "[", "]", ",", "'", '"']) or text == "":
                escaped = text.replace('"', '\\"')
                lines.append(f'{key}: "{escaped}"')
            else:
                lines.append(f"{key}: {text}")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: fix_now_ticket.py EM-XXXX", file=sys.stderr)
        return 1

    ticket_id = sys.argv[1].strip()
    ticket_path = find_ticket(ticket_id)
    if ticket_path is None:
        print(f"Ticket {ticket_id} not found under {TICKETS_ROOT}/", file=sys.stderr)
        return 1

    fm = parse_frontmatter(ticket_path)
    status = fm.get("status", "UNKNOWN")
    title = fm.get("title", ticket_id)
    deps = fm.get("depends_on") or []
    files = extract_files_touched(ticket_path)
    today = date.today().isoformat()

    # Halt 1: not OPEN.
    if status != "OPEN":
        print(f"⛔ Ticket {ticket_id} is `{status}`, not OPEN.")
        print(f"   File: {ticket_path}")
        return 1

    # Note: no staff-review check in fix-now. Scope is assumed approved.

    # Halt 2: open dependencies.
    dep_rows = dependency_status(deps)
    open_deps = [(tid, st, p) for tid, st, p in dep_rows if st not in ("CLOSED", "WONT_FIX")]
    if open_deps:
        print(f"⛔ {ticket_id} has open dependencies:\n")
        for tid, st, p in open_deps:
            loc = p.relative_to(TICKETS_ROOT) if p else "NOT FOUND"
            print(f"- `{tid}` — `{st}` — `{loc}`")
        return 1

    # Preconditions pass — emit scaffold.
    print(f"# Fix Ticket Scaffold — {ticket_id}\n")
    print(f"**Ticket file:** `{ticket_path}`\n")
    print("## Frontmatter\n")
    print("```yaml")
    print(format_frontmatter(fm))
    print("```\n")

    print("## Scope Status\n")
    print("⏩ fix-now: scope is assumed approved. No staff-review check performed.\n")

    print("## Dependencies\n")
    if deps:
        for tid, st, p in dep_rows:
            loc = p.relative_to(TICKETS_ROOT) if p else "NOT FOUND"
            print(f"- `{tid}` — `{st}` — `{loc}`")
    else:
        print("_None._")
    print("\n")

    print("## Files Touched (existence check)\n")
    if files:
        print("\n".join(file_exists_status(files)))
    else:
        print("_No files extracted. Add them to a `## Files Touched` section._")
    print("\n")

    print("## Recent Git Log\n")
    print("```")
    print(recent_git_log())
    print("```\n")

    print("## Implementation Plan\n")
    print(f"**Ticket:** {title}")
    print("**Effort estimate:** __ dev-hours")
    print("**Approach:** <one paragraph — the A+ solution>\n")

    print("### Files I will touch\n")
    print("| File | Change |")
    print("|------|--------|")
    for f in files:
        print(f"| `{f}` | <what changes and why> |")
    if not files:
        print("| _TBD_ | _Add after reading ticket_ |")
    print("\n")

    print("### Implementation order\n")
    print("1. <first change>")
    print("2. <second change>")
    print("3. <third change>\n")

    print("### Test plan\n")
    print("- <what the new/changed tests verify>")
    print("- Extended suite needed? <YES / NO — state the AGENTS.md criterion>\n")

    print("### Concerns\n")
    print("<Anything that needs a decision or could go wrong. If none: \"None identified.\">\n")

    print(f"Awaiting approval to begin. ({today})\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

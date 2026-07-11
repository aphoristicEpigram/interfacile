#!/usr/bin/env python3
"""Pre-flight helper for approving/completing a ticket's "If I Had 3 More Hours" items.

Usage:
    python3 approve_ticket.py EM-XXXX

Checks:
- ticket exists and is in an open/ directory
- ticket contains a ## Retro section
- retro contains ### If I Had 3 More Hours
- lists Files Touched for the A+ review

Does NOT close or commit — it only validates preconditions.
"""

from __future__ import annotations

import re
import subprocess
import sys
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


def extract_three_more_hours(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"###\s*If\s+I\s+Had\s+3\s+More\s+Hours\s*\n(.*?)(?:\n### |\n## |\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return []
    bullets: list[str] = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if line.startswith(("-", "*")):
            bullets.append(line.lstrip("-* ").strip())
    return bullets


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: finish_ticket.py EM-XXXX", file=sys.stderr)
        return 1

    ticket_id = sys.argv[1].strip()
    ticket_path = find_ticket(ticket_id)
    if ticket_path is None:
        print(f"Ticket {ticket_id} not found under {TICKETS_ROOT}/", file=sys.stderr)
        return 1

    rel = ticket_path.relative_to(TICKETS_ROOT)
    text = ticket_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(ticket_path)
    files = extract_files_touched(ticket_path)
    bullets = extract_three_more_hours(ticket_path)

    errors: list[str] = []

    # Check 1: in open/
    if "/open/" not in str(ticket_path):
        errors.append(f"Ticket is not in an `open/` directory: `{rel}`")

    # Check 2: retro section exists
    retro_match = re.search(r"##\s*Retro", text, re.IGNORECASE)
    if not retro_match:
        errors.append("Missing `## Retro` section.")

    # Check 3: If I Had 3 More Hours exists
    three_hours_match = re.search(
        r"###\s*If\s+I\s+Had\s+3\s+More\s+Hours",
        text,
        re.IGNORECASE,
    )
    if not three_hours_match:
        errors.append("Missing `### If I Had 3 More Hours` section in retro.")

    print(f"# Approve Pre-Flight — {ticket_id}\n")
    print(f"**Ticket file:** `{rel}`\n")

    if errors:
        print("## ❌ Pre-flight failed\n")
        for err in errors:
            print(f"- {err}")
        print()
        return 1

    print("## ✅ Pre-flight passed\n")

    print("## Frontmatter\n")
    print("```yaml")
    print(format_frontmatter(fm))
    print("```\n")

    print("## Files Touched (for A+ review)\n")
    if files:
        print("\n".join(file_exists_status(files)))
    else:
        print("_No files extracted. Add them to a `## Files Touched` section._")
    print("\n")

    print("## \"If I Had 3 More Hours\" bullets\n")
    if bullets:
        for b in bullets:
            print(f"- {b}")
    else:
        print("_No bullets found._")
    print("\n")

    print("## Recent Git Log\n")
    print("```")
    print(recent_git_log())
    print("```\n")

    print("## Next steps\n")
    print("1. Read every file above.")
    print("2. Write the A+ review block.")
    print("3. Fix polish issues and test gaps.")
    print("4. Complete every `If I Had 3 More Hours` bullet.")
    print("5. Append the Follow-up Work Completed note.")
    print("6. Commit according to the chosen mode.")
    print("7. Halt. Do NOT run the hygiene close tool.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

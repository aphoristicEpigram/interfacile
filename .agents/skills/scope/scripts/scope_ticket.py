#!/usr/bin/env python3
"""Ground-truth helper for staff-engineer ticket reviews.

Usage:
    python3 scope_ticket.py EM-XXXX

Outputs a markdown scaffold to stdout containing:
- ticket location and frontmatter
- epic summary
- dependency / block status
- recent git log
- files-touched existence check
- blank assessment sections

No third-party dependencies are required.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

TICKETS_ROOT = Path("tickets")


def _parse_value(raw: str) -> Any:
    """Parse a simple YAML-like value: string, list, or date."""
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
    # Leave dates as strings; dates are ISO-formatted.
    return raw


def parse_frontmatter(path: Path) -> dict[str, Any]:
    """Extract and parse the YAML-like frontmatter between --- fences."""
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
        # Skip empty lines.
        if not line.strip():
            continue
        # New top-level key.
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
    """Locate the ticket markdown file anywhere under tickets/."""
    pattern = f"*{ticket_id}*.md"
    matches = list(TICKETS_ROOT.rglob(pattern))
    exact = [m for m in matches if m.stem == ticket_id]
    return exact[0] if exact else (matches[0] if matches else None)


def find_epic_file(epic_id: str) -> Path | None:
    """Find the epic markdown file.

    Epic directories follow the pattern `tickets/<epic-id>-<suffix>/`
    with a matching `<epic-id>-<suffix>.md` file inside.
    """
    for child in TICKETS_ROOT.iterdir():
        if child.is_dir() and child.name.startswith(epic_id):
            candidate = child / f"{child.name}.md"
            if candidate.exists():
                return candidate
    return None


def epic_summary(path: Path | None) -> str:
    if path is None:
        return "_Epic file not found._"
    fm = parse_frontmatter(path)
    title = fm.get("title", path.stem)
    status = fm.get("status", "UNKNOWN")
    text = path.read_text(encoding="utf-8")
    # Strip frontmatter if present.
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    first_para = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "---":
            continue
        first_para = stripped
        break
    return f"**{title}** — status `{status}`\n\n{first_para}"


def dependency_status(paths: list[Any]) -> list[str]:
    """For each dependency ID, report its current status."""
    rows: list[str] = []
    for dep in paths:
        dep_id = str(dep)
        dep_path = find_ticket(dep_id)
        if dep_path is None:
            rows.append(f"- `{dep_id}`: **NOT FOUND**")
            continue
        fm = parse_frontmatter(dep_path)
        status = fm.get("status", "UNKNOWN")
        rel = dep_path.relative_to(TICKETS_ROOT)
        rows.append(f"- `{dep_id}`: `{status}` — `{rel}`")
    return rows


def extract_files_touched(path: Path) -> list[str]:
    """Best-effort extraction of file paths from the Files Touched section."""
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
        # Take the first backtick-delimited token on the line as the file path.
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
        exists = p.exists()
        marker = "✅" if exists else "❌ MISSING"
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


def implementation_status(files: list[str]) -> str:
    """Report whether files touched have uncommitted changes or recent commits.

    Returns a markdown block describing git status and diff stats for the
    ticket's files.  This helps the reviewer detect already-implemented work.
    """
    if not files:
        return "_No files extracted._"

    existing = [f for f in files if Path(f).exists()]
    if not existing:
        return "_None of the listed files exist in the working tree._"

    lines: list[str] = []

    # Status of touched files.
    try:
        result = subprocess.run(
            ["git", "status", "--short", "--"] + existing,
            capture_output=True,
            text=True,
            check=True,
        )
        status = result.stdout.strip()
        if status:
            lines.append("**Working-tree changes:**")
            lines.append("```")
            lines.append(status)
            lines.append("```")
        else:
            lines.append("**Working-tree changes:** none")
    except (subprocess.CalledProcessError, FileNotFoundError):
        lines.append("_Unable to retrieve git status._")

    # Diff stat against HEAD.
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD", "--"] + existing,
            capture_output=True,
            text=True,
            check=True,
        )
        diff_stat = result.stdout.strip()
        if diff_stat:
            lines.append("\n**Diff against HEAD:**")
            lines.append("```")
            lines.append(diff_stat)
            lines.append("```")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Recent commits touching these files.
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-10", "--"] + existing,
            capture_output=True,
            text=True,
            check=True,
        )
        file_log = result.stdout.strip()
        if file_log:
            lines.append("\n**Recent commits touching these files:**")
            lines.append("```")
            lines.append(file_log)
            lines.append("```")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return "\n".join(lines) if lines else "_No implementation status available._"


def format_frontmatter(fm: dict[str, Any]) -> str:
    """Render frontmatter as YAML-like text without external dependencies."""
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
            # Quote strings that contain colons or special characters.
            text = str(value)
            if any(c in text for c in [":", "#", "[", "]", ",", "'", '"']) or text == "":
                escaped = text.replace('"', '\\"')
                lines.append(f'{key}: "{escaped}"')
            else:
                lines.append(f"{key}: {text}")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: scope_ticket.py EM-XXXX", file=sys.stderr)
        return 1

    ticket_id = sys.argv[1].strip()
    ticket_path = find_ticket(ticket_id)
    if ticket_path is None:
        print(f"Ticket {ticket_id} not found under {TICKETS_ROOT}/", file=sys.stderr)
        return 1

    fm = parse_frontmatter(ticket_path)
    epic_id = fm.get("epic", "UNKNOWN")
    epic_path = find_epic_file(epic_id) if epic_id != "UNKNOWN" else None
    deps = fm.get("depends_on") or []
    blocks = fm.get("blocks") or []
    files = extract_files_touched(ticket_path)
    today = date.today().isoformat()

    print(f"# Staff Review Scaffold — {ticket_id}\n")
    print(f"**Ticket file:** `{ticket_path}`\n")
    print("## Frontmatter\n")
    print("```yaml")
    print(format_frontmatter(fm))
    print("```\n")

    print("## Epic\n")
    print(epic_summary(epic_path))
    print("\n")

    print("## Dependencies\n")
    if deps:
        print("\n".join(dependency_status(deps)))
    else:
        print("_None._")
    print("\n")

    print("## Blocks\n")
    if blocks:
        print("\n".join(dependency_status(blocks)))
    else:
        print("_None._")
    print("\n")

    print("## Files Touched (existence check)\n")
    if files:
        print("\n".join(file_exists_status(files)))
    else:
        print("_No files extracted. Add them to a `## Files Touched` section._")
    print("\n")

    print("## Implementation Status\n")
    print(implementation_status(files))
    print("\n")

    print("## Recent Git Log\n")
    print("```")
    print(recent_git_log())
    print("```\n")

    print("## Staff Engineer Assessment\n")
    print("### 3a. Validity\n")
    print("- [ ] Problem is real and not already fixed")
    print("- [ ] Referenced code/files exist in current form")
    print("- [ ] No conflict with EM-E005 Rust-port decisions")
    print("- [ ] Not a duplicate of another open ticket\n")

    print("### 3b. Architecture\n")
    print("- [ ] Reflects current module structure")
    print("- [ ] Rust-portable (no dynamic dispatch, monkey-patching, importlib tricks)")
    print("- [ ] Aligns with EM-E018 security posture and EM-E001 detection contracts")
    print("- [ ] A+ alternative considered\n")

    print("### 3c. Tests\n")
    print("- [ ] Happy path covered")
    print("- [ ] Failure modes covered")
    print("- [ ] Edge cases covered")
    print("- [ ] Adversarial / fuzzing cases included where appropriate")
    print("- [ ] Tests target behaviour, not implementation internals\n")

    print("### 3d. Documentation\n")
    print("- [ ] `docs/guides/` updated if user-facing behaviour changed")
    print("- [ ] `docs/security/` updated if security posture changed")
    print("- [ ] README / AGENTS.md / CHANGELOG updated if public API changed\n")

    print("### 3e. Effort\n")
    print("- Estimated effort: __ dev-days")
    print(f"- Assessment date: {today}\n")

    print("## Verdict\n")
    print("- [ ] VALID — proceed (insert Pre-Work Staff Review block)")
    print("- [ ] ALREADY IMPLEMENTED — perform close ceremony instead")
    print("- [ ] INVALID — close as WONT_FIX and supersede")
    print("- [ ] TOO LARGE — decompose into child tickets\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

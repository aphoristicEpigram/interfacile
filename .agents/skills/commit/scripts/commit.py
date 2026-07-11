#!/usr/bin/env python3
"""Stage a commit scope, or everything if no scope is given.

Usage:
    python3 commit.py                    # stage everything (commit-all default)
    python3 commit.py EM-XXXX           # stage ticket file + Files Touched
    python3 commit.py path/to/file.py   # stage a specific path

For a ticket ID, stages the ticket file and the files listed in the ticket's
Files Touched section. For a file path, stages only that path. With no
argument (or "all"), stages the entire working tree.

This helper stages but does not commit. The caller writes the commit message
and runs `git commit`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TICKETS_ROOT = Path("tickets")
PROJECT_ROOT = Path(".")


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def find_ticket(ticket_id: str) -> Path | None:
    pattern = f"*{ticket_id}*.md"
    matches = list(TICKETS_ROOT.rglob(pattern))
    exact = [m for m in matches if m.stem == ticket_id]
    return exact[0] if exact else (matches[0] if matches else None)


def extract_files_touched(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"##\s*Files\s*(?:Touched|touched|Changed|changed|changed/added).*?\n(.*?)" r"(?:\n----|\n## |\Z)",
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


def normalize_ticket_id(scope: str) -> str | None:
    if re.fullmatch(r"EM-\d+", scope, re.IGNORECASE):
        return scope.upper()
    if re.fullmatch(r"\d+", scope):
        return f"EM-{scope}"
    return None


def stage_scope(scope: str) -> list[str]:
    ticket_id = normalize_ticket_id(scope)
    if ticket_id is not None:
        return stage_ticket_scope(ticket_id)

    path = Path(scope)
    if not path.exists():
        print(f"Path does not exist: {scope}", file=sys.stderr)
        sys.exit(1)
    run_git("add", str(path))
    return [str(path)]


def stage_ticket_scope(ticket_id: str) -> list[str]:
    ticket_path = find_ticket(ticket_id)
    if ticket_path is None:
        print(f"Ticket {ticket_id} not found under {TICKETS_ROOT}/", file=sys.stderr)
        sys.exit(1)

    files = extract_files_touched(ticket_path)
    staged: list[str] = []

    for f in files:
        p = Path(f)
        if p.exists():
            run_git("add", str(p))
            staged.append(str(p))
        else:
            print(f"⚠️  Files Touched file does not exist, skipping: {f}")

    # Always stage the ticket file itself if it has changed.
    result = run_git("status", "--porcelain", str(ticket_path), check=False)
    if result.stdout.strip():
        run_git("add", str(ticket_path))
        staged.append(str(ticket_path))

    return staged


def stage_all() -> int:
    result = run_git("add", "-A")
    if result.returncode != 0:
        print(f"git add -A failed: {result.stderr}", file=sys.stderr)
        return 1

    print("# Staged all changes for a clean working tree\n")

    stat = run_git("diff", "--cached", "--stat", check=False)
    if stat.stdout.strip():
        print("## Staged diff stat")
        print(stat.stdout)
    else:
        print("Nothing staged.")
        return 1

    return 0


def main() -> int:
    if len(sys.argv) > 2:
        print("Usage: commit.py [EM-XXXX | path/to/file.py]", file=sys.stderr)
        return 1

    if len(sys.argv) == 1:
        return stage_all()

    scope = sys.argv[1].strip()
    if scope.lower() == "all":
        return stage_all()

    normalized = normalize_ticket_id(scope)
    display_scope = normalized if normalized is not None else scope
    staged = stage_scope(scope)

    if not staged:
        print("Nothing to stage.")
        return 1

    print(f"# Staged {len(staged)} item(s) for scope: {display_scope}\n")
    for item in staged:
        print(f"- {item}")

    result = run_git("diff", "--cached", "--stat", check=False)
    if result.stdout.strip():
        print("\n## Staged diff stat")
        print(result.stdout)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

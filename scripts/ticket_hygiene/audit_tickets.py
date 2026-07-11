#!/usr/bin/env python3
"""Ticket hygiene scanner — detect stale tickets, mismatched frontmatter, and broken cross-references.

Run from the project root:
    python scripts/ticket_hygiene/audit_tickets.py
    python scripts/ticket_hygiene/audit_tickets.py --write-baseline
    python scripts/ticket_hygiene/audit_tickets.py --check-baseline

Exit codes:
    0 — clean (no errors beyond baseline)
    1 — errors found (or new errors beyond baseline)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from _source_scanner import scan_pii_leaks, scan_v2_residue
from link_validator import check_links
from yaml.constructor import ConstructorError

# Target repo to scan. Defaults to "tickets" under the current working directory,
# so hygiene commands run in-repo as before. Set $TICKET_DASHBOARD_REPO to point the
# whole hygiene engine at another project's tickets/ tree (used when this kit lives
# in a separate control repo that manages several projects).
TICKETS_DIR = Path(os.environ.get("TICKET_DASHBOARD_REPO", ".")) / "tickets"
INDEX_FILE = TICKETS_DIR / "TICKET_INDEX.md"
BASELINE_FILE = Path(".ticket_hygiene_baseline.json")

# Ticket ID reference in markdown tables or body
# Matches EM-NNNN, old-format EM-NNNNA, and compound IDs like EM-0123-CP-9, EM-0175-A, EM-0123-B.1
_TICKET_REF_RE = re.compile(r"\b(EM-(?:E\d+|\d+[A-Z]*)(?:-[A-Z0-9.]+)*)\b(?![A-Za-z0-9_-])")
_EFFORT_RE = re.compile(r"^[\d.]+\s*[hdm]$|^[\d.]+\s*[-–]\s*[\d.]+\s*[hd]$")

# Allowed values
_ALLOWED_STATUS = {"OPEN", "CLOSED", "STANDING", "WONT_FIX"}
_CLOSED_STATUS_MARKERS = {"CLOSED", "WONT_FIX"}
_ALLOWED_RISK = {"LOW", "MEDIUM", "HIGH"}
_ALLOWED_PRIORITY = {1, 2, 3, 4, 5}
_REQUIRED_FIELDS = {"id", "title", "epic", "status", "risk", "effort", "depends_on", "blocks", "created", "priority"}


class DuplicateKeyError(ConstructorError):
    pass


def _no_duplicates_constructor(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateKeyError(
                f"duplicate key: {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


yaml.SafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _no_duplicates_constructor,
)


@dataclass
class Finding:
    level: str  # "ERROR" | "WARNING"
    ticket_id: str
    message: str
    check: str = ""


@dataclass
class Ticket:
    path: Path
    ticket_id: str
    frontmatter: dict[str, Any]


def _parse_frontmatter(text: str) -> tuple[dict[str, Any] | None, str | None]:
    if not text.startswith("---"):
        return None, "missing opening ---"
    parts = text.split("\n---", 2)
    if len(parts) < 2:
        return None, "missing closing ---"
    raw = parts[0][3:].strip()  # strip leading ---
    try:
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            return None, "frontmatter is not a mapping"
        return data, None
    except DuplicateKeyError as e:
        return None, f"duplicate key in frontmatter: {e}"
    except yaml.YAMLError as e:
        return None, f"invalid YAML: {e}"


def _collect_tickets() -> list[Ticket]:
    tickets: list[Ticket] = []
    for root, _dirs, files in os.walk(TICKETS_DIR):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            if fname == "TICKET_INDEX.md":
                continue
            path = Path(root) / fname
            text = path.read_text(encoding="utf-8")
            fm, err = _parse_frontmatter(text)
            if fm is None:
                fm = {}
            ticket_id = fm.get("id", "")
            if not ticket_id:
                # Fallback: try to extract from filename for legacy files without frontmatter
                m = re.match(r"^(EM-\d+[A-Z]*(?:-[A-Z0-9.]+)*)(?:\b|[^A-Za-z0-9])", fname)
                ticket_id = m.group(1) if m else ""
            if not ticket_id:
                continue
            tickets.append(Ticket(path=path, ticket_id=ticket_id, frontmatter=fm))
    return tickets


def _collect_epic_dirs() -> dict[str, Path]:
    epics: dict[str, Path] = {}
    for base in (TICKETS_DIR, TICKETS_DIR / "future"):
        if not base.exists():
            continue
        for entry in base.iterdir():
            if entry.is_dir() and entry.name.startswith("EM-"):
                short = entry.name
                if "-" in short[3:]:
                    parts = short.split("-")
                    for i, part in enumerate(parts):
                        if part.startswith("E") and part[1:].isdigit():
                            short = "-".join(parts[: i + 1])
                            break
                epics[short] = entry
    return epics


# Regex for the first column of TICKET_INDEX.md table rows only.
# Only matches ticket IDs that appear as the first cell in a markdown table row.
_INDEX_FIRST_COL_RE = re.compile(r"^\| (EM-[\w.-]+) \|")


def _collect_index_refs() -> dict[str, list[int]]:
    refs: dict[str, list[int]] = {}
    if not INDEX_FILE.exists():
        return refs
    for lineno, line in enumerate(INDEX_FILE.read_text(encoding="utf-8").splitlines(), start=1):
        m = _INDEX_FIRST_COL_RE.match(line)
        if m:
            tid = m.group(1)
            refs.setdefault(tid, []).append(lineno)
    return refs


def _relative(p: Path) -> str:
    return str(p).replace("\\", "/")


def _check_yaml_valid(ticket: Ticket, findings: list[Finding]) -> None:
    text = ticket.path.read_text(encoding="utf-8")
    fm, err = _parse_frontmatter(text)
    if fm is None:
        findings.append(
            Finding("ERROR", ticket.ticket_id, err or "unknown YAML error", "yaml_valid")
        )


def _is_epic_root(ticket: Ticket) -> bool:
    """Epic summary files have IDs like EM-E005 and match the parent directory name."""
    return bool(re.match(r"^EM-E\d+$", ticket.ticket_id))


def _check_required_fields(ticket: Ticket, findings: list[Finding]) -> None:
    fm = ticket.frontmatter
    if not fm:
        return
    # Epic files (id matches EM-E\d+) have a reduced required field set; effort is optional for them
    if _is_epic_root(ticket):
        required = {"id", "title", "status", "created", "priority"}
    else:
        required = _REQUIRED_FIELDS
    missing = required - set(fm.keys())
    if missing:
        findings.append(
            Finding("ERROR", ticket.ticket_id, f"missing required fields: {sorted(missing)}", "required_fields")
        )


def _check_allowed_values(ticket: Ticket, findings: list[Finding]) -> None:
    fm = ticket.frontmatter
    if not fm:
        return
    status = fm.get("status")
    if status and status not in _ALLOWED_STATUS:
        findings.append(
            Finding("ERROR", ticket.ticket_id, f"invalid status: {status!r} (allowed: {_ALLOWED_STATUS})", "allowed_values")
        )
    risk = fm.get("risk")
    if risk and risk not in _ALLOWED_RISK:
        findings.append(
            Finding("ERROR", ticket.ticket_id, f"invalid risk: {risk!r} (allowed: {_ALLOWED_RISK})", "allowed_values")
        )
    priority = fm.get("priority")
    if priority is not None and priority not in _ALLOWED_PRIORITY:
        findings.append(
            Finding("ERROR", ticket.ticket_id, f"invalid priority: {priority!r} (allowed: {_ALLOWED_PRIORITY})", "allowed_values")
        )
    effort = fm.get("effort")
    if effort is not None and effort != "N/A":
        if not isinstance(effort, str) or not _EFFORT_RE.match(str(effort).strip().lower()):
            findings.append(
                Finding("ERROR", ticket.ticket_id, f"invalid effort: {effort!r} (expected format like '2h', '30m', '1-2h', '1d')", "allowed_values")
            )
    elif effort is None and not _is_epic_root(ticket):
        # Missing effort is already caught by required_fields; this is a fallback guard
        pass


def _check_closed_field(ticket: Ticket, findings: list[Finding]) -> None:
    fm = ticket.frontmatter
    if not fm:
        return
    status = fm.get("status", "").upper()
    has_closed = "closed" in fm
    if status in ("OPEN", "STANDING") and has_closed:
        findings.append(
            Finding("ERROR", ticket.ticket_id, f"status: {status} but 'closed:' field is present", "closed_field")
        )
    if status == "CLOSED" and not has_closed:
        findings.append(
            Finding("WARNING", ticket.ticket_id, "status: CLOSED but missing 'closed:' date in frontmatter", "closed_field")
        )


def _check_status_location(ticket: Ticket, findings: list[Finding]) -> None:
    fm = ticket.frontmatter
    if not fm:
        return
    status = fm.get("status", "").upper()
    path_str = str(ticket.path).replace("\\", "/")
    in_closed = "/closed/" in path_str
    in_open = "/open/" in path_str

    if status == "CLOSED" and in_open:
        findings.append(
            Finding("ERROR", ticket.ticket_id, f"status: CLOSED but file is in open/ ({_relative(ticket.path)})", "status_location")
        )
    if status == "OPEN" and in_closed:
        findings.append(
            Finding("ERROR", ticket.ticket_id, f"status: OPEN but file is in closed/ ({_relative(ticket.path)})", "status_location")
        )
    if in_closed and status and status not in _CLOSED_STATUS_MARKERS and status != "OPEN":
        findings.append(
            Finding(
                "ERROR",
                ticket.ticket_id,
                f"file is in closed/ but status is '{status}' (must be one of: {_CLOSED_STATUS_MARKERS})",
                "status_location",
            )
        )


def _check_epic(ticket: Ticket, epic_dirs: dict[str, Path], findings: list[Finding]) -> None:
    fm = ticket.frontmatter
    if not fm:
        return
    # Epic files (id matches EM-E\d+) do not need an epic field — they ARE epics
    is_epic = bool(re.match(r"^EM-E\d+$", ticket.ticket_id))
    epic = fm.get("epic", "")
    if not epic:
        if not is_epic:
            findings.append(
                Finding("WARNING", ticket.ticket_id, "missing 'epic' field in frontmatter", "epic")
            )
        return
    if epic not in epic_dirs:
        findings.append(
            Finding("ERROR", ticket.ticket_id, f"epic '{epic}' does not match any directory in tickets/", "epic")
        )


def _check_index(ticket: Ticket, index_refs: dict[str, list[int]], findings: list[Finding]) -> None:
    fm = ticket.frontmatter
    if fm and fm.get("index_exempt"):
        return
    # Epic files drive section headers, not table rows
    is_epic = bool(re.match(r"^EM-E\d+$", ticket.ticket_id))
    if is_epic:
        return
    tid = ticket.ticket_id
    if tid not in index_refs:
        findings.append(
            Finding("WARNING", tid, f"not referenced in {_relative(INDEX_FILE)}", "index")
        )


def _check_filename(ticket: Ticket, findings: list[Finding]) -> None:
    fname = ticket.path.name
    fm_id = ticket.frontmatter.get("id", "")
    if fm_id:
        if not fname.startswith(fm_id):
            ok = False
        else:
            next_char = fname[len(fm_id)] if len(fname) > len(fm_id) else ""
            ok = not next_char.isalnum()
        if not ok:
            findings.append(
                Finding("ERROR", ticket.ticket_id, f"filename '{fname}' does not start with frontmatter id '{fm_id}'", "filename")
            )


def _check_retro(ticket: Ticket, findings: list[Finding]) -> None:
    fm = ticket.frontmatter
    if not fm:
        return
    if fm.get("status", "").upper() != "CLOSED":
        return
    if fm.get("retro_exempt"):
        return
    if fm.get("retro_in"):
        return
    text = ticket.path.read_text(encoding="utf-8")
    if "## Retro" not in text:
        findings.append(
            Finding("ERROR", ticket.ticket_id, "status: CLOSED but missing '## Retro' section", "retro")
        )


def _check_retro_in(ticket: Ticket, valid_ids: set[str], findings: list[Finding]) -> None:
    fm = ticket.frontmatter
    if not fm:
        return
    retro_in = fm.get("retro_in", "")
    if not retro_in:
        return
    if retro_in not in valid_ids:
        findings.append(
            Finding("ERROR", ticket.ticket_id, f"retro_in references '{retro_in}' but that ticket does not exist", "retro_in")
        )
    status = fm.get("status", "").upper()
    if status != "CLOSED":
        findings.append(
            Finding("ERROR", ticket.ticket_id, f"retro_in is only valid on CLOSED tickets (status: {status})", "retro_in")
        )


def _check_docs_drift(tickets: list[Ticket], findings: list[Finding]) -> None:
    """Optional check: open tickets whose scope may contradict locked AGENTS.md architecture."""
    from classifier import docs_drift_findings
    for drift in docs_drift_findings(tickets):
        findings.append(
            Finding(
                "WARNING",
                drift.ticket_id,
                f"possible docs drift: mentions locked architecture {drift.phrases!r} with drift language ('{drift.paragraph}...')",
                "docs_drift",
            )
        )


def _check_closed_cross_references(tickets: list[Ticket], epic_dirs: dict[str, Path], findings: list[Finding]) -> None:
    """Optional check: stale depends_on/blocks refs in CLOSED tickets (historical record)."""
    ticket_ids = {t.ticket_id for t in tickets}
    valid_refs = ticket_ids | set(epic_dirs.keys())
    for ticket in tickets:
        if ticket.frontmatter.get("status", "").upper() != "CLOSED":
            continue
        for key in ("depends_on", "blocks"):
            val = ticket.frontmatter.get(key, [])
            if isinstance(val, list):
                for ref in val:
                    if ref and ref not in valid_refs:
                        findings.append(
                            Finding("WARNING", ticket.ticket_id, f"{key} references '{ref}' but that ticket file does not exist", "closed_cross_ref")
                        )


def _check_cross_references(tickets: list[Ticket], epic_dirs: dict[str, Path], findings: list[Finding]) -> None:
    ticket_ids = {t.ticket_id for t in tickets}
    valid_refs = ticket_ids | set(epic_dirs.keys())
    for ticket in tickets:
        status = ticket.frontmatter.get("status", "").upper()
        text = ticket.path.read_text(encoding="utf-8")
        for m in re.finditer(r"[Cc]los(?:es|ed by)[:\s]+(EM-\d+[A-Z]*)", text):
            ref = m.group(1)
            if ref not in valid_refs:
                findings.append(
                    Finding("WARNING", ticket.ticket_id, f"references closing '{ref}' but that ticket file does not exist", "cross_ref")
                )
        for key in ("depends_on", "blocks"):
            val = ticket.frontmatter.get(key, [])
            if isinstance(val, list):
                for ref in val:
                    if ref and ref not in valid_refs:
                        if status == "CLOSED":
                            # Historical record — stale refs in closed tickets are expected
                            continue
                        findings.append(
                            Finding("WARNING", ticket.ticket_id, f"{key} references '{ref}' but that ticket file does not exist", "cross_ref")
                        )


def _normalize_dep(dep: Any) -> list[str]:
    """Normalize a depends_on/blocks field to a list of ticket IDs."""
    if dep is None:
        return []
    if isinstance(dep, str):
        return [dep]
    if isinstance(dep, list):
        return [str(d) for d in dep]
    return []


def _check_dependency_sanity(tickets: list[Ticket], findings: list[Finding]) -> None:
    """Flag inverted or reciprocal dependency edges.

    See docs/tickets/EM-1148-ticket-creation-standard.md "Decomposition Tickets".
    """
    by_id = {t.ticket_id: t for t in tickets}

    for ticket in tickets:
        fm = ticket.frontmatter
        if not fm:
            continue
        status = fm.get("status", "").upper()
        deps = _normalize_dep(fm.get("depends_on"))
        blocks = _normalize_dep(fm.get("blocks"))

        # Reciprocal depends_on is a cycle.
        for dep in deps:
            dep_ticket = by_id.get(dep)
            if dep_ticket is None:
                continue
            dep_deps = _normalize_dep(dep_ticket.frontmatter.get("depends_on"))
            if ticket.ticket_id in dep_deps:
                findings.append(
                    Finding(
                        "ERROR",
                        ticket.ticket_id,
                        f"reciprocal depends_on with {dep}: each ticket depends on the other",
                        "dependency_sanity",
                    )
                )

        # Reciprocal blocks is almost always an inversion (A blocks B and B blocks A).
        for blocked in blocks:
            blocked_ticket = by_id.get(blocked)
            if blocked_ticket is None:
                continue
            blocked_blocks = _normalize_dep(blocked_ticket.frontmatter.get("blocks"))
            if ticket.ticket_id in blocked_blocks:
                findings.append(
                    Finding(
                        "ERROR",
                        ticket.ticket_id,
                        f"reciprocal blocks with {blocked}: each ticket blocks the other",
                        "dependency_sanity",
                    )
                )

        # Closed tickets depending on open tickets indicate an inverted or stale graph.
        if status in _CLOSED_STATUS_MARKERS:
            for dep in deps:
                dep_ticket = by_id.get(dep)
                if dep_ticket is None:
                    continue
                dep_status = dep_ticket.frontmatter.get("status", "").upper()
                if dep_status == "OPEN":
                    findings.append(
                        Finding(
                            "WARNING",
                            ticket.ticket_id,
                            f"closed ticket depends_on open ticket {dep}; decomposition parent/child relationship may be inverted",
                            "dependency_sanity",
                        )
                    )


def _check_markdown_links(tickets: list[Ticket], findings: list[Finding]) -> None:
    """Check relative markdown links inside ticket files point to real files."""
    findings.extend(check_links(tickets, TICKETS_DIR))


_STATUS_TO_EMOJI = {
    "OPEN": "⬜",
    "CLOSED": "✅",
    "STANDING": "🏛️",
    "WONT_FIX": "🚫",
}


def _parse_index_rows() -> dict[str, list[tuple[str, str]]]:
    """Parse TICKET_INDEX.md table rows. Returns {ticket_id: [(title, status_emoji), ...]}."""
    rows: dict[str, list[tuple[str, str]]] = {}
    if not INDEX_FILE.exists():
        return rows
    # Match: | EM-XXXX | Title | emoji | ... |
    row_re = re.compile(r"^\| (EM-[\w.-]+) \| (.+?) \| ([✅⬜❌🚫🏛️➡️⬆️🔄]+)")
    for line in INDEX_FILE.read_text(encoding="utf-8").splitlines():
        m = row_re.match(line)
        if m:
            tid = m.group(1)
            title = m.group(2).strip()
            emoji = m.group(3).strip()
            rows.setdefault(tid, []).append((title, emoji))
    return rows


def _normalise_title(title: str) -> str:
    """Strip TNNNN: prefix for comparison."""
    return re.sub(r"^T\d+:\s*", "", title).strip()


def _check_index_row_accuracy(tickets: list[Ticket], index_rows: dict[str, list[tuple[str, str]]], findings: list[Finding]) -> None:
    # Build a set of ticket IDs that appear more than once
    ticket_id_counts: dict[str, int] = {}
    for t in tickets:
        ticket_id_counts[t.ticket_id] = ticket_id_counts.get(t.ticket_id, 0) + 1

    for ticket in tickets:
        tid = ticket.ticket_id
        if tid not in index_rows:
            continue
        if ticket.frontmatter.get("index_exempt"):
            continue
        # Epic files drive headers, not table rows
        if re.match(r"^EM-E\d+$", tid):
            continue

        fm_status = ticket.frontmatter.get("status", "").upper()
        expected_emoji = _STATUS_TO_EMOJI.get(fm_status)
        fm_title = ticket.frontmatter.get("title", "")
        fm_title_clean = _normalise_title(fm_title)

        # If there are duplicate IDs in the ticket set, allow ANY matching index row
        idx_entries = index_rows[tid]
        status_matches = False
        title_matches = False

        for idx_title, idx_emoji in idx_entries:
            idx_title_clean = _normalise_title(idx_title)
            if expected_emoji and idx_emoji == expected_emoji:
                status_matches = True
            if fm_title_clean and idx_title_clean == fm_title_clean:
                title_matches = True

        # Only warn if NO index row matches (handles duplicate IDs gracefully)
        if expected_emoji and not status_matches:
            emojis = ", ".join(e for _, e in idx_entries)
            findings.append(
                Finding("WARNING", tid, f"index status emoji(s) [{emojis}] do not match frontmatter status '{fm_status}' (expected '{expected_emoji}')", "index_accuracy")
            )

        if fm_title_clean and not title_matches:
            titles = " | ".join(t for t, _ in idx_entries)
            findings.append(
                Finding("WARNING", tid, f"index title(s) '{titles}' do not match frontmatter title '{fm_title_clean}'", "index_accuracy")
            )


def _collect_doc_ids() -> set[str]:
    ids: set[str] = set()
    docs_dir = Path("docs")
    if not docs_dir.exists():
        return ids
    for path in docs_dir.glob("EM-*.md"):
        text = path.read_text(encoding="utf-8")
        fm, _err = _parse_frontmatter(text)
        if fm:
            tid = fm.get("id", "")
            if tid:
                ids.add(tid)
    return ids


def _check_index_orphans(index_refs: dict[str, list[int]], ticket_ids: set[str], findings: list[Finding]) -> None:
    doc_ids = _collect_doc_ids()
    for tid, lines in index_refs.items():
        if tid not in ticket_ids and tid not in doc_ids and tid.startswith("EM-"):
            findings.append(
                Finding("ERROR", tid, f"referenced in {_relative(INDEX_FILE)} line(s) {lines} but no ticket file exists", "index_orphan")
            )


def _check_duplicate_ids(tickets: list[Ticket], findings: list[Finding]) -> None:
    id_to_paths: dict[str, list[str]] = {}
    id_has_open: dict[str, bool] = {}
    for ticket in tickets:
        tid = ticket.ticket_id
        if not tid:
            continue
        id_to_paths.setdefault(tid, []).append(_relative(ticket.path))
        if ticket.frontmatter.get("status", "").upper() == "OPEN":
            id_has_open[tid] = True
    for tid, paths in id_to_paths.items():
        if len(paths) > 1 and id_has_open.get(tid, False):
            findings.append(
                Finding("ERROR", tid, f"duplicate id appears in {len(paths)} files: {', '.join(paths)}", "duplicate_id")
            )


def _finding_key(f: Finding) -> str:
    return f"{f.level}:{f.ticket_id}:{f.message}"


def _load_baseline() -> set[str]:
    if not BASELINE_FILE.exists():
        return set()
    data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    return set(data.get("findings", []))


def _save_baseline(findings: list[Finding]) -> None:
    keys = sorted({_finding_key(f) for f in findings})
    BASELINE_FILE.write_text(
        json.dumps({"findings": keys, "count": len(keys)}, indent=2) + "\n",
        encoding="utf-8",
    )


def _print_findings_text(findings: list[Finding]) -> None:
    errors = [f for f in findings if f.level == "ERROR"]
    warnings = [f for f in findings if f.level == "WARNING"]

    if warnings:
        print(f"--- WARNINGS ({len(warnings)}) ---")
        for f in warnings:
            print(f"  [{f.ticket_id}] [{f.check}] {f.message}")
        print("")

    if errors:
        print(f"--- ERRORS ({len(errors)}) ---")
        for f in errors:
            print(f"  [{f.ticket_id}] [{f.check}] {f.message}")
        print("")
        print(f"Audit failed: {len(errors)} error(s), {len(warnings)} warning(s)")
        return

    if warnings:
        print(f"Audit passed with {len(warnings)} warning(s)")
    else:
        print("Audit passed — all clean")


def _print_findings_json(findings: list[Finding]) -> None:
    data = {
        "schema_version": "1.0",
        "findings": [
            {"level": f.level, "ticket_id": f.ticket_id, "check": f.check, "message": f.message}
            for f in findings
        ],
    }
    print(json.dumps(data, indent=2))


def _run_scan_source(json_output: bool) -> int:
    """Run source-code and ticket scanners and print findings."""
    project_root = Path.cwd()

    skip_dirs = {
        ".git",
        "venv",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".hypothesis",
        "node_modules",
    }

    def _keep(path: Path) -> bool:
        return not any(part in skip_dirs for part in path.parts)

    # Scan all Python files for PII leaks.
    py_files = [p for p in project_root.rglob("*.py") if _keep(p)]
    pii_findings = scan_pii_leaks(py_files)

    # v2 residue scan is only meaningful on the product source and tests.
    residue_files = [
        p for p in py_files
        if any(str(p).startswith(str(project_root / root)) for root in ("clean_paste_lite", "tests"))
    ]
    residue_findings = scan_v2_residue(residue_files)

    all_findings = pii_findings + residue_findings

    if json_output:
        print(json.dumps([{"level": f.level, "location": f.location, "message": f.message, "check": f.check} for f in all_findings], indent=2))
    else:
        print("=== Source Scan ===")
        print("")
        print(
            f"Found {len(all_findings)} source finding(s) "
            f"({len(pii_findings)} PII, {len(residue_findings)} v2 residue)"
        )
        print("")
        for f in all_findings:
            print(f"  [{f.level}] {f.location} — {f.check}: {f.message}")
        print("")

    if any(f.level == "ERROR" for f in pii_findings + residue_findings):
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ticket hygiene scanner")
    parser.add_argument("--write-baseline", action="store_true", help="Write current findings to baseline")
    parser.add_argument("--check-baseline", action="store_true", help="Only report findings not in baseline")
    parser.add_argument("--closed-checks", action="store_true", help="Also check CLOSED tickets for stale cross-references")
    parser.add_argument("--docs-drift", action="store_true", help="Check open tickets for possible contradictions with AGENTS.md locked architecture")
    parser.add_argument("--classify", action="store_true", help="Run zombie/legitimacy classifier on open tickets")
    parser.add_argument("--scan-source", action="store_true", help="Run AST PII leak, deprecated-term registry, and v2 residue scans")
    parser.add_argument("--json", action="store_true", help="Output findings/classification as JSON")
    args = parser.parse_args()

    tickets = _collect_tickets()
    epic_dirs = _collect_epic_dirs()

    if args.classify:
        from classifier import classify_tickets
        results = classify_tickets(tickets)
        if args.json:
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
        else:
            print("=== Ticket Classification ===")
            print("")
            print(f"Found {len(tickets)} ticket files, {len(epic_dirs)} epics")
            print("")
            by_cat: dict[str, list] = {}
            for c in results:
                by_cat.setdefault(c.category, []).append(c)
            for cat in ["4 — Confirmed Zombie / Structural Defect", "3 — Likely Zombie", "2 — Stale / Needs Review", "1 — Active / Legitimate"]:
                items = by_cat.get(cat, [])
                print(f"{cat}: {len(items)}")
                for c in sorted(items, key=lambda x: x.ticket_id):
                    print(f"  {c.ticket_id:<20} {c.subcategory:<30} {c.rationale}")
        return 0

    if args.scan_source:
        return _run_scan_source(args.json)

    if not args.json:
        print("=== Ticket Hygiene Audit ===")
        print("")

    index_refs = _collect_index_refs()
    ticket_ids = {t.ticket_id for t in tickets}

    if not args.json:
        print(f"Found {len(tickets)} ticket files, {len(epic_dirs)} epics")
        print("")

    findings: list[Finding] = []

    valid_ids = ticket_ids | set(epic_dirs.keys())
    for ticket in tickets:
        _check_yaml_valid(ticket, findings)
        _check_required_fields(ticket, findings)
        _check_allowed_values(ticket, findings)
        _check_closed_field(ticket, findings)
        _check_status_location(ticket, findings)
        _check_epic(ticket, epic_dirs, findings)
        _check_index(ticket, index_refs, findings)
        _check_filename(ticket, findings)
        _check_retro(ticket, findings)
        _check_retro_in(ticket, valid_ids, findings)

    _check_cross_references(tickets, epic_dirs, findings)
    _check_dependency_sanity(tickets, findings)
    _check_markdown_links(tickets, findings)
    if args.closed_checks:
        _check_closed_cross_references(tickets, epic_dirs, findings)
    if args.docs_drift:
        _check_docs_drift(tickets, findings)
    _check_index_orphans(index_refs, ticket_ids, findings)
    _check_duplicate_ids(tickets, findings)

    index_rows = _parse_index_rows()
    _check_index_row_accuracy(tickets, index_rows, findings)

    if args.write_baseline:
        _save_baseline(findings)
        print(f"Baseline written to {BASELINE_FILE}: {len(findings)} finding(s)")
        return 0

    baseline: set[str] = set()
    if args.check_baseline:
        baseline = _load_baseline()
        if baseline:
            print(f"Loaded baseline: {len(baseline)} known finding(s)")
            print("")

    # Filter by baseline if requested
    if baseline:
        new_findings = [f for f in findings if _finding_key(f) not in baseline]
        if new_findings:
            print(f"--- NEW FINDINGS ({len(new_findings)} of {len(findings)} total) ---")
            for f in new_findings:
                print(f"  [{f.ticket_id}] [{f.check}] {f.message}")
            print("")
        findings = new_findings

    if args.json:
        _print_findings_json(findings)
    else:
        _print_findings_text(findings)

    return 1 if any(f.level == "ERROR" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())

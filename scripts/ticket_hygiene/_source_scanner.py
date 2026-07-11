"""Source-code and ticket scanners for the hygiene audit.

- PII leak detection in logging calls (AST-based).
- v2 architectural residue detection in new/modified files.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    level: str  # "ERROR" | "WARNING"
    location: str
    message: str
    check: str


# Names / suffixes that indicate a logging argument may carry PII.
_PII_NAME_FRAGMENTS = frozenset(
    {
        "matched_text",
        "original",
        "source_text",
        "token_views",
        "registry",
        "session_state",
        "snapshot",
        "merge_request",
    }
)

# Logging methods we care about.
_LOGGING_METHODS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})

# Directories that are scanned but only produce warnings for PII leaks.
_WARNING_ONLY_DIRS = frozenset({"scripts", "tests", "docs"})

# Directories to skip when indexing source symbols or scanning source.
_SKIP_SOURCE_DIRS = frozenset(
    {
        ".git",
        "venv",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".hypothesis",
        "node_modules",
        ".tox",
        "build",
        "dist",
    }
)

# Forbidden v2 / architectural residue patterns.
_V2_RESIDUE_PATTERNS: list[tuple[str, str]] = [
    (r"\bTextSegment\b", "TextSegment is v2 residue; use Segment from v3 Document model"),
    (r"\bAttributeScrubRule\b", "AttributeScrubRule superseded by EM-1596-A/B"),
    (r"\bDocumentParser\b", "DocumentParser was deleted in EM-0179"),
    (r"\bhtml_reinjector\b", "html_reinjector.py was deleted in EM-1596-PRE"),
]


def _is_warning_only(path: Path) -> bool:
    """Return True if the path lives under a warning-only directory."""
    parts = set(path.parts)
    return not parts.isdisjoint(_WARNING_ONLY_DIRS)


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_SOURCE_DIRS


def _collect_name_ids(node: ast.AST) -> set[str]:
    """Collect all Name/Attribute ids used in an AST subtree."""
    ids: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            ids.add(child.id)
        elif isinstance(child, ast.Attribute):
            ids.add(child.attr)
            # Also include the dotted path as a single string for suffix matching.
            value = child.value
            parts = [child.attr]
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                parts.append(value.id)
            ids.add(".".join(reversed(parts)))
        elif isinstance(child, ast.JoinedStr):
            for value in child.values:
                if isinstance(value, ast.FormattedValue):
                    ids.update(_collect_name_ids(value.value))
    return ids


def _looks_like_logger_call(node: ast.Call) -> bool:
    """Best-effort detection of logging calls."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in _LOGGING_METHODS:
        return True
    if isinstance(func, ast.Name) and func.id in _LOGGING_METHODS:
        return True
    return False


def scan_pii_leaks(paths: Iterable[Path]) -> list[Finding]:
    """Scan Python files for potential PII leaks in logging calls."""
    findings: list[Finding] = []
    for path in paths:
        if path.suffix != ".py":
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            findings.append(Finding("ERROR", str(path), f"syntax error: {exc}", "pii-scan"))
            continue

        warning_only = _is_warning_only(path)
        level = "WARNING" if warning_only else "ERROR"

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _looks_like_logger_call(node):
                continue
            for arg in node.args:
                ids = _collect_name_ids(arg)
                for full_id in ids:
                    for fragment in _PII_NAME_FRAGMENTS:
                        if re.search(rf"\b{re.escape(fragment)}\b", full_id):
                            findings.append(
                                Finding(
                                    level,
                                    str(path),
                                    f"potential PII leak in log call: '{full_id}' contains '{fragment}'",
                                    "pii-scan",
                                )
                            )
                            break
    return findings


def scan_v2_residue(paths: Iterable[Path]) -> list[Finding]:
    """Scan files for v2 architectural residue patterns."""
    findings: list[Finding] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern, message in _V2_RESIDUE_PATTERNS:
            for match in re.finditer(pattern, text):
                line_no = text[: match.start()].count("\n") + 1
                findings.append(
                    Finding(
                        "ERROR",
                        str(path),
                        f"{message} (line {line_no})",
                        "v2-residue-scan",
                    )
                )
    return findings

#!/usr/bin/env python3
"""Markdown link validator and repair tool for the tickets/ tree.

Detects stale relative links inside ticket files and, when possible, rewrites
them to point at the current epic directory / file name. The canonical epic
rename mapping is hard-coded here so the tool can repair historical links that
predate the current directory names.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TICKETS_DIR = Path("tickets")

# Markdown link matcher: ![text](path "title") or [text](path "title")
_LINK_RE = re.compile(
    r"!?\[(?P<text>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+(?P<title>\"[^\"]*\"|'[^']*'))?\)"
)

# Historical epic directory/file slugs -> current directory/file slugs.
# Ordered longest-first so shorter names do not partially overwrite longer ones.
_EPIC_SLUG_RENAMES: dict[str, str] = {
    "EM-E009-engine-architecture-auditability": "EM-E009-engine-architecture",
    "EM-E010-identity-session-adjacency": "EM-E010-entity-model-and-adjacency",
    "EM-E012-cross-pass-conflict-resolution": "EM-E012-conflict-resolution",
    "EM-E015-starter-content-onboarding": "EM-E015-onboarding-and-starter-packs",
    "EM-E017-foundation-unification": "EM-E017-document-model-and-domains",
    "EM-E018-adversarial-hardening": "EM-E018-security-and-adversarial",
    "EM-E019-session-architecture-rewrite": "EM-E019-session-layer",
    "EM-E021-scanner-hardening-ci-gate": "EM-E021-ci-and-dev-tooling",
    "EM-E022-indexed-replacement-modes": "EM-E022-output-formatting-modes",
    "EM-E001-lightweight-clipboard-scrubber": "EM-E001-pii-detection-core",
    "EM-E002-awesome-cut": "EM-E002-product-surface-and-mcp",
    "EM-E003-post-rust-swift": "EM-E003-swift-macos-app",
    "EM-E005-rust-port": "EM-E005-rust-production-port",
    "EM-E007-csv-schema-walker": "EM-E007-gazetteer-and-detection-data",
    # Identity mappings for epics that were not renamed.
    "EM-E004-launch-readiness": "EM-E004-launch-readiness",
    "EM-E006-blog-posts": "EM-E006-developer-marketing",
    "EM-E008-native-swift-layer": "EM-E008-native-swift-layer",
    "EM-E011-performance-stability": "EM-E011-performance-stability",
    "EM-E013-commercialisation": "EM-E013-commercialisation",
    "EM-E014-distribution-packaging": "EM-E014-distribution-packaging",
    "EM-E020-process-and-ticket-hygiene": "EM-E020-process-and-ticket-hygiene",
    "EM-E023-domain-implementations": "EM-E023-domain-implementations",
}

# Stable ordering for deterministic iteration.
_RENAME_KEYS = sorted(_EPIC_SLUG_RENAMES, key=lambda s: (-len(s), s))

# Ticket-like filename: EM-XXXX...md
_TICKET_FILE_RE = re.compile(r"^(EM-(?:E\d+|\d+[A-Z]*(?:-[A-Z0-9.]+)*)).*\.md$")


@dataclass(frozen=True)
class Link:
    lineno: int
    raw: str
    path: str
    anchor: str
    text: str


@dataclass(frozen=True)
class Repair:
    ticket_path: Path
    lineno: int
    old: str
    new: str


def _split_anchor(path: str) -> tuple[str, str]:
    if "#" in path:
        base, anchor = path.split("#", 1)
        return base, f"#{anchor}"
    return path, ""


def _join_anchor(path: str, anchor: str) -> str:
    return f"{path}{anchor}"


def _is_external(path: str) -> bool:
    """Return True for URLs, mailto, etc."""
    lower = path.lower()
    if lower.startswith(("http://", "https://", "mailto:", "tel:", "ftp://", "file://")):
        return True
    return False


def _looks_like_ticket_link(path: str) -> bool:
    """True if the link target looks like a ticket file path."""
    if _is_external(path):
        return False
    base, _ = _split_anchor(path)
    if not base:
        return False
    # Explicit ticket paths contain EM- after a slash or at the start.
    if re.search(r"(^|/)EM-(?:E\d+|\d+[A-Z]*)", base):
        return True
    # Bare ticket filename in same directory.
    if _TICKET_FILE_RE.match(Path(base).name):
        return True
    return False


def _resolve(source: Path, path: str) -> Path | None:
    """Resolve a relative link from a source file; returns None if non-existent."""
    base, _ = _split_anchor(path)
    if not base or base.startswith("/"):
        return None
    target = (source.parent / base).resolve()
    if not target.exists():
        return None
    if target.is_file():
        return target
    # Directory links are valid when the link explicitly points at a directory.
    if target.is_dir() and base.endswith("/"):
        return target
    return None


def _canonical_id(ticket_id: str) -> str:
    """Strip common spike / exploratory suffixes so IDs line up across renames."""
    return re.sub(r"\.[xs]$", "", ticket_id)


@dataclass
class TicketIndex:
    exact: dict[str, Path]
    by_id: dict[str, Path]
    by_canonical_id: dict[str, Path]
    by_stem: dict[str, Path]
    by_canonical_stem: dict[str, Path]
    all_paths: list[Path]


def _build_ticket_index(tickets_dir: Path) -> TicketIndex:
    """Index ticket files by filename, ticket ID, and stem for fuzzy repairs."""
    exact: dict[str, Path] = {}
    by_id: dict[str, Path] = {}
    by_canonical_id: dict[str, Path] = {}
    by_stem: dict[str, Path] = {}
    by_canonical_stem: dict[str, Path] = {}
    all_paths: list[Path] = []

    for path in tickets_dir.rglob("*.md"):
        if path.name == "TICKET_INDEX.md":
            continue
        all_paths.append(path)

        # Exact filename index — prefer the shorter path on collisions.
        existing = exact.get(path.name)
        if existing is None or len(path.parts) < len(existing.parts):
            exact[path.name] = path

        m = _TICKET_FILE_RE.match(path.name)
        if not m:
            continue
        ticket_id = m.group(1)
        stem = path.stem
        canonical_id = _canonical_id(ticket_id)
        canonical_stem = _canonical_id(stem)

        for store, key in (
            (by_id, ticket_id),
            (by_canonical_id, canonical_id),
            (by_stem, stem),
            (by_canonical_stem, canonical_stem),
        ):
            old = store.get(key)
            if old is None or len(path.parts) < len(old.parts):
                store[key] = path

    return TicketIndex(
        exact=exact,
        by_id=by_id,
        by_canonical_id=by_canonical_id,
        by_stem=by_stem,
        by_canonical_stem=by_canonical_stem,
        all_paths=all_paths,
    )


def _extract_id_from_filename(filename: str) -> str | None:
    m = _TICKET_FILE_RE.match(filename)
    return m.group(1) if m else None


def _source_context(path: Path) -> tuple[str, str]:
    """Return (epic_dir_name, status_folder) for a ticket path, if present."""
    try:
        epic = path.parts[1] if len(path.parts) > 1 else ""
        status = path.parts[2] if len(path.parts) > 2 else ""
    except IndexError:
        return "", ""
    return epic, status


def _pick_best_candidate(
    source: Path,
    candidates: list[Path],
    link_stem: str,
    link_ticket_id: str | None,
) -> Path:
    """Choose the candidate most likely to be the intended target.

    Preference order:
    1. Same epic directory as source.
    2. Same open/closed status folder as source.
    3. Filename similarity (exact, prefix, substring, ID prefix).
    4. Shortest path, then shortest name.
    """
    src_epic, src_status = _source_context(source)
    link_stem_lower = link_stem.lower()

    def _relation_rank(p: Path) -> int:
        cand_stem_lower = p.stem.lower()
        if cand_stem_lower == link_stem_lower:
            return 0
        if cand_stem_lower.startswith(link_stem_lower):
            return 1
        if link_stem_lower in cand_stem_lower:
            return 2
        if link_ticket_id:
            pid = _extract_id_from_filename(p.name)
            if pid and (pid == link_ticket_id or pid.startswith(f"{link_ticket_id}-")):
                return 3
        return 4

    def score(p: Path) -> tuple[int, ...]:
        epic, status = _source_context(p)
        same_epic = epic == src_epic
        same_status = status == src_status
        return (
            -int(same_epic),
            -int(same_status),
            _relation_rank(p),
            len(p.parts),
            len(p.name),
        )

    return min(candidates, key=score)


def _all_candidates(filename: str, index: TicketIndex, source: Path) -> list[Path]:
    """Return every ticket file that could be the intended target of `filename`.

    Excludes the source file itself so a broken link to a sibling ticket is not
    rewritten into a self-link.
    """
    if not filename:
        return []

    candidates: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        if p != source and p not in seen:
            seen.add(p)
            candidates.append(p)

    # Exact filename.
    if filename in index.exact:
        add(index.exact[filename])

    # Ticket ID match (canonical and full).
    ticket_id = _extract_id_from_filename(filename)
    if ticket_id:
        canonical = _canonical_id(ticket_id)
        for p in index.all_paths:
            pid = _extract_id_from_filename(p.name)
            if pid and _canonical_id(pid) == canonical:
                add(p)
            elif pid == ticket_id:
                add(p)

    # Stem match (canonical and full).
    stem = Path(filename).stem
    canonical_stem = _canonical_id(stem)
    for p in index.all_paths:
        p_canonical_stem = _canonical_id(p.stem)
        if p_canonical_stem == canonical_stem or p.stem == stem:
            add(p)

    # Prefix fallback: EM-XXXX is the start of a longer compound ticket ID.
    if ticket_id:
        for p in index.all_paths:
            pid = _extract_id_from_filename(p.name)
            if pid and (pid == ticket_id or pid.startswith(f"{ticket_id}-")):
                add(p)

    # Substring fallback: the link's stem appears inside another filename.
    for p in index.all_paths:
        if stem in p.name:
            add(p)

    return candidates


def _best_candidate(
    source: Path,
    candidates: list[Path],
    link_stem: str,
    link_ticket_id: str | None,
) -> Path | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return _pick_best_candidate(source, candidates, link_stem, link_ticket_id)


def _repair_path(source: Path, path: str, index: TicketIndex) -> str | None:
    """Attempt to repair a stale ticket link. Returns the new path or None."""
    if not _looks_like_ticket_link(path):
        return None

    base, anchor = _split_anchor(path)

    # Already valid (file or explicit directory).
    if _resolve(source, path):
        return None

    # 1. Apply epic slug renames.
    renamed = base
    for old in _RENAME_KEYS:
        renamed = renamed.replace(old, _EPIC_SLUG_RENAMES[old])
    if renamed != base and _resolve(source, _join_anchor(renamed, anchor)):
        return _join_anchor(renamed, anchor)

    # 2. Directory link to an epic directory.
    if renamed.endswith("/"):
        target = (source.parent / renamed).resolve()
        if target.exists() and target.is_dir():
            return None  # valid after rename

    # 3. Fuzzy file lookup.
    filename = Path(renamed).name
    link_stem = Path(filename).stem
    link_ticket_id = _extract_id_from_filename(filename)
    candidates = _all_candidates(filename, index, source)
    if not candidates and filename != Path(base).name:
        filename = Path(base).name
        link_stem = Path(filename).stem
        link_ticket_id = _extract_id_from_filename(filename)
        candidates = _all_candidates(filename, index, source)

    actual = _best_candidate(source, candidates, link_stem, link_ticket_id)
    if actual is not None:
        new_path = _join_anchor(_relative(source.parent, actual), anchor)
        if _resolve(source, new_path):
            return new_path

    return None


def _relative(from_dir: Path, to_file: Path) -> str:
    """Return a POSIX relative path from from_dir to to_file."""
    import os

    return os.path.relpath(to_file, from_dir).replace("\\", "/")


def find_links(text: str) -> list[Link]:
    """Extract ticket-looking markdown links from text."""
    links: list[Link] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in _LINK_RE.finditer(line):
            raw_path = m.group("path")
            if not _looks_like_ticket_link(raw_path):
                continue
            base, anchor = _split_anchor(raw_path)
            links.append(
                Link(
                    lineno=lineno,
                    raw=m.group(0),
                    path=base,
                    anchor=anchor,
                    text=m.group("text"),
                )
            )
    return links


def repair_file(
    path: Path,
    index: TicketIndex,
    dry_run: bool = True,
) -> list[Repair]:
    """Repair links in a single ticket file. Returns list of repairs made/needed."""
    text = path.read_text(encoding="utf-8")
    repairs: list[Repair] = []
    new_lines: list[str] = []
    changed = False

    for lineno, line in enumerate(text.splitlines(), start=1):
        new_line = line
        # Process matches right-to-left so replacements do not shift indices.
        matches = list(_LINK_RE.finditer(line))
        for m in reversed(matches):
            raw_path = m.group("path")
            if not _looks_like_ticket_link(raw_path):
                continue
            fixed = _repair_path(path, raw_path, index)
            if fixed is not None and fixed != raw_path:
                start, end = m.start(), m.end()
                new_link = f"[{m.group('text')}]({fixed})"
                new_line = new_line[:start] + new_link + new_line[end:]
                repairs.append(Repair(ticket_path=path, lineno=lineno, old=raw_path, new=fixed))
                changed = True
        new_lines.append(new_line)

    if changed and not dry_run:
        path.write_text("\n".join(new_lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")

    return repairs


def repair_all(
    tickets_dir: Path = TICKETS_DIR,
    dry_run: bool = True,
) -> list[Repair]:
    """Repair links across the whole tickets tree."""
    index = _build_ticket_index(tickets_dir)
    all_repairs: list[Repair] = []
    for path in sorted(tickets_dir.rglob("*.md")):
        if path.name == "TICKET_INDEX.md":
            continue
        all_repairs.extend(repair_file(path, index, dry_run=dry_run))
    return all_repairs


def check_links(tickets: Iterable[Any], tickets_dir: Path = TICKETS_DIR) -> list[Any]:
    """Return Finding-like objects for broken ticket links.

    This function uses duck typing so it can be imported by audit_tickets.py
    without creating a circular dependency on the Finding dataclass.
    """
    from audit_tickets import Finding

    _build_ticket_index(tickets_dir)
    findings: list[Finding] = []
    seen: set[tuple[Path, str]] = set()

    for ticket in tickets:
        path = ticket.path
        text = path.read_text(encoding="utf-8")
        for link in find_links(text):
            key = (path, link.raw)
            if key in seen:
                continue
            seen.add(key)
            full_path = _join_anchor(link.path, link.anchor)
            if _resolve(path, full_path):
                continue
            # Flag as WARNING; many stale links are historical and already broken.
            findings.append(
                Finding(
                    level="WARNING",
                    ticket_id=ticket.ticket_id,
                    message=f"broken markdown link to '{full_path}' (line {link.lineno})",
                    check="markdown_link",
                )
            )
    return findings


def _main() -> int:
    parser = argparse.ArgumentParser(description="Repair ticket markdown links")
    parser.add_argument("--tickets-dir", type=Path, default=TICKETS_DIR)
    parser.add_argument("--write", action="store_true", help="Apply repairs")
    parser.add_argument("--json", action="store_true", help="Output repairs as JSON")
    args = parser.parse_args()

    repairs = repair_all(tickets_dir=args.tickets_dir, dry_run=not args.write)

    if args.json:
        import json

        data = [
            {
                "file": str(r.ticket_path),
                "lineno": r.lineno,
                "old": r.old,
                "new": r.new,
            }
            for r in repairs
        ]
        print(json.dumps(data, indent=2))
    else:
        print(f"Found {len(repairs)} repair(s)")
        for r in repairs:
            print(f"  {r.ticket_path}:{r.lineno} {r.old!r} -> {r.new!r}")

    return 0 if args.write or not repairs else 1


if __name__ == "__main__":
    sys.exit(_main())

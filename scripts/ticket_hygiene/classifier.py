#!/usr/bin/env python3
"""Zombie / legitimacy classifier for open tickets.

Heuristic classification based on the methodology used in EM-1640 Phase A.
Rules are loaded from classifier_rules.yaml by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from show_path import _build_graph, _critical_path

DEFAULT_RULES_PATH = Path(__file__).with_name("classifier_rules.yaml")

# Embedded defaults so the classifier still works if the YAML file is missing.
_DEFAULT_RULES: dict[str, Any] = {
    "schema_version": "1.0",
    "deleted_components": [
        "__main__.py",
        "replacement_map.py",
        "document_parser.py",
        "html_reinjector.py",
        "engine_v2.py",
        "session_map.py",
        "MatchCandidate",
        "Change",
        "ReplacementSpec",
        "to_document()",
        "scrub_json()",
        "scrub_csv()",
        "scrub_html()",
        "scrub_xml()",
        "scrub_markdown()",
    ],
    "placeholder_words": ["spike", "followup", "follow-up", "placeholder", "decide fate", "tbd", "wip", "stub"],
    "drift_verbs": ["change", "revisit", "modify", "override", "replace", "delete", "remove", "rethink", "reconsider", "relax", "bend", "break"],
    "locked_phrases": [
        "Pipeline.run_domain",
        "tree-first",
        "BaseDomain",
        "Address Protocol",
        "CoordinateSpace",
        "ScrubOperation",
        "ResolvedOperation",
        "Metadata",
        "Receipt",
        "merkle_root",
        "Document + Session",
        "DocumentEditor",
        "Session is never cached",
        "Engine is frozen",
        "Token Format",
        "CPL_ID",
        "Obfuscation Taxonomy",
        "Reversible modes",
        "Incremental obfuscation",
        "Absolute obfuscation",
    ],
}


@dataclass
class Classification:
    ticket_id: str
    category: str
    subcategory: str
    rationale: str
    signals: list[str] = field(default_factory=list)


def _load_rules(path: Path | str | None = None) -> dict[str, Any]:
    """Load classifier rules from YAML, falling back to embedded defaults."""
    if path is None:
        path = DEFAULT_RULES_PATH
    p = Path(path)
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return _DEFAULT_RULES.copy()


def _build_placeholder_regex(words: list[str]) -> re.Pattern[str]:
    escaped = [re.escape(w) for w in words]
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


def _body_text(text: str) -> str:
    """Return body text after the YAML frontmatter, or full text if no frontmatter.

    Only the first `---` ... `---` block is treated as frontmatter. Subsequent
    `---` lines (e.g. horizontal rules inside the ticket body) must not be
    mistaken for the frontmatter boundary.
    """
    if not text.startswith("---"):
        return text
    match = re.search(r"^---\n(.*?)\n-{3,}\n(.*)", text, re.DOTALL)
    if not match:
        return text
    return match.group(2)


def _has_acceptance_criteria(body: str) -> bool:
    return bool(re.search(r"^\s*-\s*\[\s*[ x]\s*\]", body, re.MULTILINE))


def _deleted_components_in(text: str, components: list[str]) -> list[str]:
    return [comp for comp in components if comp in text]


def _placeholder_words(text: str, pattern: re.Pattern[str]) -> list[str]:
    return sorted(set(pattern.findall(text)))


def _normalize_dep(dep: Any) -> list[str]:
    if dep is None:
        return []
    if isinstance(dep, str):
        return [dep]
    if isinstance(dep, list):
        return [str(d) for d in dep]
    return []


def _status_location_mismatch(ticket: Any) -> bool:
    status = ticket.frontmatter.get("status", "").upper()
    path_str = str(ticket.path).replace("\\", "/")
    in_closed = "/closed/" in path_str
    in_open = "/open/" in path_str
    if status == "CLOSED" and in_open:
        return True
    if status == "OPEN" and in_closed:
        return True
    if in_closed and status not in {"CLOSED", "WONT_FIX"}:
        return True
    return False


def _is_epic_root_id(ticket_id: str) -> bool:
    return bool(re.match(r"^EM-E\d+$", ticket_id))


@dataclass
class DocsDriftFinding:
    ticket_id: str
    phrases: list[str]
    paragraph: str


def docs_drift_findings(
    tickets: list[Any],
    rules_path: Path | str | None = None,
) -> list[DocsDriftFinding]:
    """Find open tickets whose paragraphs mention locked architecture + a drift verb."""
    rules = _load_rules(rules_path)
    locked_phrases = rules.get("locked_phrases", _DEFAULT_RULES["locked_phrases"])
    drift_verbs = rules.get("drift_verbs", _DEFAULT_RULES["drift_verbs"])
    verb_re = _build_placeholder_regex(drift_verbs)

    findings: list[DocsDriftFinding] = []
    for t in tickets:
        if t.frontmatter.get("status", "").upper() != "OPEN":
            continue
        text = t.path.read_text(encoding="utf-8")
        body = _body_text(text)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        for para in paragraphs:
            para_lower = para.lower()
            matched = [ph for ph in locked_phrases if ph.lower() in para_lower]
            if not matched:
                continue
            if verb_re.search(para):
                findings.append(DocsDriftFinding(
                    ticket_id=t.ticket_id,
                    phrases=matched,
                    paragraph=para[:200].replace("\n", " "),
                ))
                break
    return findings


def classify_tickets(
    tickets: list[Any],
    top_critical_path_n: int = 10,
    rules_path: Path | str | None = None,
) -> list[Classification]:
    """Classify open tickets into active/stale/likely-zombie/confirmed-zombie buckets."""
    rules = _load_rules(rules_path)
    deleted_components = rules.get("deleted_components", _DEFAULT_RULES["deleted_components"])
    placeholder_words = rules.get("placeholder_words", _DEFAULT_RULES["placeholder_words"])
    placeholder_re = _build_placeholder_regex(placeholder_words)

    ticket_map = {t.ticket_id: t for t in tickets}
    valid_ids = set(ticket_map.keys())
    epic_ids = {t.frontmatter.get("epic", "") for t in tickets if t.frontmatter.get("epic", "").startswith("EM-")}
    valid_refs = valid_ids | epic_ids

    id_counts: dict[str, int] = {}
    id_has_open: dict[str, bool] = {}
    for t in tickets:
        tid = t.ticket_id
        if not tid:
            continue
        id_counts[tid] = id_counts.get(tid, 0) + 1
        if t.frontmatter.get("status", "").upper() == "OPEN":
            id_has_open[tid] = True
    open_duplicate_ids = {tid for tid, count in id_counts.items() if count > 1 and id_has_open.get(tid, False)}

    graph = _build_graph(tickets)
    open_tids = [tid for tid, node in graph.items() if node["status"] == "OPEN"]
    critical_depths = {tid: _critical_path(graph, tid) for tid in open_tids}
    sorted_depths = sorted(critical_depths.items(), key=lambda x: (-x[1], x[0]))
    top_critical_ids = {tid for tid, _ in sorted_depths[:top_critical_path_n]}

    results: list[Classification] = []
    for t in tickets:
        status = t.frontmatter.get("status", "").upper()
        if status != "OPEN":
            continue

        tid = t.ticket_id
        text = t.path.read_text(encoding="utf-8")
        body = _body_text(text)
        title = t.frontmatter.get("title", "")
        depends_on = _normalize_dep(t.frontmatter.get("depends_on"))
        blocks = _normalize_dep(t.frontmatter.get("blocks"))

        signals: dict[str, Any] = {}

        if tid in open_duplicate_ids:
            signals["duplicate_id"] = True
        if _status_location_mismatch(t):
            signals["status_location_mismatch"] = True

        missing_deps = [d for d in depends_on + blocks if d and d not in valid_refs]
        if missing_deps:
            signals["missing_deps"] = missing_deps

        wontfix_deps = [
            d for d in depends_on
            if d in ticket_map and ticket_map[d].frontmatter.get("status", "").upper() == "WONT_FIX"
        ]
        if wontfix_deps:
            signals["wontfix_deps"] = wontfix_deps

        open_deps = [
            d for d in depends_on
            if d in ticket_map and ticket_map[d].frontmatter.get("status", "").upper() == "OPEN"
        ]
        if open_deps:
            signals["open_deps"] = open_deps

        known_closed_deps = [
            d for d in depends_on
            if d in ticket_map and ticket_map[d].frontmatter.get("status", "").upper() == "CLOSED"
        ]
        known_deps = [d for d in depends_on if d in ticket_map]
        all_known_deps_closed = bool(known_deps) and len(known_closed_deps) == len(known_deps)
        if all_known_deps_closed:
            signals["all_deps_closed"] = True

        has_ac = _has_acceptance_criteria(body)
        if not has_ac:
            signals["missing_ac"] = True

        deleted = _deleted_components_in(text, deleted_components)
        if deleted:
            signals["deleted_components"] = deleted

        placeholders = _placeholder_words(title + "\n" + body, placeholder_re)
        if placeholders:
            signals["placeholder_words"] = placeholders

        if tid in top_critical_ids and critical_depths.get(tid, 0) > 0:
            signals["on_critical_path"] = critical_depths[tid]

        if _is_epic_root_id(tid):
            signals["epic_root"] = True
        if "audit" in title.lower() or "hygiene" in title.lower():
            signals["audit_ticket"] = True

        # Categorise (conservative: escalate upward)
        if signals.get("epic_root") or signals.get("audit_ticket"):
            category = "1 — Active / Legitimate"
            if signals.get("epic_root"):
                subcategory = "epic-root"
                rationale = "epic summary ticket"
            else:
                subcategory = "audit-ticket"
                rationale = "this ticket is the active hygiene audit"
        elif signals.get("duplicate_id") or signals.get("status_location_mismatch") or (
            signals.get("missing_deps") and signals.get("missing_ac")
        ):
            category = "4 — Confirmed Zombie / Structural Defect"
            if signals.get("duplicate_id"):
                subcategory = "duplicate-id-requires-renumber"
                rationale = f"ID {tid} appears in {id_counts[tid]} files"
            elif signals.get("status_location_mismatch"):
                subcategory = "status-location-mismatch"
                rationale = "status does not match open/closed directory"
            else:
                subcategory = "no-viable-path"
                rationale = f"missing dependencies {missing_deps} and no acceptance criteria"
        elif signals.get("wontfix_deps") or (
            signals.get("placeholder_words") and signals.get("missing_ac")
        ):
            category = "3 — Likely Zombie"
            if signals.get("wontfix_deps"):
                subcategory = "blocked-by-wontfix"
                rationale = f"depends on WONT_FIX: {wontfix_deps}"
            else:
                subcategory = "placeholder-without-ac"
                rationale = f"placeholder ({placeholders}) with no acceptance criteria"
        elif (
            signals.get("deleted_components")
            or signals.get("missing_ac")
            or signals.get("placeholder_words")
            or signals.get("missing_deps")
        ):
            category = "2 — Stale / Needs Review"
            if signals.get("deleted_components"):
                subcategory = "references-deleted-component"
                rationale = f"mentions deleted component(s): {', '.join(deleted)}"
            elif signals.get("missing_ac"):
                subcategory = "missing-acceptance-criteria"
                rationale = "no acceptance-criteria checkboxes"
            elif signals.get("placeholder_words"):
                subcategory = "vague-scope-or-spike"
                rationale = f"placeholder scope ({placeholders})"
            else:
                subcategory = "missing-dependency"
                rationale = f"missing dependency {missing_deps}"
        else:
            category = "1 — Active / Legitimate"
            if signals.get("on_critical_path"):
                subcategory = "on-critical-path"
                rationale = f"critical-path depth {critical_depths[tid]}"
            elif signals.get("open_deps"):
                subcategory = "blocked-by-open-ticket"
                rationale = f"blocked by open: {open_deps}"
            elif signals.get("all_deps_closed"):
                subcategory = "ready-to-work"
                rationale = "all dependencies closed / no open blockers"
            else:
                subcategory = "ready-to-work"
                rationale = "no open blockers"

        results.append(
            Classification(
                ticket_id=tid,
                category=category,
                subcategory=subcategory,
                rationale=rationale,
                signals=sorted(str(k) for k in signals.keys()),
            )
        )

    return results

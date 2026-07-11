#!/usr/bin/env python3
"""Ticket dependency path analyzer — show what blocks what, and what paths lead to a feature.

Run from the project root:

    # Show the full dependency chain for a ticket
    python scripts/ticket_hygiene/show_path.py EM-0123-CP-9-ADJ

    # Show what's blocked by a ticket (transitive)
    python scripts/ticket_hygiene/show_path.py --blocked EM-1506

    # Show all OPEN tickets with no OPEN dependencies (ready to work)
    python scripts/ticket_hygiene/show_path.py --ready

    # Show all tickets in an epic with dependency chains
    python scripts/ticket_hygiene/show_path.py --epic EM-E010

    # Show the critical path (longest dependency chain)
    python scripts/ticket_hygiene/show_path.py --critical-path

The script parses `depends_on` and `blocks` from ticket frontmatter and builds
a directed acyclic graph. It then computes transitive closures to answer
"what must close before X?" and "what unlocks when X closes?"
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from audit_tickets import _collect_tickets


def _normalize_dep(dep: Any) -> list[str]:
    """Normalize a depends_on/blocks field to a list of ticket IDs."""
    if dep is None:
        return []
    if isinstance(dep, str):
        return [dep]
    if isinstance(dep, list):
        return [str(d) for d in dep]
    return []


def _check_duplicate_collisions(tickets: list) -> list[tuple[str, list[str]]]:
    """Return duplicate IDs where at least one involved ticket is OPEN."""
    id_to_paths: dict[str, list[str]] = {}
    id_has_open: dict[str, bool] = {}
    for t in tickets:
        tid = t.ticket_id
        if not tid:
            continue
        id_to_paths.setdefault(tid, []).append(str(t.path))
        if t.frontmatter.get("status", "").upper() == "OPEN":
            id_has_open[tid] = True
    return [(tid, paths) for tid, paths in id_to_paths.items() if len(paths) > 1 and id_has_open.get(tid, False)]


def _build_graph(tickets: list) -> dict[str, dict]:
    """Build dependency graph from tickets.

    Returns dict[ticket_id, {
        'ticket': Ticket object,
        'status': str,
        'title': str,
        'epic': str,
        'depends_on': list[str],
        'blocks': list[str],
        'path': Path,
    }]
    """
    graph: dict[str, dict] = {}
    for t in tickets:
        fm = t.frontmatter
        tid = t.ticket_id
        graph[tid] = {
            "ticket": t,
            "status": fm.get("status", "UNKNOWN").upper(),
            "title": fm.get("title", ""),
            "epic": fm.get("epic", ""),
            "priority": fm.get("priority"),
            "effort": fm.get("effort", ""),
            "depends_on": _normalize_dep(fm.get("depends_on")),
            "blocks": _normalize_dep(fm.get("blocks")),
            "path": t.path,
        }
    return graph


def _transitive_deps(graph: dict[str, dict], tid: str, visited: set[str] | None = None) -> set[str]:
    """Return all transitive dependencies of *tid* (what must close first)."""
    if visited is None:
        visited = set()
    if tid in visited:
        return set()
    visited.add(tid)
    deps: set[str] = set()
    for dep in graph.get(tid, {}).get("depends_on", []):
        if dep in graph:
            deps.add(dep)
            deps |= _transitive_deps(graph, dep, visited)
    return deps


def _transitive_blocked(graph: dict[str, dict], tid: str, visited: set[str] | None = None) -> set[str]:
    """Return all tickets transitively blocked by *tid* (what unlocks when this closes)."""
    if visited is None:
        visited = set()
    if tid in visited:
        return set()
    visited.add(tid)
    blocked: set[str] = set()
    for b in graph.get(tid, {}).get("blocks", []):
        if b in graph:
            blocked.add(b)
            blocked |= _transitive_blocked(graph, b, visited)
    return blocked


def _is_ready(graph: dict[str, dict], tid: str) -> bool:
    """True if *tid* is OPEN and all its dependencies are CLOSED."""
    node = graph.get(tid)
    if not node:
        return False
    if node["status"] != "OPEN":
        return False
    for dep in node.get("depends_on", []):
        dep_node = graph.get(dep)
        if dep_node and dep_node["status"] != "CLOSED":
            return False
    return True


def _critical_path(
    graph: dict[str, dict],
    tid: str,
    memo: dict[str, int] | None = None,
    visited: set[str] | None = None,
) -> int:
    """Return the length of the longest dependency chain leading to *tid*."""
    if memo is None:
        memo = {}
    if visited is None:
        visited = set()
    if tid in memo:
        return memo[tid]
    if tid in visited:
        return 0
    visited.add(tid)
    deps = graph.get(tid, {}).get("depends_on", [])
    if not deps:
        memo[tid] = 0
        return 0
    max_len = 0
    for dep in deps:
        if dep in graph:
            max_len = max(max_len, 1 + _critical_path(graph, dep, memo, visited))
    visited.discard(tid)
    memo[tid] = max_len
    return max_len


def _render_path(graph: dict[str, dict], tid: str, indent: int = 0, visited: set[str] | None = None) -> list[str]:
    """Render the dependency tree for *tid* as indented lines."""
    if visited is None:
        visited = set()
    lines: list[str] = []
    node = graph.get(tid)
    if not node:
        return lines

    status_emoji = {"OPEN": "⬜", "CLOSED": "✅", "STANDING": "🏛️", "WONT_FIX": "🚫"}.get(node["status"], "❓")
    prefix = "  " * indent
    lines.append(f"{prefix}{status_emoji} {tid} — {node['title']}")

    if tid in visited:
        lines.append(f"{prefix}    ↳ (cycle detected)")
        return lines
    visited.add(tid)

    for dep in node.get("depends_on", []):
        if dep in graph:
            lines.extend(_render_path(graph, dep, indent + 1, visited))
        else:
            lines.append(f"{prefix}  ❓ {dep} — (unknown ticket)")

    return lines


def _show_path(graph: dict[str, dict], tid: str) -> None:
    """Show the full dependency path to *tid*."""
    if tid not in graph:
        print(f"ERROR: Ticket '{tid}' not found.")
        sys.exit(1)

    print(f"Path to {tid}")
    print("=" * 60)
    lines = _render_path(graph, tid)
    for line in lines:
        print(line)

    deps = _transitive_deps(graph, tid)
    open_deps = [d for d in deps if graph.get(d, {}).get("status") == "OPEN"]
    closed_deps = [d for d in deps if graph.get(d, {}).get("status") == "CLOSED"]

    print()
    print(f"Total dependencies: {len(deps)}")
    print(f"  ✅ Closed: {len(closed_deps)}")
    print(f"  ⬜ Open:   {len(open_deps)}")
    if open_deps:
        print()
        print("Next tickets to close:")
        for d in sorted(open_deps):
            node = graph[d]
            print(f"  ⬜ {d} — {node['title']}")


def _show_blocked(graph: dict[str, dict], tid: str) -> None:
    """Show what's blocked by *tid* (transitive)."""
    if tid not in graph:
        print(f"ERROR: Ticket '{tid}' not found.")
        sys.exit(1)

    blocked = _transitive_blocked(graph, tid)
    if not blocked:
        print(f"{tid} does not block any tickets.")
        return

    print(f"Tickets blocked by {tid} (transitive)")
    print("=" * 60)
    for b in sorted(blocked):
        node = graph[b]
        status_emoji = {"OPEN": "⬜", "CLOSED": "✅", "STANDING": "🏛️", "WONT_FIX": "🚫"}.get(node["status"], "❓")
        print(f"  {status_emoji} {b} — {node['title']}")
    print()
    print(f"Total blocked: {len(blocked)}")


def _show_ready(graph: dict[str, dict]) -> None:
    """Show all OPEN tickets with no OPEN dependencies."""
    ready = [tid for tid in graph if _is_ready(graph, tid)]
    if not ready:
        print("No tickets are ready to work on.")
        return

    print("Tickets ready to work on (OPEN, all dependencies CLOSED)")
    print("=" * 60)

    # Group by epic
    by_epic: dict[str, list[str]] = defaultdict(list)
    for tid in ready:
        by_epic[graph[tid]["epic"]].append(tid)

    for epic in sorted(by_epic.keys()):
        print()
        print(f"  Epic: {epic}")
        for tid in sorted(by_epic[epic]):
            node = graph[tid]
            print(f"    ⬜ {tid} — {node['title']}")

    print()
    print(f"Total ready: {len(ready)}")


def _show_epic(graph: dict[str, dict], epic_id: str) -> None:
    """Show all tickets in an epic with dependency chains."""
    tickets = [tid for tid, node in graph.items() if node["epic"] == epic_id]
    if not tickets:
        print(f"No tickets found for epic '{epic_id}'.")
        return

    print(f"Epic: {epic_id}")
    print("=" * 60)

    # Find root tickets (no dependencies within the epic)
    epic_set = set(tickets)
    roots = [tid for tid in tickets if not any(d in epic_set for d in graph[tid]["depends_on"])]

    for root in sorted(roots):
        print()
        lines = _render_path(graph, root)
        for line in lines:
            print(line)

    print()
    print(f"Total tickets: {len(tickets)}")
    open_count = sum(1 for tid in tickets if graph[tid]["status"] == "OPEN")
    closed_count = sum(1 for tid in tickets if graph[tid]["status"] == "CLOSED")
    print(f"  ⬜ Open:   {open_count}")
    print(f"  ✅ Closed: {closed_count}")


def _detect_cycles(graph: dict[str, dict]) -> list[list[str]]:
    """Detect all dependency cycles in the graph using DFS."""
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str], visited: set[str]) -> None:
        for dep in graph.get(node, {}).get("depends_on", []):
            if dep not in graph:
                continue
            if dep in path:
                # Found cycle
                cycle_start = path.index(dep)
                cycle = path[cycle_start:] + [dep]
                # Normalize: rotate to start with smallest ID
                min_idx = cycle.index(min(cycle[:-1]))
                normalized = cycle[min_idx:-1] + cycle[:min_idx] + [cycle[min_idx]]
                if normalized not in cycles:
                    cycles.append(normalized)
                continue
            if dep in visited:
                continue
            visited.add(dep)
            dfs(dep, path + [dep], visited)

    visited: set[str] = set()
    for tid in graph:
        if tid not in visited:
            visited.add(tid)
            dfs(tid, [tid], visited)

    return cycles


def _show_cycles(graph: dict[str, dict]) -> None:
    """Show all dependency cycles in the graph."""
    cycles = _detect_cycles(graph)
    if not cycles:
        print("✅ No dependency cycles detected.")
        return

    print("🔄 Dependency Cycles Detected")
    print("=" * 60)
    for cycle in cycles:
        chain = " → ".join(cycle)
        print(f"  {chain}")
    print(f"\nTotal cycles: {len(cycles)}")


def _show_bottlenecks(graph: dict[str, dict]) -> None:
    """Show tickets with the most open dependents (bottlenecks)."""
    open_tickets = [tid for tid, node in graph.items() if node["status"] == "OPEN"]
    if not open_tickets:
        print("No OPEN tickets.")
        return

    # Count direct + transitive open dependents
    bottleneck_scores: list[tuple[str, int, int]] = []
    for tid in open_tickets:
        blocked = _transitive_blocked(graph, tid)
        open_blocked = [b for b in blocked if graph.get(b, {}).get("status") == "OPEN"]
        direct_blocked = [b for b in graph.get(tid, {}).get("blocks", []) if graph.get(b, {}).get("status") == "OPEN"]
        if len(direct_blocked) >= 2 or len(open_blocked) >= 5:
            bottleneck_scores.append((tid, len(direct_blocked), len(open_blocked)))

    if not bottleneck_scores:
        print("No significant bottlenecks found.")
        return

    print("🎯 Bottleneck Tickets (most open dependents)")
    print("=" * 60)
    print(f"{'Ticket':<20} {'Direct':>8} {'Transitive':>12} {'Title'}")
    print("-" * 60)

    for tid, direct, transitive in sorted(bottleneck_scores, key=lambda x: -x[2]):
        title = graph[tid]["title"][:40]
        print(f"{tid:<20} {direct:>8} {transitive:>12}  {title}")


def _categorize_ticket(tid: str, title: str, epic: str) -> int:
    """Assign a ticket to Engineer 1 (core/security/engine) or Engineer 2 (domains/ui/features/docs).
    Returns 1 or 2."""
    title_lower = title.lower()

    # Engineer 2: Domains, UI/Swift, Features, Documentation, CSV/Gazetteer, MCP, Session features
    if any(kw in title_lower for kw in ["jsondomain", "xmldomain", "yamldomain", "markdowndomain", "codedomain"]):
        return 2
    if tid in ("EM-0175-A-3-FOLLOWUP", "EM-0175-B-2", "EM-0177", "EM-0178", "EM-1128"):
        return 2
    if any(kw in title_lower for kw in ["csv term upload", "csv loader", "gazetteer file encryption", "csvkeyrulesource", "gazetteer pack", "compliance preset"]):
        return 2
    if tid in ("EM-0123-A", "EM-0123-C", "EM-0123-SEC-1", "EM-1131", "EM-1132", "EM-E007", "EM-0123", "EM-1500", "EM-1501"):
        return 2
    if any(kw in title_lower for kw in ["menubar", "settings panel", "hotkey", "launch at login", "screen share", "terminal pastejacking", "copy-event", "sparkle", "offline help", "swift auto-save", "paste preview", "profile switcher", "live regex validation", "setapp", "distribution"]):
        return 2
    if tid in ("EM-0113", "EM-0114", "EM-0206", "EM-0208", "EM-0209", "EM-1558", "EM-0108", "EM-0201", "EM-0202", "EM-0205", "EM-0117-D", "EM-0207", "EM-0403"):
        return 2
    if any(kw in title_lower for kw in ["url tracking", "copy as", "rule suggestions", "clipboard history", "named replacement pools", "settings import", "plaintext export", "session mapping view", "session continuity"]):
        return 2
    if tid in ("EM-0101", "EM-0109", "EM-0110", "EM-0111", "EM-0118", "EM-0137", "EM-0191", "EM-0193", "EM-0125Q-B"):
        return 2
    if "mcp" in title_lower or tid in ("EM-0106-PRE", "EM-0106B", "EM-0106", "EM-0106A", "EM-0106C", "EM-0195", "EM-1542"):
        return 2
    if any(kw in title_lower for kw in ["docs staleness", "licensing strategy", "user manual", "rule pack authoring", "enterprise deployment", "pre-flight", "obsolescence review", "state of play", "feature spec", "blog:"]):
        return 2
    if tid in ("EM-E011-DOCS", "EM-0404", "EM-0410", "EM-0411", "EM-0412", "EM-0402", "EM-E010-SPEC", "EM-E010-STATE", "EM-E012-CP-AUDIT-REVIEW"):
        return 2
    if tid.startswith("EM-07"):
        return 2
    if tid == "EM-0021":
        return 2

    # Engineer 1: Core engine, security, tokenization, performance, Rust internals
    if tid.startswith("EM-110") or tid.startswith("EM-112"):
        return 1
    if any(kw in title_lower for kw in ["trie", "aho-corasick", "engine factory", "pipeline performance", "replacement engine", "token format", "token map", "scrubrule", "config directory", "engine builder", "combinatorial entity", "foundation unification"]):
        return 1
    if tid in ("EM-0323-FOLLOWUP-2", "EM-0123-PERF", "EM-1527", "EM-1528", "EM-0123-CP-9-ADJ", "EM-1521", "EM-0175-A", "EM-1130", "EM-0123-B.1", "EM-0123-ERR", "EM-E010", "EM-E011", "EM-E017", "EM-E018"):
        return 1
    if any(kw in title_lower for kw in ["outline", "indexed", "per-level", "disambiguate"]):
        return 1
    if tid in ("EM-1533", "EM-1534", "EM-1535"):
        return 1
    if any(kw in title_lower for kw in ["auditentry", "receiptformatter", "compliance query", "encrypted session export"]):
        return 1
    if tid in ("EM-0188", "EM-0189", "EM-0190", "EM-0192"):
        return 1
    if "rust port" in title_lower and tid not in ("EM-0312", "EM-0311"):
        return 1
    if tid in ("EM-0300", "EM-0303", "EM-0304", "EM-0305", "EM-0306", "EM-0307", "EM-0308", "EM-0309", "EM-0310"):
        return 1
    if any(kw in title_lower for kw in ["documenthistory", "mapping_registry memory", "import_session", "undo ring"]):
        return 1
    if tid in ("EM-1557A", "EM-1118", "EM-1111", "EM-1112"):
        return 1
    if tid == "EM-0125I-SCHEMA":
        return 1
    return 1


def _parse_effort_hours(value: str) -> float | None:
    """Parse effort string to hours."""
    if not value:
        return None
    import re
    s = str(value).strip().lower()
    m = re.match(r"^([\d.]+)\s*([hdm])$", s)
    if m:
        num, unit = float(m.group(1)), m.group(2)
        if unit == "d":
            return num * 8
        if unit == "m":
            return num / 60
        return num
    m = re.match(r"^([\d.]+)\s*[-–]\s*([\d.]+)\s*([hd])$", s)
    if m:
        low, high, unit = float(m.group(1)), float(m.group(2)), m.group(3)
        num = (low + high) / 2
        if unit == "d":
            return num * 8
        return num
    return None


# Epics that contain actual build work (excludes marketing, commercial, distribution, etc.)
_BUILD_EPICS = {
    "EM-E001", "EM-E002", "EM-E005", "EM-E007", "EM-E009",
    "EM-E010", "EM-E011", "EM-E012", "EM-E017", "EM-E018",
    "EM-E019", "EM-E020", "EM-E021", "EM-E022",
}


def _show_stale(graph: dict[str, dict]) -> None:
    """Show open tickets that look stale: blocked by WONT_FIX, missing deps, or deps all closed."""
    valid_ids = set(graph.keys())

    blocked_by_wontfix: list[str] = []
    missing_dependency: list[str] = []
    all_deps_closed: list[str] = []

    for tid, node in graph.items():
        if node["status"] != "OPEN":
            continue
        deps = node.get("depends_on", [])
        blocks = node.get("blocks", [])

        if any(
            graph.get(d, {}).get("status") == "WONT_FIX" for d in deps
        ):
            blocked_by_wontfix.append(tid)
            continue

        missing = [d for d in deps + blocks if d not in valid_ids]
        if missing:
            missing_dependency.append(tid)
            continue

        known_deps = [d for d in deps if d in valid_ids]
        if known_deps and all(graph[d]["status"] == "CLOSED" for d in known_deps):
            all_deps_closed.append(tid)

    total = len(blocked_by_wontfix) + len(missing_dependency) + len(all_deps_closed)
    if total == 0:
        print("✅ No stale open tickets detected.")
        return

    print("🧟 Stale Open Tickets")
    print("=" * 60)

    def _group_and_print(label: str, tids: list[str], detail: str) -> None:
        if not tids:
            return
        print(f"\n{label} ({len(tids)})")
        by_epic: dict[str, list[str]] = defaultdict(list)
        for tid in tids:
            by_epic[graph[tid]["epic"]].append(tid)
        for epic in sorted(by_epic.keys()):
            print(f"  Epic: {epic}")
            for tid in sorted(by_epic[epic]):
                node = graph[tid]
                print(f"    ⬜ {tid} — {node['title']}")
                if detail == "wontfix":
                    refs = [d for d in node.get("depends_on", []) if graph.get(d, {}).get("status") == "WONT_FIX"]
                    print(f"       depends on WONT_FIX: {', '.join(refs)}")
                elif detail == "missing":
                    refs = [d for d in node.get("depends_on", []) + node.get("blocks", []) if d not in valid_ids]
                    print(f"       missing refs: {', '.join(refs)}")

    _group_and_print("Blocked by WONT_FIX", blocked_by_wontfix, "wontfix")
    _group_and_print("Missing dependency references", missing_dependency, "missing")
    _group_and_print("All dependencies closed but still open", all_deps_closed, "closed")

    print(f"\nTotal stale: {total}")


def _show_workstreams(graph: dict[str, dict]) -> None:
    """Show actionable tickets split into two parallel workstreams."""
    open_tickets = [tid for tid, node in graph.items() if node["status"] == "OPEN"]

    # Exclude epic roots and non-build epics
    import re
    def is_epic_root(tid: str) -> bool:
        return bool(re.match(r"^EM-E\d+$", tid))

    actionable = []
    for tid in open_tickets:
        if is_epic_root(tid):
            continue
        node = graph[tid]
        if node.get("epic", "") not in _BUILD_EPICS:
            continue
        # All deps satisfied?
        deps_ok = all(
            graph.get(dep, {}).get("status") in ("CLOSED", "WONT_FIX")
            for dep in node.get("depends_on", [])
        )
        # No open dependents?
        no_dependents = not any(
            graph.get(b, {}).get("status") == "OPEN"
            for b in node.get("blocks", [])
        )
        if deps_ok and no_dependents:
            actionable.append(tid)

    e1_tickets = [tid for tid in actionable if _categorize_ticket(tid, graph[tid]["title"], graph[tid]["epic"]) == 1]
    e2_tickets = [tid for tid in actionable if _categorize_ticket(tid, graph[tid]["title"], graph[tid]["epic"]) == 2]

    def summarize(tids: list[str]) -> tuple[int, float, dict]:
        total_hours = 0.0
        by_priority: dict[str, int] = {}
        for tid in tids:
            effort = graph[tid].get("effort", "")
            hours = _parse_effort_hours(str(effort)) or 0.0
            total_hours += hours
            p = str(graph[tid].get("priority", "?"))
            by_priority[p] = by_priority.get(p, 0) + 1
        return len(tids), total_hours, by_priority

    e1_count, e1_hours, e1_p = summarize(e1_tickets)
    e2_count, e2_hours, e2_p = summarize(e2_tickets)

    print("👤 Engineer 1: Core / Security / Engine / Tokenization / Rust Foundation")
    print(f"   {e1_count} tickets | ~{e1_hours:.1f}h | Priority: {dict(sorted(e1_p.items()))}")
    print()
    for tid in sorted(e1_tickets, key=lambda t: (_parse_effort_hours(str(graph[t].get("effort", ""))) or 999, t)):
        node = graph[tid]
        hours = _parse_effort_hours(str(node.get("effort", "")))
        hstr = f"{hours:.1f}h" if hours else "?h"
        print(f"   {tid:<18} {hstr:>6}  P{node.get('priority', '?')}  {node['title'][:55]}")

    print()
    print("👤 Engineer 2: Domains / UI / Features / Docs / MCP / Session Features")
    print(f"   {e2_count} tickets | ~{e2_hours:.1f}h | Priority: {dict(sorted(e2_p.items()))}")
    print()
    for tid in sorted(e2_tickets, key=lambda t: (_parse_effort_hours(str(graph[t].get("effort", ""))) or 999, t)):
        node = graph[tid]
        hours = _parse_effort_hours(str(node.get("effort", "")))
        hstr = f"{hours:.1f}h" if hours else "?h"
        print(f"   {tid:<18} {hstr:>6}  P{node.get('priority', '?')}  {node['title'][:55]}")

    print()
    print(f"📊 Total actionable: {len(actionable)} tickets | ~{e1_hours + e2_hours:.1f}h")


def _show_critical_path(graph: dict[str, dict]) -> None:
    """Show the longest dependency chain(s) in the graph."""
    open_tickets = [tid for tid, node in graph.items() if node["status"] == "OPEN"]
    if not open_tickets:
        print("No OPEN tickets.")
        return

    memo: dict[str, int] = {}
    lengths = {tid: _critical_path(graph, tid, memo) for tid in open_tickets}
    max_len = max(lengths.values()) if lengths else 0

    print("🛤️  Critical Path(s) — Longest dependency chain(s) among OPEN tickets")
    print("=" * 60)

    for tid, length in sorted(lengths.items(), key=lambda x: (-x[1], x[0])):
        if length == max_len and length > 0:
            graph[tid]
            print()
            print(f"Chain length: {length + 1} tickets")
            lines = _render_path(graph, tid)
            for line in lines:
                print(line)

    print()
    print(f"Longest chain: {max_len + 1} tickets")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze ticket dependencies and show paths to features."
    )
    parser.add_argument("ticket", nargs="?", help="Ticket ID to analyze")
    parser.add_argument(
        "--blocked", action="store_true", help="Show what's blocked by this ticket"
    )
    parser.add_argument(
        "--ready", action="store_true", help="Show all tickets ready to work on"
    )
    parser.add_argument(
        "--epic", metavar="EPIC", help="Show all tickets in an epic"
    )
    parser.add_argument(
        "--critical-path", action="store_true", help="Show longest dependency chain"
    )
    parser.add_argument(
        "--cycles", action="store_true", help="Detect dependency cycles"
    )
    parser.add_argument(
        "--bottleneck", action="store_true", help="Show bottleneck tickets (most dependents)"
    )
    parser.add_argument(
        "--workstreams", action="store_true", help="Show actionable tickets split into two parallel workstreams"
    )
    parser.add_argument(
        "--stale", action="store_true", help="Show open tickets that may be stale or orphaned"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON (for programmatic use)"
    )

    args = parser.parse_args()

    tickets = _collect_tickets()

    collisions = _check_duplicate_collisions(tickets)
    if collisions:
        print("ERROR: Duplicate ticket IDs detected (open-involved). Resolve before running dependency analysis:", file=sys.stderr)
        for tid, paths in collisions:
            print(f"  {tid}: {', '.join(paths)}", file=sys.stderr)
        sys.exit(1)

    graph = _build_graph(tickets)

    if args.json:
        import json as _json
        output = {}
        for tid, node in graph.items():
            output[tid] = {
                "status": node["status"],
                "title": node["title"],
                "epic": node["epic"],
                "depends_on": node["depends_on"],
                "blocks": node["blocks"],
                "transitive_deps": sorted(_transitive_deps(graph, tid)),
                "transitive_blocked": sorted(_transitive_blocked(graph, tid)),
                "ready": _is_ready(graph, tid),
                "critical_path_length": _critical_path(graph, tid),
            }
        print(_json.dumps(output, indent=2))
        return

    if args.ready:
        _show_ready(graph)
    elif args.epic:
        _show_epic(graph, args.epic)
    elif args.critical_path:
        _show_critical_path(graph)
    elif args.cycles:
        _show_cycles(graph)
    elif args.bottleneck:
        _show_bottlenecks(graph)
    elif args.workstreams:
        _show_workstreams(graph)
    elif args.stale:
        _show_stale(graph)
    elif args.ticket:
        if args.blocked:
            _show_blocked(graph, args.ticket)
        else:
            _show_path(graph, args.ticket)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

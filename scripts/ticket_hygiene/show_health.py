#!/usr/bin/env python3
"""Per-ticket health dashboard: age (days since created:) and blocked duration.

Run from the project root:
    python scripts/ticket_hygiene/show_health.py
    python scripts/ticket_hygiene/show_health.py --epic EM-E020
    python scripts/ticket_hygiene/show_health.py --json
    python scripts/ticket_hygiene/show_health.py --threshold-yellow 7 --threshold-red 21
"""

from __future__ import annotations

import argparse
import datetime
import json as _json
import sys
from dataclasses import asdict, dataclass

from audit_tickets import Ticket, _collect_tickets

DEFAULT_YELLOW = 14  # days
DEFAULT_RED = 30     # days


def _parse_date(value: object) -> datetime.date | None:
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            return None
    return None


@dataclass
class HealthResult:
    ticket_id: str
    title: str
    epic: str
    age_days: int
    blocked_days: int  # 0 if no open depends_on
    severity: str      # "🟢" | "🟡" | "🔴"


def compute_health(
    ticket: Ticket,
    ticket_map: dict[str, Ticket],
    today: datetime.date,
    threshold_yellow: int = DEFAULT_YELLOW,
    threshold_red: int = DEFAULT_RED,
) -> HealthResult:
    fm = ticket.frontmatter
    created = _parse_date(fm.get("created"))
    age_days = (today - created).days if created else 0

    deps = fm.get("depends_on", [])
    open_deps = [
        d for d in (deps if isinstance(deps, list) else [])
        if d in ticket_map
        and ticket_map[d].frontmatter.get("status", "").upper() == "OPEN"
    ]
    # No per-dep timestamps exist; proxy blocked duration with the ticket's own age.
    blocked_days = age_days if open_deps else 0

    max_days = max(age_days, blocked_days)
    if max_days > threshold_red:
        severity = "🔴"
    elif max_days > threshold_yellow:
        severity = "🟡"
    else:
        severity = "🟢"

    return HealthResult(
        ticket_id=ticket.ticket_id,
        title=fm.get("title", "(no title)"),
        epic=fm.get("epic", "NO-EPIC"),
        age_days=age_days,
        blocked_days=blocked_days,
        severity=severity,
    )


def show_health_table(
    tickets: list[Ticket],
    today: datetime.date,
    epic: str | None = None,
    threshold_yellow: int = DEFAULT_YELLOW,
    threshold_red: int = DEFAULT_RED,
    json_out: bool = False,
) -> None:
    open_tickets = [
        t for t in tickets
        if t.frontmatter.get("status", "").upper() == "OPEN"
    ]
    if epic:
        open_tickets = [t for t in open_tickets if t.frontmatter.get("epic") == epic]

    if not open_tickets:
        if json_out:
            print(_json.dumps({"today": today.isoformat(), "tickets": [], "summary": {}}))
        else:
            print("No open tickets found.")
        return

    ticket_map = {t.ticket_id: t for t in tickets}
    results = [
        compute_health(t, ticket_map, today, threshold_yellow, threshold_red)
        for t in open_tickets
    ]

    sev_order = {"🔴": 0, "🟡": 1, "🟢": 2}
    results.sort(key=lambda r: (sev_order.get(r.severity, 3), -r.age_days))

    ages = [r.age_days for r in results]
    avg_age = sum(ages) / len(ages) if ages else 0
    red = sum(1 for r in results if r.severity == "🔴")
    yellow = sum(1 for r in results if r.severity == "🟡")
    green = len(results) - red - yellow
    unblocked = sum(1 for r in results if r.blocked_days == 0)

    if json_out:
        data = {
            "today": today.isoformat(),
            "thresholds": {"yellow": threshold_yellow, "red": threshold_red},
            "tickets": [asdict(r) for r in results],
            "summary": {
                "total": len(results),
                "red": red,
                "yellow": yellow,
                "green": green,
                "oldest_days": max(ages, default=0),
                "avg_age_days": round(avg_age, 1),
                "unblocked": unblocked,
            },
        }
        print(_json.dumps(data, indent=2))
        return

    print(f"{'':4} {'Ticket':<16} {'Epic':<12} {'Age':>5} {'Blk':>5}  Title")
    print("-" * 90)
    for r in results:
        title = r.title[:48] + "…" if len(r.title) > 49 else r.title
        print(f"{r.severity}  {r.ticket_id:<16} {r.epic:<12} {r.age_days:>4}d {r.blocked_days:>4}d  {title}")

    print()
    print(f"Summary: {len(results)} open  🔴 {red}  🟡 {yellow}  🟢 {green}")
    print(f"  Oldest: {max(ages, default=0)}d  |  Avg age: {avg_age:.0f}d  |  Unblocked: {unblocked}/{len(results)}")
    print(f"  Thresholds: 🟡 >{threshold_yellow}d  🔴 >{threshold_red}d")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ticket health dashboard")
    parser.add_argument("--epic", help="Filter to one epic (e.g. EM-E020)")
    parser.add_argument("--today", help="Override today's date (YYYY-MM-DD)")
    parser.add_argument("--threshold-yellow", type=int, default=DEFAULT_YELLOW, metavar="DAYS",
                        help=f"Days before 🟡 warning (default: {DEFAULT_YELLOW})")
    parser.add_argument("--threshold-red", type=int, default=DEFAULT_RED, metavar="DAYS",
                        help=f"Days before 🔴 alert (default: {DEFAULT_RED})")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args(argv)

    today = datetime.date.today()
    if args.today:
        try:
            today = datetime.date.fromisoformat(args.today)
        except ValueError:
            print(f"ERROR: Invalid date: {args.today!r}", file=sys.stderr)
            return 1

    tickets = _collect_tickets()
    show_health_table(
        tickets,
        today,
        epic=args.epic,
        threshold_yellow=args.threshold_yellow,
        threshold_red=args.threshold_red,
        json_out=args.json,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

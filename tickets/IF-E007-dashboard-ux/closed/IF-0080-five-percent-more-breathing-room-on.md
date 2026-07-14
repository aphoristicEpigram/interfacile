---
id: IF-0080
title: Five percent more breathing room on dashboard cards and chips
epic: IF-E007
status: CLOSED
risk: LOW
priority: 4
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0080 · Five percent more breathing room on dashboard cards and chips

## Context

The dashboard's cards and icon chips sat tight - kpis, backlog cards,
panels, charts and the badge row all wanted a touch more air.

## Approach

A uniform ~5% bump to the load-bearing paddings and gaps: kpi/track/chart
card padding, backlog column gap and card margins, panel headers, list rows,
and the b-meta chip row. Values round up by 1px where 5% is sub-pixel; the
bar's fixed 32px control height is deliberately untouched.

## Acceptance criteria

- [x] Cards (kpi, track, bcard, chart, recent) and chip rows breathe wider by about 5% with no layout breakage at narrow widths.

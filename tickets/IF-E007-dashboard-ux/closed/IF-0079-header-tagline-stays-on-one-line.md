---
id: IF-0079
title: Header tagline stays on one line
epic: IF-E007
status: CLOSED
risk: LOW
priority: 4
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0079 · Header tagline stays on one line

## Context

The header tagline ('...tracked as 1,288 tickets across 22 epics. 88.7%
resolved; 145 still open.') wrapped to two lines under its 62ch cap.

## Approach

Drop the width cap and keep the line whole with white-space:nowrap; screens
under 980px wrap normally instead of overflowing.

## Acceptance criteria

- [x] The tagline renders as one line on a desktop viewport.

---
id: IF-0070
title: Link multi-level sub-ticket ids (PFX-1234-B-ii) everywhere
epic: IF-E007
status: CLOSED
risk: MEDIUM
priority: 2
effort: 1h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0070 · Link multi-level sub-ticket ids (PFX-1234-B-ii) everywhere

## Context

Multi-level sub-ticket ids (EM-2222-B-i, EM-2222-A-iii) were unlinked in
ticket bodies and mis-handled in sorts: the id regexes hardcoded one uppercase
suffix segment despite make_id_res's docstring promising deeper nesting.

## Approach

One suffix-segment pattern - digits, uppercase letters, or lowercase roman
numerals limited to i/v/x so slug words (-cli, -mid) can't glom on - reused
across ticket_split/dep_id/sub_id/ticket_link/md_ticket. sub_id now captures
the base id, so a whole family groups together in lists.

## Acceptance criteria

- [x] EM-2222's body links EM-2222-B-i and EM-2222-B-ii as real anchors.
- [x] Slug-derived ids (EM-1234-cli-...) still resolve to the bare ticket id.
- [x] depends_on lists with deep child ids parse whole ids.

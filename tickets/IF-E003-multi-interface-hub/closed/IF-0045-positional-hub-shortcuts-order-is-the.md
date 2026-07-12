---
id: IF-0045
title: Positional hub shortcuts: order is the key, drag to reorder
epic: IF-E003
status: CLOSED
risk: MEDIUM
priority: 1
effort: 4h
created: 2026-07-13
closed: 2026-07-13
updated: 2026-07-13
index_note: keys derived from hub order (1-9,0); drag or interfacile shortcut N; config field retired
---

# IF-0045 · Positional hub shortcuts: order is the key, drag to reorder

## Context

Shortcut keys were a per-repo `"shortcut"` config field (IF-0037) — one more
thing to configure, keep unique, and hand-edit. The obvious mental model is
simpler: the switcher order *is* the shortcut. Supersedes IF-0037's approach.

## Approach

Keys are derived, never stored: the first ten interfaces in registry order get
`1`–`9` then `0`, the rest none. Reordering is the only operation:

- **Drag** an interface in the hub switcher menu — persisted via
  `POST /api/ifc-order` to the registry (kept for unserved entries and extra
  keys), interfaces rebuilt immediately, keycaps follow. Hubs pinned by
  explicit `--repo` flags aren't draggable, by design.
- `interfacile shortcut N` moves the repo to position N (0 = tenth);
  bare `shortcut` shows the key and full order; `interfacile list` shows keys.
- The `"shortcut"` config field is no longer read; stripped from configs,
  examples, and docs.

## Acceptance criteria

- [x] First ten interfaces get 1-9,0 by position; 11th+ get none (unit test).
- [x] Drag endpoint reorders live and persists to the registry, preserving
      unserved entries and unknown keys (unit + HTTP test).
- [x] CLI shortcut/list reflect positional keys; docs updated everywhere.

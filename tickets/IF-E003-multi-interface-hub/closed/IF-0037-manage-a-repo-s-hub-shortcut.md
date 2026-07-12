---
id: IF-0037
title: Manage a repo's hub shortcut key from the CLI
epic: IF-E003
status: CLOSED
risk: LOW
priority: 2
effort: 2h
created: 2026-07-12
closed: 2026-07-12
updated: 2026-07-12
index_note: interfacile shortcut — show/set/clear, duplicate warning, key shown in list
---

# IF-0037 · Manage a repo's hub shortcut key from the CLI

## Context

The hub switches interfaces with a per-repo `shortcut` key in
`.interfacile/config.json`, but the only way to add, change, or remove one was
to hand-edit JSON.

## Approach

`interfacile shortcut` with three shapes: bare (show), `shortcut 3` (set),
`shortcut --clear` (remove). Setting warns when another registered repo
already uses the key, and `interfacile list` shows each repo's key.

## Acceptance criteria

- [x] `interfacile shortcut` / `shortcut 7` / `shortcut --clear` show, set,
      and remove the key, editing config.json in place (verified on a scratch
      repo).
- [x] Invalid keys (multi-character, non-alphanumeric) are rejected.
- [x] `interfacile list` displays the shortcut next to each registered repo.

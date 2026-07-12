---
id: IF-0039
title: Hub follows registry edits live
epic: IF-E003
status: CLOSED
risk: LOW
priority: 2
created: 2026-07-12
closed: 2026-07-12
effort: 2h
updated: 2026-07-12
index_note: registry mtime-watched per request; register/unregister appear on refresh
---

# IF-0039 · Hub follows registry edits live

## Context

The hub read the registry once at startup, so a freshly `init`-ed or
`register`-ed repo didn't appear in the switcher until a restart — a documented
footgun with its own troubleshooting section.

## Approach

Watch the registry file's mtime per request (under the existing request lock —
one stat when nothing changed), and rebuild the interface list on change.
Repos without `tickets/` are skipped, and a bad edit can never empty a running
hub. Only registry-driven hubs follow; explicit `--repo` lists stay pinned.

## Acceptance criteria

- [x] `interfacile register` while a hub is running makes the switcher appear
      with both repos on the next request, no restart (verified live).
- [x] Explicit `--repo` hubs ignore registry changes.
- [x] README and config-reference troubleshooting updated.

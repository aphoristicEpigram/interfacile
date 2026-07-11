---
id: IF-0034
title: Per-request context isolation
epic: IF-E008
status: OPEN
risk: HIGH
priority: 3
effort: 3d
created: 2026-07-12
updated: 2026-07-12
---

# IF-0034 · Per-request context isolation

Replace the shared-globals + lock model with request-scoped interface context so the hub is safe for concurrent/multi-user access.

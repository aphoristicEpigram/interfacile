---
id: IF-0044
title: Strip remaining imported debt from examples and docs
epic: IF-E001
status: CLOSED
risk: LOW
priority: 1
effort: 2h
created: 2026-07-12
closed: 2026-07-12
updated: 2026-07-12
index_note: neutral example configs; examples/docs describe only what exists
---

# IF-0044 · Strip remaining imported debt from examples and docs

## Context

After the ticket-flow consolidation, imported baggage still shipped in the
repo: `examples/configs/clean-paste.interfacile.json` (another project's brand
as a copyable example), an `examples/README.md` describing an `EM-` tree that
doesn't exist via deleted scripts, `engineering-reference.md`, the pre-package
dev shim, a stale dashboard doc, and the `TO BE ADDED/` staging folder.

## Approach

Delete the branded/stale files; replace the example configs with neutral ones
(`starter`, `custom-theme`); rewrite `examples/README.md` around the actual
`EX-` demo and the installed CLI; refresh CONTRIBUTING (unittest, no-deps
rule) and the main README (Development section, live-registry behaviour).

## Acceptance criteria

- [x] No CleanPaste/EM/TH-branded files anywhere in the repo; example configs
      are neutral.
- [x] `grep -riE "clean.?paste|ticket_hygiene|\.agents"` over docs/examples
      returns nothing.
- [x] Repo root contains only package, tests, examples, tickets, and docs
      that describe what actually exists.

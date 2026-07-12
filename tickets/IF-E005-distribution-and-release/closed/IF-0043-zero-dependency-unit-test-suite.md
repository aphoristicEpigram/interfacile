---
id: IF-0043
title: Zero-dependency unit test suite
epic: IF-E005
status: CLOSED
risk: LOW
priority: 1
effort: 4h
created: 2026-07-12
closed: 2026-07-12
blocks: [IF-0013]
updated: 2026-07-12
index_note: 23 stdlib unittest tests; python -m unittest discover tests
---

# IF-0043 · Zero-dependency unit test suite

## Context

The only tests in the repo exercised the deleted imported scripts, so the
package shipped untested. CI (IF-0013) and PyPI (IF-0012) need a suite to
stand on.

## Approach

`tests/` as plain `unittest` — runs with `python -m unittest discover tests`
on a bare checkout (pytest still picks them up). Twenty-three tests over
throwaway repos: creation (ids, epic spellings, rejections), the full
lifecycle (derived blocking, close/reopen/drop), lint findings, JSON output,
id-scheme helpers, and the scaffold (prefix substitution, idempotency,
drift repair, staleness notice).

## Acceptance criteria

- [x] `python -m unittest discover tests` passes with no extra installs.
- [x] Lifecycle, lint, JSON, and scaffold behaviours all covered.
- [x] CONTRIBUTING and README document the test command.

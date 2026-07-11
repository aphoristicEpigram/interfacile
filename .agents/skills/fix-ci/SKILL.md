---
name: fix-ci
description: Fetch the latest CI run from GitHub, diagnose failing jobs, reproduce failures locally, apply fixes, verify the full PR gate passes, and report. No ticket is opened. No commit is made. Use when the user says "fix ci", "CI is failing", "fix the build", "what broke in CI", or "fix-ci".
---

# Fix CI

Fetch the latest CI run, diagnose any failures, fix them locally, verify with the full PR gate, and report. This skill creates no tickets and makes no commits. Fixes should be correct and complete, not artificially minimal.

## Quick start

1. Verify `gh` is installed and authenticated.
2. Fetch the latest run for the current branch (fall back to `main`).
3. If all jobs passed → output `CI is green — nothing to fix.` and HALT immediately. No further steps.
4. Download failure logs, reproduce locally, fix, verify.
5. Append each fix to `ci-fix-log.md`.
6. Report what changed — no commit, no ticket.

---

## Step 0 — Prerequisite check

```bash
gh --version
```

If `gh` is not found:

```
gh (GitHub CLI) is not installed.
Install: brew install gh  (or `brew bundle` if the project Brewfile is present)
Auth:    gh auth login
```

**HALT.**

Confirm the repo remote:

```bash
git remote get-url origin
```

Record the `owner/repo` slug (e.g. `aphoristicEpigram/em-dash`) for all subsequent calls.

---

## Step 1 — Fetch the latest run

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
gh run list --repo OWNER/REPO --branch "$BRANCH" --limit 5 \
  --json databaseId,status,conclusion,name,headSha,createdAt
```

If no runs exist on this branch, retry with `main`:

```bash
gh run list --repo OWNER/REPO --branch main --limit 5 \
  --json databaseId,status,conclusion,name,headSha,createdAt
```

Select the most recent run. Report its ID, branch, status, conclusion, and timestamp in one line.

If the most recent run has `status != "completed"` (still running):

```
CI run #RUN_ID on <branch> is still <status> — waiting for it to finish.
```

**HALT immediately.** Do not download logs, do not run local checks, do not fix anything. Treat a running CI exactly like a green CI for the purposes of this skill.

---

## Step 2 — Identify failing jobs

```bash
gh run view RUN_ID --repo OWNER/REPO --json jobs \
  --jq '.jobs[] | select(.conclusion != "success") | {name: .name, conclusion: .conclusion, steps: [.steps[] | select(.conclusion == "failure") | .name]}'
```

If the run has `status == "completed"` and every job has `conclusion == "success"`:

```
CI is green — nothing to fix.
```

**HALT immediately.** Do not download logs, do not run local checks, do not fix anything.

Otherwise list every failing job and the step(s) within it that failed. CI has four jobs:

- `pr-gate` — blocking checks, tests, and security scanners
- `extended-tests` — benchmark, stress, and adversarial suites
- `optimized-mode-guards` — guard tests under `python -O`
- `docs` — docs freshness and Mermaid diagram validation

---

## Step 3 — Download failure logs

```bash
gh run view RUN_ID --repo OWNER/REPO --log-failed
```

Read the output carefully. For each failing job identify:

- The exact error message
- The file and line number referenced (if any)
- Whether the failure is a test error, type error, lint / script error, or security gate

If the log exceeds 500 lines, focus on the first error block per job — cascading failures almost always share one root cause.

---

## Step 4 — Reproduce locally

Before editing anything, confirm each failure reproduces locally using the commands for the failing CI job.

### `pr-gate` job

| CI step | Local command |
|---|---|
| Type check | `mypy clean_paste_lite/ tests/` |
| Run PR gate test suite | `pytest tests/ -q --junitxml=.pytest_cache/junit.xml` |
| Check package layout | `python scripts/ci/check_package_layout.py` |
| Check Engine.default() singleton comments | `python scripts/ci/check_engine_default_singleton.py` |
| Check config file coverage | `python scripts/ci/config_coverage.py` |
| Check no unmarked timing assertions in default suite | `python scripts/ci/no_unmarked_timing_assertions.py` |
| Check doc test paths resolve | `python scripts/ci/doc_test_paths.py` |
| Check test collection baseline | `python scripts/ci/test_collection_guard.py` |
| Check source hygiene | `python scripts/ticket_hygiene/audit_tickets.py --scan-source` |
| Check test quality gates | `python scripts/ci/pre_commit_quality_gates.py` |
| Check test quality lint | `python scripts/ci/test_quality_lint.py` |
| Run security scanner combined report | `python scripts/security/combined_report.py --root clean_paste_lite --baseline-dir scripts/security/baselines` |
| Run per-scanner HIGH/CRITICAL gates | `for scanner in scripts/security/audit_*.py; do name=$(basename "$scanner" .py); echo "=== $name ==="; python "$scanner" --root clean_paste_lite --severity-threshold HIGH --baseline "scripts/security/baselines/${name}.baseline.json"; done` |

Steps marked `if: always()` (security suggestions, slowest-tests reports, artifact uploads) do not block the build and do not need separate reproduction.

### `extended-tests` job

| CI step | Local command |
|---|---|
| Run extended timing suite | `pytest tests/ -m "benchmark or stress or adversarial" -q` |
| Persist benchmark results | `CPL_BENCHMARK_JSON=1 pytest tests/ -m benchmark -q` |
| Compare benchmarks against baseline | `python scripts/ci/compare_benchmarks.py` |
| Generate machine calibration | `python scripts/ci/calibrate_runner.py --output ~/.config/clean-paste-lite/calibration.json` |

The extended timing suite is sequential-only; wall-clock assertions fail under `pytest-xdist` contention.

### `optimized-mode-guards` job

| CI step | Local command |
|---|---|
| Run guard tests under python -O | `python -O -m pytest tests/ -k "under_optimized_mode" -m "not benchmark and not adversarial and not stress" -q` |

### `docs` job

| CI step | Local command |
|---|---|
| Check docs freshness | `python scripts/ci/check_docs_freshness.py --full` |
| Lint architecture Mermaid style | `python scripts/dev/lint_architecture_mermaid.py && python scripts/dev/lint_architecture_mermaid.py -i README.md && python scripts/dev/lint_architecture_mermaid.py -i docs/architecture/test-architecture.md && python scripts/dev/lint_architecture_mermaid.py -i docs/architecture/ci-pipeline.md` |
| Render architecture diagrams (light) | `python scripts/dev/render_architecture_diagrams.py && python scripts/dev/render_architecture_diagrams.py -i docs/architecture/test-architecture.md && python scripts/dev/render_architecture_diagrams.py -i docs/architecture/ci-pipeline.md` |
| Render architecture diagrams (dark) | `python scripts/dev/render_architecture_diagrams.py --theme dark && python scripts/dev/render_architecture_diagrams.py -i docs/architecture/test-architecture.md --theme dark && python scripts/dev/render_architecture_diagrams.py -i docs/architecture/ci-pipeline.md --theme dark` |

Report whether each failure reproduces. If a failure does not reproduce locally, note the discrepancy and proceed cautiously — environment drift is likely.

---

## Step 5 — Fix

Read the relevant files and apply the correct fix. Work through failing jobs in severity order: type errors first, then test failures, then lint / script failures.

After each fix, re-run the local equivalent from Step 4 for that job. Do not move to the next job until the current one passes locally.

---

## Step 6 — Full local PR gate

After all individual fixes pass, run the full gate:

```bash
pytest tests/ -q
mypy clean_paste_lite/ tests/
```

Both must be green. Fix any regressions introduced by the fixes before continuing.

---

## Step 7 — Update the running CI fix log

Append one row per distinct failure fixed this run to the `## Log` table in `ci-fix-log.md`, newest row directly under the header (not at the bottom). Never rewrite, reorder, or delete existing rows. The file's `## Format` section is the schema of record — read it before writing a row. If the file is missing, that's a problem to flag, not to silently recreate; ci-fix-log.md should already exist.

Row shape (see the file's Format table for exact column rules):

```
| YYYY-MM-DD | <branch> | <job>/<step> | <error symptom, <=15 words> | <file:line — change, <=15 words> |
```

Use the exact `<job>/<step>` naming from Step 2. One row per distinct failure — if three failures were fixed this run, write three rows.

This step runs even if only one failure was fixed. It does not run if Step 1 or Step 2 halted on green/running CI — there is nothing to log.

---

## Step 8 — Report

Output a compact summary and HALT:

```
## CI Fix Summary

**Run:** #RUN_ID — <conclusion before fix>
**Branch:** <branch>

### Failures found
- <job> / <step> — <one-line root cause>

### Fixes applied
- <file:line> — <what changed and why>

### Local gate
- pytest: X passed
- mypy: clean

Logged to ci-fix-log.md.
No commit made.
```

**HALT.** Do not commit. Do not open tickets. Do not start follow-on work.

---

## Non-negotiable rules

- **No commit.** This skill never commits. The user decides when to commit.
- **No ticket.** This skill never opens a ticket. CI fixes don't get tracked as tickets — they get logged (Step 7) instead. If the fix reveals a pattern worth tracking, mention it in the report — do not create the ticket.
- **Fix what CI caught.** Do not introduce unrelated refactoring or scope creep, but do not force fixes to be artificially minimal if a correct fix requires more.
- **Reproduce first.** Never edit code for a failure you have not confirmed locally.
- **Full gate last.** Always run pytest + mypy before reporting success, even if only one CI step failed.
- **Log every fix.** Always append to `ci-fix-log.md` (Step 7) when a fix was made — never skip it, and never rewrite or delete existing rows.

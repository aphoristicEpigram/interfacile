---
name: commit
description: Commit from the working tree. With a scope argument (ticket ID or file path), stages only that scope. With no argument, stages and commits the entire working tree (commit-all default). Does not close tickets.
---

# Commit

Commit from the current working tree. The scope argument is optional.

- **With a scope** (`commit EM-2004`, `commit path/to/file.py`): stages only that ticket or path.
- **Without a scope** (`commit`): stages everything — equivalent to `commit-all`. This is the default when in doubt.

## Quick start

1. **The scope argument is optional.** If omitted, commit-all behavior applies — the entire working tree is staged. If a ticket ID or file path is provided, only that scope is staged.
3. Run the helper to stage:
   ```bash
   python3 .agents/skills/commit/scripts/commit.py           # no arg → stages all
   python3 .agents/skills/commit/scripts/commit.py EM-XXXX   # scope → ticket files
   python3 .agents/skills/commit/scripts/commit.py path/to/file.py
   ```
4. Review what was staged (`git diff --cached --stat`).
5. Write a concise, meaningful commit message.
6. Commit. Do not use `--no-verify`.
7. Report the result.

## Workflow

### Step 1 — Understand the argument

The argument may be:

- **Absent**: commit-all default — stage the entire working tree.
- **Ticket ID** (`EM-XXXX` or bare `XXXX`): match the ticket file and its Files Touched. Bare numeric IDs are normalized to `EM-XXXX`.
- **File path**: a specific file or directory to commit.
- **"all"**: treated as absent — commit-all default applies.

### Step 2 — Stage the scope

For **no argument** (or "all"):

1. Run the helper with no argument:
   ```bash
   python3 .agents/skills/commit/scripts/commit.py
   ```
   This stages all tracked modifications, deletions, and untracked files (`git add -A`).

For a **ticket ID**:

1. Find the ticket file under `tickets/` (open or closed).
2. Read the ticket's **Files Touched** section.
3. Stage the ticket file plus every file listed in Files Touched that exists in the working tree.
4. If a Files Touched file does not exist, warn but continue.

For a **file path**:

1. Verify the path exists.
2. Stage only that path.

For scoped commits (ticket ID or file path), use explicit `git add <path> ...` — do not use `git add -A` or `git add .`.

Run the helper script to do this safely:

```bash
python3 .agents/skills/commit/scripts/commit.py <scope>
```

### Step 3 — Review staged changes

Run:

```bash
git diff --cached --stat
git diff --cached
```

Confirm the staged changes match the requested scope. If they do not, reset and report.

### Step 4 — Write the commit message

The message must describe what was done and why, not list files.

For a ticket ID:

```bash
git commit -m "$(cat <<'EOF'
EM-XXXX: <concise summary>

<optional body>
EOF
)"
```

For no argument or a file path:

```bash
git commit -m "$(cat <<'EOF'
<concise summary>

<optional body>
EOF
)"
```

Do not use `--no-verify`. If the pre-commit hook fails, fix the issue and retry.

### Step 5 — Report

Output a one-line summary including the commit hash:

```
Committed <scope|all> as <short-hash>: <message summary>
```

## Rules

- The scope argument is optional. No argument (or "all") → stage the entire working tree.
- Do not stage unrelated files when a scope is given.
- Do not use `--no-verify`.
- Do not commit if the staged changes do not match the requested scope.

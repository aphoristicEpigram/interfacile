---
name: commit-all
description: Convenience alias for /commit with no argument. Stages and commits the entire working tree for a clean git status. Delegates staging to commit/scripts/commit.py with no scope argument. No argument required.
---

# Commit All

Convenience alias for `/commit` with no argument. Stages everything in the working tree so `git status` returns clean.

**Prefer `/commit` directly** — calling it with no argument produces identical behavior. Use `commit-all` only when the intent is unambiguous and you want the alias for clarity.

## Quick start

1. Run the commit helper with no argument (no-arg = commit-all default):
   ```bash
   python3 .agents/skills/commit/scripts/commit.py
   ```
3. Review what was staged (`git diff --cached --stat`).
4. Write a concise, meaningful commit message.
5. Commit. Do not use `--no-verify`.
6. Report the result.

## Workflow

### Step 1 — Inspect the working tree

```bash
git status
git diff --stat
```

Confirm that committing everything is intentional.

### Step 2 — Stage everything

```bash
python3 .agents/skills/commit/scripts/commit.py
```

This runs `git add -A` and reports the staged diff stat.

### Step 3 — Review staged changes

```bash
git diff --cached --stat
git diff --cached
```

If anything looks wrong, reset and report before committing.

### Step 4 — Write the commit message

```bash
git commit -m "$(cat <<'EOF'
<concise summary>

<optional body: what changed and why>
EOF
)"
```

Do not use `--no-verify`. If the pre-commit hook fails, fix the issue and retry.

### Step 5 — Report

```
Committed all changes as <short-hash>: <message summary>
```

## Rules

- Do not ask for confirmation before staging — the user explicitly asked for a clean tree.
- Do not exclude untracked files or unrelated modifications unless the user explicitly told you to skip them.
- Do not use `--no-verify`.

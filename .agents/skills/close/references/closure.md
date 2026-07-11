# Close Ticket — Detailed Reference

Loaded on demand when the main `SKILL.md` needs expansion.

## Table of Contents

1. [Commit Mode Prompt](#1-commit-mode-prompt)
2. [A+ Review Block Template](#2-a-review-block-template)
3. [Quality Closure Note Template](#3-quality-closure-note-template)
4. [Commit Message Template](#4-commit-message-template)
5. [Halt Messages](#5-halt-messages)

---

## 1. Commit Mode Prompt

Ask the user before any other work:

```
Commit mode for EM-XXXX:

  a) Work only — stage and commit only the files touched by this ticket (`/commit EM-XXXX`)
  b) All — stage and commit everything (`/commit-all`) for a clean working tree
  c) No commit — close the ticket but don't commit
```

Valid answers: `a`, `b`, `c`, or the full label.

---

## 2. A+ Review Block Template

```markdown
## A+ Review — EM-XXXX

### Overall verdict
<one sentence: A+ / needs polish / has issues>

### What's excellent
- <specific — file:line or pattern>

### What needs fixing
- <specific issue> → <what to change>

### Test gaps
- <specific gap> → <what test to add>
```

If verdict is **needs polish** or **has issues**, fix every item. Mark completed items with ✓.

---

## 3. Quality Closure Note Template

Append to the retro:

```markdown
### Quality Closure

**A+ review:** <verdict — e.g. "A+ after polish" / "A+ no changes needed" / "Skipped">
**Review fixes applied:** <list what was fixed, or "None">
**"3 More Hours" items completed:** <N of N, or "Skipped">
**Future Items ticketed:** <list tickets, or "None">
**Final test count:** <X passed, Y skipped, Z xfailed>
```

---

## 4. Commit Message Template

### Mode a — work only

```bash
git add <file1> <file2> ... tickets/<epic>/closed/EM-XXXX*.md
git commit -m "$(cat <<'EOF'
EM-XXXX: <one-line description>

Ceremony: <2-3 sentences: what was fixed, key design decision, 3-hours items completed, quality verdict>

Approved: <quote the exact phrase the user used to approve the retro>

- A+ review: <verdict>
- 3 More Hours: <N items completed>
- Final tests: <X passed>
EOF
)"
```

### Mode b — all

```bash
git add -A
git commit -m "$(cat <<'EOF'
EM-XXXX: <one-line description>

Ceremony: <2-3 sentences>

Approved: <exact user phrase>

- A+ review: <verdict>
- 3 More Hours: <N items completed>
- Final tests: <X passed>
EOF
)"
```

### Mode c — no commit

Output:

```
EM-XXXX closed. No commit made.
```

---

## 5. Halt Messages

### Final halt

```
EM-XXXX CLOSED. Ceremony complete. Awaiting instructions.
```

### Pre-flight failures

```
⛔ Cannot close EM-XXXX: ticket is not in an open/ directory.
```

```
⛔ Cannot close EM-XXXX: missing ## Retro section. Retro must be written before closing.
```

```
⛔ Cannot close EM-XXXX: missing ### If I Had 3 More Hours section. This section is mandatory.
```

### Commit hook blocked

If the hook blocks, identify whether `Ceremony:`, `Approved:`, or the retro section is missing. Fix that specific problem and retry. Never use `--no-verify`.

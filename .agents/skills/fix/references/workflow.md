# Fix Ticket — Detailed Workflow Reference

Use this reference when the main `SKILL.md` instructions need expansion. This file is loaded on demand.

## Table of Contents

1. [Implementation Plan Template](#1-implementation-plan-template)
2. [Code Rules](#2-code-rules)
3. [Test Data Policy](#3-test-data-policy)
4. [Verification Checklists](#4-verification-checklists)
5. [Retro Template](#5-retro-template)
6. [Commit Template](#6-commit-template)
7. [Halt Messages](#7-halt-messages)

---

## 1. Implementation Plan Template

Post this in chat at Step 3 and halt.

```markdown
## Implementation Plan — EM-XXXX

**Ticket:** <title from frontmatter>
**Effort estimate:** <honest dev-hours>
**Approach:** <the A+ solution from the Pre-Work Staff Review, stated concisely — one paragraph>

### Files I will touch
| File | Change |
|------|--------|
| path/to/file.py | <what changes and why> |

### Implementation order
1. <first change — specific>
2. <second change — specific>
...

### Test plan
- <what the new/changed tests verify>
- Extended suite needed? <YES / NO — state the AGENTS.md criterion that applies>

### Concerns
<Anything that needs a decision or could go wrong. If none: "None identified.">

Awaiting approval to begin.
```

---

## 2. Code Rules

- **Always choose A+.** If the ticket's Fix/Approach proposes a B solution and you see a better one: stop, describe both options, ask which to use. Do not proceed on your own judgment.
- **Rust-portable only.** No Python-specific patterns that don't translate cleanly:
  - dynamic dispatch via `__dict__`
  - monkey-patching
  - `importlib` tricks
  - mutable class state
- **Security-first.** No command injection, no plaintext secrets, no reserved test values.
- **Raise conflicts.** If the approach conflicts with existing code in a way the ticket didn't anticipate, stop and raise it. Do not work around it silently.

---

## 3. Test Data Policy

Reserved values are blocked by the pre-commit gate. Never use them as test input:

- `example.com`, `example.net`, `example.org`, `localhost`
- `555-*` US phone numbers
- `192.0.2.*`, `203.0.113.*`, `2001:db8::*` TEST-NET IPs
- `666-**-****`, `9**-**-****` SSNs

Always import safe fixtures:

```python
from tests.support.fixtures import SAFE_EMAIL, SAFE_EMAILS, SAFE_PHONE, SAFE_SSN, SAFE_CC, SAFE_DOMAINS
```

---

## 4. Verification Checklists

### After each meaningful change

```bash
pytest tests/ -q
mypy clean_paste_lite/ tests/
python scripts/ticket_hygiene/ticket.py lint
```

All three must be green before continuing.

### Extended suite

Run only if:
- the ticket modifies performance-critical code
- the ticket adds/removes/changes `@pytest.mark.benchmark`, `stress`, or `adversarial` tests
- the ticket changes `PerformanceConfig`, calibration, or benchmark thresholds
- the ticket fixes a failing extended test

Command:

```bash
pytest tests/ -m "benchmark or stress or adversarial" -q
```

### Final verification

```bash
pytest tests/ -q -n auto
mypy clean_paste_lite/ tests/
python scripts/ticket_hygiene/ticket.py lint
```

Also:
- [ ] Every acceptance criterion verified against actual code
- [ ] Test files classified into `tests/unit/`, `tests/integration/`, `tests/regression/`
- [ ] Placeholder/scaffold tests deleted
- [ ] `AGENTS.md` / `README.md` updated if public API or architecture changed
- [ ] `AGENTS.md` `Last verified` date updated if it was modified

---

## 5. Retro Template

Append to the bottom of the ticket file:

```markdown
---

## Retro

**Closed:** <YYYY-MM-DD>
**Actual effort:** <Xh>
**Tickets closed:** <N>
**Test count at close:** <X passed, Y skipped, Z xfailed — from the final pytest run>

### Design Decisions
<What decisions were made and why.>

### What Went Well
<What worked cleanly.>

### What Didn't Go Well
<What was harder than expected.>

### Issues
<Bugs discovered, edge cases, anything revisited.>

### Future Items
<What would be worth doing next.>

### If I Had 3 More Hours
<Specific improvements. Mandatory — minimum 2 bullets.>

### Would I Inherit This Code?
<Yes / No / With caveats — and why.>
```

---

## 6. Commit Template

Stage only files created or modified for this ticket. Commit message must contain `Ceremony:` and `Approved:`.

```bash
git commit -m "$(cat <<'EOF'
EM-XXXX: <one-line description>

Ceremony: <2-3 sentence retro summary>

Approved: <exact phrase the user used>
EOF
)"
```

---

## 7. Halt Messages

### Missing staff review

```
⛔ No staff review found for EM-XXXX.

Run /scope EM-XXXX first. Work cannot begin until a VALID verdict is on the ticket.
```

### Retro written — hand off to /close

Output this, then immediately execute `/close` from Step 0:

```
EM-XXXX retro written. Handing off to /close.
```

`/close` Step 0 presents the full decision menu and halts. Do not present the menu here.

### Final halt

```
EM-XXXX CLOSED. Ceremony complete. Awaiting instructions.
```

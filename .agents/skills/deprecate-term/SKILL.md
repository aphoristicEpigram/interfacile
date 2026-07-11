---
name: deprecate-term
description: Retire or deprecate a codebase term (class, module, constant, or architectural name) according to the project's deprecation policy. Updates docs/architecture/deprecated-terms.md and docs/DEPRECATION.md, masks retired terms in closed-ticket references, and ensures live docs do not refer to deleted code. Use when deleting dead code, removing a public API, or scheduling a feature for removal.
---

# Deprecate / Retire a Codebase Term

This skill implements the project's deprecation lifecycle for classes, modules,
constants, and other architectural terms. It covers two stages:

- **Deprecated** — the term still exists but is scheduled for removal.
- **Retired** — the term has been deleted from the codebase.

Use this skill whenever you intentionally remove or schedule the removal of a
term that could appear in old tickets, retros, docs, or comments. This is a
codebase-maintenance skill, not a ticket-lifecycle skill; it does not open or
close tickets.

---

## Core principle

**A deleted term must not look alive.**

Old references in closed tickets are masked with the `_RETIRED` convention so
future readers do not accidentally recreate removed architecture. Live docs and
tests must be updated to stop using the deleted term.

---

## Quick start

1. Determine the stage:
   - **Deprecating** — term still works, removal planned: go to §Deprecate path.
   - **Retiring** — term is being deleted now: go to §Retire path.
2. Compute the masked form for retired terms (§Mask algorithm).
3. Update `docs/architecture/deprecated-terms.md`.
4. Update `docs/DEPRECATION.md` (currently deprecated / recently retired tables).
5. Update live docs/tests that reference the deleted term.
6. Run the PR gate after any code changes.

---

## Deprecate path

Use when a feature is still present but scheduled for removal.

1. Add a `DeprecationWarning` at every live call site, with a clear migration path.
2. Add a row to the **Currently deprecated features** table in `docs/DEPRECATION.md`.
3. Create a ticket to track the actual removal.
4. Do **not** add the term to `docs/architecture/deprecated-terms.md` yet — that file
   is only for retired terms.

---

## Retire path

Use when deleting a term from the codebase.

1. **Delete the code.** Remove the class, module, constant, or function from
   `clean_paste_lite/` (and `tests/` if it is test-only dead code).
2. **Compute the masked form** of the term (§Mask algorithm).
3. **Add a glossary entry** to `docs/architecture/deprecated-terms.md` under
   **Glossary of retired terms**:

   ```markdown
   | `<MASKED_FORM>` | `<OriginalTerm>` |
   ```

4. **Update `docs/DEPRECATION.md`:**
   - If the term was previously in **Currently deprecated features**, move its row
     to **Recently retired features**.
   - If the term was never formally deprecated (e.g. dead code), add it directly
     to **Recently retired features**.
5. **Update live references** in docs, tests, and code comments so they no longer
   use the deleted term as if it were current. It is acceptable for closed tickets
   and historical notes to use the masked `_RETIRED` form.
6. **Do not mask live terms.** If a term still exists anywhere in
   `clean_paste_lite/` or `tests/`, it is not retired.

---

## Mask algorithm

For a retired term:

1. Split the term into segments on `_`, `/`, and `.`.
2. For each segment of length **1 or 2**, keep it as-is.
3. For each longer segment, keep the first and last character and replace the
   interior with `*` characters.
4. Join the segments with `_` and append `_RETIRED`.

Examples:

| Original | Masked form |
|---|---|
| `Change` | `C****e_RETIRED` |
| `TokenChangeModel` | `T***n_C****e_M***l_RETIRED` |
| `RANDOM_FROM_CSV` | `R****M_F**M_C*V_RETIRED` |
| `MCP_API.md` | `M*P_A*I_MD_RETIRED` |

One-liner to compute the mask:

```bash
python3 - <<'PY'
import re, sys
term = sys.argv[1]
def mask(s):
    if len(s) <= 2:
        return s
    return s[0] + '*' * (len(s) - 2) + s[-1]
print('_'.join(mask(seg) for seg in re.split(r'[_/.]', term)) + '_RETIRED')
PY
```

---

## Updating closed tickets

If a closed ticket file contains the original term, replace occurrences with the
masked form. This applies to the body of closed tickets under
`tickets/<epic>/closed/`. Do **not** mask terms in open tickets unless the term
has already been deleted.

When the retirement is part of a ticket that is being closed now, mask the term
in that ticket's body as part of the close ceremony.

---

## Acceptance criteria

- [ ] The term no longer exists in production or test code (unless it is only
      deprecated, not retired).
- [ ] `docs/architecture/deprecated-terms.md` contains the masked form and the
      original term.
- [ ] `docs/DEPRECATION.md` reflects the correct lifecycle stage.
- [ ] No live docs or tests refer to the deleted term without the `_RETIRED`
      suffix.
- [ ] Closed tickets that mention the term use the masked form.
- [ ] The PR gate passes after any code changes.

---

## See also

- `docs/DEPRECATION.md` — project deprecation policy and lifecycle tables.
- `docs/architecture/deprecated-terms.md` — retired-term glossary and masking convention.
- EM-1765 — Deprecated-term registry and zombie-term replacement ceremony.

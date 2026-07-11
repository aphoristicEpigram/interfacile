# A+ Review — Shared Reference

Used by `/close` (Step 2), `/approve` (Step 2), and `/fund` (Step 3). Run before completing bonus or 3-hours work.

## Review block template

```markdown
## A+ Review — EM-XXXX[variant]

### Overall verdict
<one sentence: A+ / needs polish / has issues>

### What's excellent
- <specific — file:line or pattern>

### What needs fixing
- <specific issue> → <what to change>

### Test gaps
- <specific gap> → <what test to add>
```

Heading variant per skill:
- `/close` and `/fund`: `## A+ Review — EM-XXXX`
- `/approve`: `## A+ Review — EM-XXXX follow-ups`
- `/fund` (funded work): `## A+ Review — EM-XXXX funded work`

## Review criteria

Assess as a staff engineer:

- **Architecture**: cleanest, most elegant solution? Fits the codebase?
- **Rust-portability**: will it translate cleanly to the Rust port (EM-E005)?
- **Forward-thinking**: solves today's problem without creating tomorrow's debt?
- **Tests**: happy path, failures, edges, adversarial cases; behavioural, not internals?
- **Elegance**: anything that makes you wince — large functions, misleading names, apologetic comments?

## After the review

If the verdict is **needs polish** or **has issues**: fix everything listed. Do not ask — just fix. Mark each fix with ✓ in the review block.

After all fixes, run the full PR gate (see [pr-gate.md](pr-gate.md)). All three commands must be green before proceeding.

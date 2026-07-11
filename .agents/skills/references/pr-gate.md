# PR Gate — Shared Reference

All three commands must pass before continuing. Used by `/fix`, `/fix-now`, `/close`, `/approve`, `/fund`, and `/batch`.

## Full gate

Run after each meaningful milestone and as the final verification step.

```bash
pytest tests/ -q -n auto
mypy clean_paste_lite/ tests/
python scripts/ticket_hygiene/ticket.py lint
```

## Mid-implementation gate

Run after each individual change for faster feedback:

```bash
pytest tests/ -q
mypy clean_paste_lite/ tests/
python scripts/ticket_hygiene/ticket.py lint
```

## Extended suite

Run **only** when the ticket:
- Modifies performance-critical code
- Adds/removes/changes `@pytest.mark.benchmark`, `stress`, or `adversarial` tests
- Changes `PerformanceConfig`, calibration, or benchmark thresholds
- Fixes a failing extended test

```bash
pytest tests/ -m "benchmark or stress or adversarial" -q
```

Otherwise skip it entirely — do not run as a matter of routine.

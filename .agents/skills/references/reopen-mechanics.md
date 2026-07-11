# Reopen Mechanics — Shared Reference

Used by `/reopen` and `/rollback` (Step 7). Describes the standard steps for moving a ticket from `closed/` back to `open/` and restoring its frontmatter.

## File move

```bash
mv tickets/<epic>/closed/<filename> tickets/<epic>/open/<filename>
```

The filename does not change. Only the directory changes.

## Frontmatter fields to restore

In the moved file, update the YAML frontmatter block (between `---` delimiters):

- Set `status: OPEN`
- Remove the `closed:` field (prefer removal over setting to empty string)
- Remove the `closed_reason:` field (prefer removal over setting to empty string)
- Remove the `closed_note:` field (prefer removal over setting to empty string)

If any field is absent, skip it — do not add fields that were not present.

## Lint

```bash
python scripts/ticket_hygiene/ticket.py lint
```

Fix any errors introduced by this reopen. Pre-existing errors in other tickets may be noted but should not be fixed here.

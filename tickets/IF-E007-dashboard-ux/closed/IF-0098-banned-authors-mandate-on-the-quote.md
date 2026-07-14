---
id: IF-0098
title: Swap Sadhguru for Osho; drop the authors we don't quote
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 30m
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
---

# IF-0098 · Swap Sadhguru for Osho; drop the authors we don't quote

## Context

The shipped quote list carried authors this repo's owner doesn't want to
quote: Sadhguru, Teal Swan, Gabor Maté, Gabrielle Bernstein.

## Approach

Just edit the list — no enforcement code. This is one repo's taste, not a
rule to impose on anyone who installs the package: if a downstream user wants
those authors, that is their call, and their own `.interfacile/quotes.txt`.
Sadhguru's five quotes are replaced in place with Osho; Teal Swan's remaining
line is dropped with no replacement; Maté and Bernstein were already out.
140 quotes remain.

(A first pass coded a `!banned:` directive enforced in load_quotes(). Wrong
call — it turned a personal preference into package behaviour. Reverted.)

## Acceptance criteria

- [x] No quotes by Sadhguru, Teal Swan, Gabor Maté or Gabrielle Bernstein in
      the packaged list.
- [x] Five Osho quotes take Sadhguru's slots; 140 quotes total.
- [x] No ban logic in the loader; the list is just a list.
- [x] Suite green.

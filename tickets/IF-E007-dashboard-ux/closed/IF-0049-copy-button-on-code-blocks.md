---
id: IF-0049
title: Copy button on code blocks
epic: IF-E007
status: CLOSED
risk: LOW
priority: 3
effort: 1h
created: 2026-07-13
closed: 2026-07-13
updated: 2026-07-13
index_note: hover copy on every code block via the response transform
---

# IF-0049 · Copy button on code blocks

## Context

Copying a command out of a rendered ticket or doc meant drag-selecting inside
a `<pre>` block — fiddly, and it grabs the prompt characters too.

## Approach

A hover "copy" button on the top right of every code block, injected by the
response transform wherever a page contains one — tickets, docs, ADRs, epic
pages all get it without their renderers knowing. Clipboard API with an
execCommand fallback, "copied ✓" feedback, theme-variable styling.

## Acceptance criteria

- [x] Every `<pre>` on every rendered page gets the button; text copies
      without the button label leaking into it.
- [x] Styling follows the active theme in light and dark.

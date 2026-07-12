---
id: IF-0038
title: Template tokens replace find-and-replace reskin
epic: IF-E001
status: CLOSED
risk: MEDIUM
priority: 1
effort: 4h
created: 2026-07-12
closed: 2026-07-12
updated: 2026-07-12
index_note: reskin via __PFX__/__BRAND__/etc tokens; ticket bodies never rewritten
---

# IF-0038 · Template tokens replace find-and-replace reskin

## Context

`_transform_html` rebranded every page by string-replacing real-looking
literals baked into the embedded HTML (`EM-`, `Clean Paste`, the PII tagline).
Fragile by its own docstring's admission — editing the template copy silently
broke substitution — and wrong for content: a ticket body that legitimately
mentioned `EM-` ids got rewritten in the browser. It also meant anyone grepping
the installed package found another project's name.

## Approach

Replace all 38 served occurrences with explicit tokens — `__PFX__`,
`__BRAND__`, `__FAVICON__`, `__TAGLINE__`, `__EYEBROW__` — substituted
unconditionally per request, like the existing `__HDR_ICON__`. Comments and
docstrings keep their illustrative examples; only served strings changed.

## Acceptance criteria

- [x] No `Clean Paste` / tagline / eyebrow literals and no served `EM-`
      remain in templates; no token leaks into any rendered page.
- [x] A ticket body mentioning `EM-` renders verbatim (verified on IF-0036).
- [x] Config-less repos render the engine defaults; dashboard, ticket, epic,
      ADR, and prioritize pages all verified over HTTP.

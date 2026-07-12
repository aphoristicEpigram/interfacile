# Examples

A self-contained demo board plus ready-made config files.

```
examples/
  .interfacile/config.json      # the demo's own interface config
  tickets/                      # a small, valid EX- ticket tree
    EX-E001-core/
      EX-E001-core.md           # epic charter
      open/  closed/
    EX-E002-interface/
      ...
  configs/                      # example .interfacile/config.json files
    starter.interfacile.json        # the minimal useful config
    custom-theme.interfacile.json   # full custom palette, links, epics
```

## Try the demo board

From the repo root (no setup, nothing written outside `examples/`):

```bash
interfacile serve --repo examples
```

The ticket flow works against it too:

```bash
interfacile tickets --repo examples
interfacile lint    --repo examples
```

## Start a real project

Don't copy this tree — run `interfacile init` in your repo instead. It infers
your id prefix, offers an epic wizard, seeds a starter `tickets/` tree, and
installs the ticket-flow skills. The files here are for reading: the `EX-`
tickets show the frontmatter schema in practice, and
[`configs/`](configs/) documents every config field.

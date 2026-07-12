# Contributing to interfacile

Thanks for being here — contributions are very welcome, whether that's a bug
report, a new colour preset, a docs fix, or a feature.

interfacile is deliberately small and dependency-free: **standard-library Python
only, no build step**. Please keep it that way — a big part of the appeal is that
a single file serves the whole dashboard.

## Getting set up

```bash
git clone https://github.com/aphoristicEpigram/interfacile
cd interfacile
python3 -m venv venv && source venv/bin/activate
pip install -e .          # puts the `interfacile` command on your PATH
```

Run it against the bundled demo, or dogfood it against interfacile's own board:

```bash
interfacile serve --repo examples     # the EX- demo interface
interfacile                           # from the repo root: interfacile's own board
interfacile hub --repo examples --repo .   # both, with the switcher
```

Run the tests (standard library only, like everything else):

```bash
python -m unittest discover tests
```

## How work is tracked

interfacile tracks its own development **in interfacile** — see [`tickets/`](tickets/)
(or just run `interfacile` in the repo). The flow is the built-in one:
`interfacile new "Title" --epic E00N` to file work, `interfacile close ID` when
it ships, `interfacile lint` to keep the tree honest. Feel free to pick
something up from `interfacile ready`, or propose a new ticket in your PR.

## Proposing a change

1. Open an issue first for anything non-trivial, so we can agree on the approach.
2. Branch off `main`, keep changes focused, and match the surrounding code style.
3. **No new dependencies** — runtime or test. Everything is stdlib.
4. Run `python -m unittest discover tests`, and if you touch the server, verify
   it still boots and renders: `interfacile serve --repo examples --no-open`
   and click around.
5. Open a PR describing what changed and why. Screenshots help for UI changes.

## Good first contributions

- **New colour presets** — add an entry to `PRESETS` in
  [`interfacile/server.py`](interfacile/server.py); each is just a few semantic
  colours (`ground`, `surface`, `ink`, `accent`) for light and dark.
- **Docs** — clarify the [README](README.md) or the config reference under
  [`examples/configs/`](examples/configs/).
- **Ticket format / config options** — small, well-scoped improvements.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).

---
id: IF-0062
title: Never leave a server you cannot find: ps, stop, and no duplicates
epic: IF-E004
status: CLOSED
risk: LOW
priority: 1
effort: 3h
created: 2026-07-14
closed: 2026-07-14
updated: 2026-07-14
index_note: servers register themselves; ps/stop; no duplicate per repo; port clash and reuse both say what happened
---

# IF-0062 · Never leave a server you cannot find: ps, stop, and no duplicates

## Context

Twenty-one `interfacile` servers were found running on this machine, on ports
8801–8850, none of them known to the person whose machine it is. One of them sat
on 8790 — the port this repo's config asks for — so `interfacile` died on
`OSError: [Errno 48] Address already in use` before printing anything, and the
tool looked broken.

They were left by agents running the server to check their work, but the failure
is the tool's: **a server you started is a process you now have to know about,
find, and kill, and interfacile gives you nothing to do that with.** You are
expected to remember a `pkill -f` incantation. Anyone who runs the dashboard
several times a day will accumulate these.

A tool that spawns processes owes you a way to see them and stop them.

## Approach

**Servers register themselves.** On start, a server writes a small record —
pid, port, url, the repos it serves — into `~/.config/interfacile/servers/`, and
removes it on exit (`atexit`, plus SIGTERM/SIGINT, so Ctrl-C tidies up). The
record is the *only* new state, it lives beside the existing registry, and it is
per-user, not per-repo.

A record is a claim, not a fact: a `kill -9` leaves one behind. So every read
verifies the pid is alive (`os.kill(pid, 0)`) and deletes the record if it isn't.
Stale entries clean themselves up on the next `ps`.

**Two commands, which is all this needs:**

    interfacile ps            what is running: pid, port, url, repos
    interfacile stop [--all]  stop this repo's server, or every one of them

**And no duplicates in the first place.** `serve`/`hub` check the records before
binding: if a live server is already serving this repo, don't start a second one
— print where it is and open *that*, which is what you wanted anyway. Combined
with the records, the number of servers stops growing on its own.

**A port clash finally says what it is.** If the port is taken by something that
isn't ours, exit with a line that names the port and suggests a free one, instead
of a socket traceback.

## Acceptance criteria

- [x] A running server appears in `interfacile ps` with pid, port, url and repos.
- [x] Its record is removed on exit — normal exit and Ctrl-C alike.
- [x] A record whose process is gone is treated as absent and cleaned up.
- [x] `interfacile stop` stops this repo's server; `--all` stops every one.
- [x] Running `interfacile` when a server already serves this repo opens the
      existing one instead of starting a second — and *says* it is opening it.
      **Reopened for this:** the first message ("already serving -> url") was
      true but read as a failure, because the command exits straight back to the
      prompt without serving. A terminal that returns silently looks like a tool
      that didn't launch, which is exactly the confusion this ticket exists to
      remove.
- [x] A port held by something else exits with a message naming the port and a
      free alternative — no traceback.
- [x] Tests cover: register/list/liveness, stale-record cleanup, and `stop`
      actually killing a real process.

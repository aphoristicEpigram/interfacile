---
id: IF-0100
title: Create a ticket from anywhere in the hub
epic: IF-E006
status: CLOSED
risk: LOW
priority: 2
effort: 4h
created: 2026-07-14
closed: 2026-07-15
updated: 2026-07-15
---

# IF-0100 · Create a ticket from anywhere in the hub

## Context

Filing a ticket today means dropping to a terminal and running `interfacile new`.
That is a context switch away from the board you were looking at, and it arrives
at the moment you are least willing to pay one — the thought that deserves a
ticket usually turns up while you are reading a chart or another ticket. The hub
should be able to take the ticket there and then, from whatever page you are on.

## Approach

A compact modal, injected on every page via `_transform_html` so it is not a
dashboard-only feature, opened by pressing `n` when focus is not in a field.
`n`, not `Cmd+N`: browsers reserve `Cmd+N` for a new window and a page cannot
`preventDefault` it. A `＋ New ticket` control in the header/bar makes it
discoverable for anyone who does not know the key.

The form is short by default — title, epic, priority, risk, effort and a context
blurb — with everything but the title pre-populated, so the fast path is type a
title and save. Epic defaults to the last one used (localStorage), falling back
to the first. Priority defaults to 3, risk to MEDIUM, effort to 2h. A `<>`
disclosure expands the form to the remaining fields (`depends_on`, explicit id)
for the rarer case.

The id shown in the form is a preview, not a reservation. An agent may write a
ticket between the form opening and the save. So the id is allocated on save,
server-side, inside the existing `_LOCK`: re-scan the board, take the next free
number, write the file with `O_EXCL`, and on collision retry with the next
number. The response carries the id that was actually used, which may not be the
one previewed — the toast reports the real one.

On save the modal closes, the user stays on the page they were on, and a toast
confirms with a link to the new ticket. Creation reuses `ticket.cmd_new`'s logic
rather than reimplementing frontmatter assembly, and records a `new` event in the
hub event log so a modal-created ticket is indistinguishable from a CLI-created
one downstream.

Tickets created here are `OPEN`, like every other ticket. A separate unscoped
status was considered and dropped: it would mean teaching ~20 `status == "OPEN"`
sites about a fifth status, and any site that was missed would silently drop the
ticket from that view.

## Acceptance criteria

- [x] Pressing `n` on the dashboard, a ticket page, a doc page and a chart page opens the New ticket modal; typing `n` inside a text field or textarea does not.
- [x] `Esc` closes the modal, and a `＋ New ticket` control in the header/bar opens it.
- [x] The compact form shows title, epic, priority, risk, effort and a context blurb, with epic/priority/risk/effort pre-populated and only the title empty.
- [x] The `<>` disclosure expands to reveal `depends_on` and an explicit id override, and collapses again.
- [x] Epic defaults to the last epic used from this browser, and to the first epic when there is no stored choice.
- [x] Saving creates a real ticket file in `tickets/<EPIC>/open/` with valid frontmatter, and `interfacile lint` passes on it.
- [x] The context blurb, when given, replaces the placeholder text in the ticket's Context section; when blank the standard scaffold is used.
- [x] If the previewed id is taken between the form opening and the save, the ticket is created under the next free id and the toast reports that id — no clash, no overwrite, no error shown to the user.
- [x] The toast links to the new ticket, and the user remains on the page they filed from.
- [x] Creation appears in the hub event log as a `new` event, the same as `interfacile new`.

"""The New-ticket form: id allocation under a race, and the server's validation.

Standard library only — run with either:

    python -m unittest discover tests
    pytest tests
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from interfacile import server
from interfacile import ticket


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class NewTicketCase(unittest.TestCase):
    """A throwaway XX repo with two epics, served as the active interface."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ifc-new-")
        self.addCleanup(shutil.rmtree, self.root, True)
        # Creating a ticket records an event; keep that out of the developer's
        # own hub-wide log.
        self.home = tempfile.mkdtemp(prefix="ifc-new-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self._old_home = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.home
        self.addCleanup(self._restore_home)

        write(os.path.join(self.root, ".interfacile", "config.json"),
              json.dumps({"ids": {"prefix": "XX", "digits": 4}}))
        for code, name in (("E001", "first-epic"), ("E002", "second-epic")):
            edir = os.path.join(self.root, "tickets", "XX-%s-%s" % (code, name))
            write(os.path.join(edir, "XX-%s-%s.md" % (code, name)),
                  "---\nid: XX-%s\ntitle: %s\nstatus: OPEN\nindex_exempt: true\n"
                  "---\n\n# XX-%s\n" % (code, name.replace("-", " ").title(), code))
            os.makedirs(os.path.join(edir, "open"), exist_ok=True)
            os.makedirs(os.path.join(edir, "closed"), exist_ok=True)

        self._saved = (server.REPO_ROOT, server.TICKETS_DIR, server.PFX)
        server.REPO_ROOT = self.root
        server.TICKETS_DIR = os.path.join(self.root, "tickets")
        server.apply_config({"ids": {"prefix": "XX", "digits": 4}})
        self.addCleanup(self._restore_server)
        self.repo = ticket.Repo(self.root)

    def _restore_server(self):
        server.REPO_ROOT, server.TICKETS_DIR, server.PFX = self._saved
        server.apply_config({"ids": {"prefix": self._saved[2]}})

    def _restore_home(self):
        if self._old_home is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_home

    def post(self, **payload):
        return server.create_ticket(payload)

    def body(self, tid):
        return read(server.scan()[1][tid])

    # ------------------------------------------------------------------ #
    # ticket.create — the id is allocated at write time, never reserved
    # ------------------------------------------------------------------ #
    def test_create_takes_the_next_free_id(self):
        eid, edir = self.repo.resolve_epic("E1")
        tid, path = ticket.create(self.repo, "First one", eid, edir)
        self.assertEqual(tid, "XX-0001")
        self.assertTrue(os.path.isfile(path))
        tid2, _ = ticket.create(self.repo, "Second one", eid, edir)
        self.assertEqual(tid2, "XX-0002")

    def test_create_steps_over_an_id_taken_since_the_form_opened(self):
        """The heart of it: the form previews an id, an agent files a ticket
        under that number, and the save must still succeed — one number up."""
        eid, edir = self.repo.resolve_epic("E1")
        previewed = self.repo.next_id(self.repo.cards()[0])
        self.assertEqual(previewed, "XX-0001")

        # ... an agent gets there first, under a different title (and so a
        # different filename — the clash is on the id, not the path).
        ticket.create(self.repo, "Agent got here first", eid, edir)

        tid, path = ticket.create(self.repo, "Mine, saved late", eid, edir)
        self.assertEqual(tid, "XX-0002")
        self.assertIn("XX-0002", os.path.basename(path))
        self.assertEqual(len(self.repo.cards()[0]) - 2, 2)   # 2 epics + 2 tickets

    def test_create_never_overwrites_an_existing_file(self):
        eid, edir = self.repo.resolve_epic("E1")
        tid, path = ticket.create(self.repo, "Only me", eid, edir)
        before = read(path)
        # A second ticket with the same title would want the same filename stem;
        # it must take a new id rather than clobber the first.
        tid2, path2 = ticket.create(self.repo, "Only me", eid, edir)
        self.assertNotEqual(tid, tid2)
        self.assertNotEqual(path, path2)
        self.assertEqual(read(path), before)

    def test_create_rejects_a_demanded_id_that_is_taken(self):
        eid, edir = self.repo.resolve_epic("E1")
        ticket.create(self.repo, "Taken", eid, edir, tid="XX-0007")
        with self.assertRaises(ValueError):
            ticket.create(self.repo, "Also 7", eid, edir, tid="XX-0007")

    def test_context_blurb_replaces_the_placeholder(self):
        eid, edir = self.repo.resolve_epic("E1")
        _tid, path = ticket.create(self.repo, "With context", eid, edir,
                                   context="Because the board was hard to reach.")
        text = read(path)
        self.assertIn("Because the board was hard to reach.", text)
        self.assertNotIn("_Why this ticket exists", text)
        # the rest of the scaffold survives
        self.assertIn("## Acceptance criteria", text)

    def test_blank_context_keeps_the_scaffold(self):
        eid, edir = self.repo.resolve_epic("E1")
        _tid, path = ticket.create(self.repo, "No context", eid, edir, context="   ")
        self.assertIn("_Why this ticket exists", read(path))

    # ------------------------------------------------------------------ #
    # the form's data
    # ------------------------------------------------------------------ #
    def test_meta_lists_every_epic_and_previews_the_next_id(self):
        meta = server.new_ticket_meta()
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["nextId"], "XX-0001")
        self.assertEqual([e["id"] for e in meta["epics"]], ["XX-E001", "XX-E002"])
        self.assertEqual(meta["epics"][0]["title"], "First Epic")

    # ------------------------------------------------------------------ #
    # POST /api/newticket
    # ------------------------------------------------------------------ #
    def test_post_creates_a_ticket_with_the_defaults(self):
        code, out = self.post(title="  Spaced   out  title ", epic="E1")
        self.assertEqual(code, 200)
        self.assertEqual(out["id"], "XX-0001")
        self.assertEqual(out["url"], "/ticket/XX-0001")
        text = self.body("XX-0001")
        self.assertIn("title: Spaced out title", text)      # whitespace collapsed
        self.assertIn("status: OPEN", text)
        self.assertIn("epic: XX-E001", text)

    def test_post_honours_every_field(self):
        self.post(title="A dependency", epic="E1")
        code, out = self.post(title="The real one", epic="E2", priority=1,
                              risk="HIGH", effort="1d", context="A reason.",
                              dependsOn="XX-0001")
        self.assertEqual(code, 200)
        text = self.body(out["id"])
        self.assertIn("epic: XX-E002", text)
        self.assertIn("priority: 1", text)
        self.assertIn("risk: HIGH", text)
        self.assertIn("effort: 1d", text)
        self.assertIn("depends_on: [XX-0001]", text)
        self.assertIn("A reason.", text)

    def test_post_rejects_what_the_user_can_fix(self):
        for payload in ({"title": "", "epic": "E1"},
                        {"title": "x", "epic": "E999"},
                        {"title": "x", "epic": "E1", "risk": "SPICY"},
                        {"title": "x", "epic": "E1", "priority": 9},
                        {"title": "x", "epic": "E1", "priority": "high"},
                        {"title": "x", "epic": "E1", "effort": "banana"},
                        {"title": "x", "epic": "E1", "dependsOn": "XX-9999"}):
            code, out = self.post(**payload)
            self.assertEqual(code, 400, payload)
            self.assertFalse(out["ok"])
            self.assertTrue(out["error"])

    def test_post_records_a_new_event(self):
        from interfacile import events
        code, out = self.post(title="Logged", epic="E1")
        self.assertEqual(code, 200)
        kinds = [(e["kind"], e["id"]) for e in events.load()["recent"]]
        self.assertIn(("new", out["id"]), kinds)

    # ------------------------------------------------------------------ #
    # promoting a to-do item (IF-0103)
    # ------------------------------------------------------------------ #
    def test_promoting_a_todo_item_ticks_and_links_it(self):
        server.save_note("todo", "- [ ] feed the cat\n- [ ] leave me alone\n")
        code, out = self.post(title="Feed the cat", epic="E1", todoText="feed the cat")
        self.assertEqual(code, 200)
        todo = server.load_note("todo")
        self.assertEqual(todo, "- [x] feed the cat (%s)\n- [ ] leave me alone\n" % out["id"])
        # and the pop-out is handed the rewritten file, so it needs no reload
        self.assertEqual(out["todo"], todo)

    def test_an_item_that_already_names_a_ticket_gains_the_new_one(self):
        """`(XX-0001)` becomes `(XX-0001, XX-0002)` — not a second group."""
        self.post(title="The first one", epic="E1")            # XX-0001
        server.save_note("todo", "- [ ] two birds (XX-0001)\n")
        code, out = self.post(title="Two birds", epic="E1",
                              todoText="two birds (XX-0001)")
        self.assertEqual(code, 200)
        self.assertEqual(server.load_note("todo"),
                         "- [x] two birds (XX-0001, %s)\n" % out["id"])

    def test_a_ticket_filed_from_the_board_touches_no_todo(self):
        server.save_note("todo", "- [ ] untouched\n")
        code, _out = self.post(title="Nothing to do with the list", epic="E1")
        self.assertEqual(code, 200)
        self.assertEqual(server.load_note("todo"), "- [ ] untouched\n")

    def test_a_failed_create_leaves_the_item_alone(self):
        """The item is only ticked off by the same request that wrote the ticket,
        so there is no window where the note claims a ticket that does not exist."""
        server.save_note("todo", "- [ ] still mine\n")
        code, out = self.post(title="Doomed", epic="E999", todoText="still mine")
        self.assertEqual(code, 400)
        self.assertFalse(out["ok"])
        self.assertEqual(server.load_note("todo"), "- [ ] still mine\n")

    def test_an_item_that_moved_on_is_not_ticked_by_mistake(self):
        """Matched on text, not position: if the item is gone by the time the form
        is saved, the ticket is still made and no other line is ticked."""
        server.save_note("todo", "- [ ] a different item\n")
        code, out = self.post(title="Vanished", epic="E1", todoText="the one I opened")
        self.assertEqual(code, 200)
        self.assertIsNone(out["todo"])
        self.assertEqual(server.load_note("todo"), "- [ ] a different item\n")

    def test_created_ticket_passes_lint(self):
        """A ticket the form made is a ticket the standard accepts — no hand
        repair between saving it and the board being clean."""
        self.post(title="A dependency", epic="E1")
        code, _out = self.post(title="Lint me", epic="E2", priority=1, risk="HIGH",
                               effort="1d", context="Because.", dependsOn="XX-0001")
        self.assertEqual(code, 200)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ticket.cmd_lint(SimpleNamespace(repo=self.root, json=True))
        report = json.loads(out.getvalue())
        self.assertEqual(report["errors"], [], out.getvalue())
        self.assertEqual(report["warnings"], [], out.getvalue())
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()

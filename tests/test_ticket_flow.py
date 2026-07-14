"""The ticket flow, end to end, against a throwaway repo.

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


def run(func, **kw):
    """Call one cmd_* with captured output. Returns (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = func(SimpleNamespace(**kw))
    return rc, out.getvalue(), err.getvalue()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class RepoCase(unittest.TestCase):
    """A fresh XX-prefixed repo with one epic per test."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ifc-test-")
        self.addCleanup(shutil.rmtree, self.root, True)
        # Ticket commands record interactions in the hub-wide event log under
        # XDG_CONFIG_HOME. Point it at a temp dir: a test must never write into
        # the counters the developer's own automations are watching.
        self.home = tempfile.mkdtemp(prefix="ifc-test-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self._old_home = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.home
        self.addCleanup(self._restore_home)
        write(os.path.join(self.root, ".interfacile", "config.json"),
              json.dumps({"ids": {"prefix": "XX", "digits": 4}}))
        self.epic_dir = os.path.join(self.root, "tickets", "XX-E001-first-epic")
        write(os.path.join(self.epic_dir, "XX-E001-first-epic.md"),
              "---\nid: XX-E001\ntitle: First Epic\nstatus: OPEN\n"
              "index_exempt: true\n---\n\n# XX-E001\n")
        os.makedirs(os.path.join(self.epic_dir, "open"), exist_ok=True)
        os.makedirs(os.path.join(self.epic_dir, "closed"), exist_ok=True)

    def _restore_home(self):
        if self._old_home is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_home

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def new(self, title, **kw):
        args = dict(repo=self.root, title=title, epic="E1", id=None, risk="LOW",
                    priority=3, effort="2h", depends_on="", dry_run=False)
        args.update(kw)
        return run(ticket.cmd_new, **args)

    def close(self, tid, note=None, force=False):
        return run(ticket.cmd_close, repo=self.root, ticket_id=tid,
                   note=note, force=force)

    def ready_json(self):
        rc, out, _ = run(ticket.cmd_ready, repo=self.root, epic=None, json=True)
        self.assertEqual(rc, 0)
        return json.loads(out)

    def lint(self, as_json=False):
        return run(ticket.cmd_lint, repo=self.root, json=as_json)

    def ticket_path(self, bay, name):
        return os.path.join(self.epic_dir, bay, name)

    def read(self, bay, name):
        with open(self.ticket_path(bay, name), encoding="utf-8") as fh:
            return fh.read()


class TestNew(RepoCase):
    def test_creates_with_next_id_and_frontmatter(self):
        rc, out, _ = self.new("Ship the widget", priority=1, effort="4h")
        self.assertEqual(rc, 0)
        self.assertIn("XX-0001 created", out)
        text = self.read("open", "XX-0001-ship-the-widget.md")
        for line in ("id: XX-0001", "title: Ship the widget", "epic: XX-E001",
                     "status: OPEN", "priority: 1", "effort: 4h"):
            self.assertIn(line, text)

        rc, out, _ = self.new("Second")
        self.assertEqual(rc, 0)
        self.assertIn("XX-0002 created", out)

    def test_epic_accepted_in_any_spelling(self):
        for spelling in ("E1", "E001", "XX-E001", "XX-E001-first-epic"):
            rc, _, err = self.new("For %s" % spelling, epic=spelling)
            self.assertEqual(rc, 0, "epic %r rejected: %s" % (spelling, err))

    def test_unknown_epic_and_duplicate_id_are_rejected(self):
        rc, _, err = self.new("Nope", epic="E9")
        self.assertEqual(rc, 1)
        self.assertIn("unknown epic", err)

        self.new("First")
        rc, _, err = self.new("Clash", id="XX-0001")
        self.assertEqual(rc, 1)
        self.assertIn("already exists", err)

    def test_unknown_dependency_is_rejected(self):
        rc, _, err = self.new("Needs ghost", depends_on="XX-0999")
        self.assertEqual(rc, 1)
        self.assertIn("unknown ticket", err)

    def test_dry_run_writes_nothing(self):
        rc, out, _ = self.new("Maybe", dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("[dry-run]", out)
        self.assertFalse(os.path.exists(self.ticket_path(
            "open", "XX-0001-maybe.md")))


class TestLifecycle(RepoCase):
    def setUp(self):
        super().setUp()
        self.new("Foundation", priority=1)                    # XX-0001
        self.new("Tower", depends_on="XX-0001", priority=2)   # XX-0002

    def test_blocked_is_derived_not_a_status(self):
        ready = self.ready_json()
        self.assertEqual([t["id"] for t in ready], ["XX-0001"])

        rc, out, _ = run(ticket.cmd_tickets, repo=self.root, epic=None,
                         all=False, json=True)
        tower = [t for t in json.loads(out) if t["id"] == "XX-0002"][0]
        self.assertEqual(tower["status"], "OPEN")
        self.assertEqual(tower["blocked_by"], ["XX-0001"])

    def test_close_refuses_while_blocked_then_unblocks(self):
        rc, _, err = self.close("XX-0002")
        self.assertEqual(rc, 1)
        self.assertIn("still waits on XX-0001", err)

        rc, out, _ = self.close("XX-0001", note="poured the concrete")
        self.assertEqual(rc, 0)
        self.assertIn("now unblocked: XX-0002", out)
        text = self.read("closed", "XX-0001-foundation.md")
        self.assertIn("status: CLOSED", text)
        self.assertIn("closed: ", text)
        self.assertIn("index_note: poured the concrete", text)
        self.assertEqual([t["id"] for t in self.ready_json()], ["XX-0002"])

        rc, out, _ = self.close("XX-0001")
        self.assertEqual(rc, 1)          # already closed

    def test_reopen_reverses_a_close(self):
        self.close("XX-0001", note="done")
        rc, out, _ = run(ticket.cmd_reopen, repo=self.root, ticket_id="XX-0001")
        self.assertEqual(rc, 0)
        text = self.read("open", "XX-0001-foundation.md")
        self.assertIn("status: OPEN", text)
        self.assertNotIn("closed:", text)
        self.assertNotIn("index_note:", text)
        self.assertEqual([t["id"] for t in self.ready_json()], ["XX-0001"])

        rc, _, _ = run(ticket.cmd_reopen, repo=self.root, ticket_id="XX-0001")
        self.assertEqual(rc, 1)          # not closed

    def test_drop_records_why_and_satisfies_dependencies(self):
        rc, out, _ = run(ticket.cmd_drop, repo=self.root, ticket_id="XX-0001",
                         why="obsoleted by the new design")
        self.assertEqual(rc, 0)
        self.assertIn("now unblocked: XX-0002", out)
        text = self.read("closed", "XX-0001-foundation.md")
        self.assertIn("status: WONT_FIX", text)
        self.assertIn("index_note: obsoleted by the new design", text)
        self.assertEqual([t["id"] for t in self.ready_json()], ["XX-0002"])

    def test_ids_are_found_case_insensitively_and_without_prefix(self):
        for spelling in ("XX-0001", "xx-0001", "0001"):
            rc, out, _ = run(ticket.cmd_show, repo=self.root,
                             ticket_id=spelling, json=True)
            self.assertEqual(rc, 0, spelling)
            self.assertEqual(json.loads(out)["id"], "XX-0001")

    def test_show_json_carries_frontmatter_and_body(self):
        rc, out, _ = run(ticket.cmd_show, repo=self.root, ticket_id="XX-0001",
                         json=True)
        data = json.loads(out)
        self.assertEqual(data["frontmatter"]["epic"], "XX-E001")
        self.assertIn("## Acceptance criteria", data["body"])


class TestLint(RepoCase):
    def test_clean_tree_lints_clean(self):
        self.new("Fine")
        rc, out, _ = self.lint()
        self.assertEqual(rc, 0)
        self.assertIn("✓ clean", out)

    def test_broken_tickets_are_caught(self):
        write(self.ticket_path("open", "XX-0002-broken.md"),
              "---\nid: XX-0002\ntitle: Broken\nepic: XX-E001\n"
              "status: MAYBE\ncreated: not-a-date\n"
              "depends_on: [XX-0777]\n---\n")
        rc, out, _ = self.lint(as_json=True)
        self.assertEqual(rc, 1)
        checks = {e["check"] for e in json.loads(out)["errors"]}
        self.assertLessEqual({"status", "dates", "depends_on"}, checks)

    def test_closed_without_date_and_duplicates_are_errors(self):
        write(self.ticket_path("closed", "XX-0003-done.md"),
              "---\nid: XX-0003\ntitle: Done\nepic: XX-E001\nstatus: CLOSED\n"
              "created: 2026-01-01\n---\n")
        write(self.ticket_path("open", "XX-0003-twin.md"),
              "---\nid: XX-0003\ntitle: Twin\nepic: XX-E001\nstatus: OPEN\n"
              "created: 2026-01-01\n---\n")
        rc, out, _ = self.lint(as_json=True)
        self.assertEqual(rc, 1)
        checks = {e["check"] for e in json.loads(out)["errors"]}
        self.assertLessEqual({"closed", "duplicate"}, checks)

    def test_circular_dependency_is_an_error(self):
        self.new("A")
        self.new("B", depends_on="XX-0001")
        text = self.read("open", "XX-0001-a.md").replace(
            "created:", "depends_on: [XX-0002]\ncreated:")
        write(self.ticket_path("open", "XX-0001-a.md"), text)
        rc, out, _ = self.lint(as_json=True)
        self.assertEqual(rc, 1)
        checks = {e["check"] for e in json.loads(out)["errors"]}
        self.assertIn("deps", checks)


class TestTodo(RepoCase):
    """`interfacile todo` — the on-ramp the capture-ticket skill drives."""

    def setUp(self):
        super(TestTodo, self).setUp()
        self.path = os.path.join(self.root, ".interfacile", "todo.md")

    def todo(self, action="list", n=None, **kw):
        args = dict(repo=self.root, action=action, n=n, ticket=None,
                    all=False, json=False)
        args.update(kw)
        return run(ticket.cmd_todo, **args)

    def read(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def seed(self, text="- [x] shipped\n- [ ] first\n- [ ] second\n"):
        write(self.path, text)

    def test_parse_and_serialize_round_trip(self):
        text = "- [x] done one\n- [ ] open one\n"
        self.assertEqual(server.serialize_todo(server.parse_todo(text)), text)

    def test_parse_tolerates_bare_lines_and_normalises_them(self):
        items = server.parse_todo("pasted line\n* [X] starred done\n\n- dashed\n")
        self.assertEqual([i["done"] for i in items], [False, True, False])
        self.assertEqual([i["text"] for i in items],
                         ["pasted line", "starred done", "dashed"])
        self.assertEqual(server.serialize_todo(items),
                         "- [ ] pasted line\n- [x] starred done\n- [ ] dashed\n")

    def test_list_hides_done_until_asked(self):
        self.seed()
        rc, out, _ = self.todo()
        self.assertEqual(rc, 0)
        self.assertNotIn("shipped", out)
        self.assertIn("first", out)
        self.assertIn("2 open · 1 done", out)
        self.assertIn("shipped", self.todo(all=True)[1])

    def test_json_numbers_are_stable_across_done_items(self):
        self.seed()
        payload = json.loads(self.todo(json=True)[1])
        self.assertEqual([i["n"] for i in payload], [2, 3])   # 1 is done
        self.assertEqual(payload[0]["text"], "first")

    def test_done_ticks_one_item_and_leaves_the_rest_alone(self):
        self.seed()
        rc, out, _ = self.todo("done", 2)
        self.assertEqual(rc, 0)
        self.assertIn("first", out)
        self.assertEqual(self.read(), "- [x] shipped\n- [x] first\n- [ ] second\n")

    def test_done_with_ticket_links_the_item_and_never_double_appends(self):
        self.seed()
        self.todo("done", 3, ticket="xx-0007")            # id is normalised
        self.assertIn("- [x] second (XX-0007)\n", self.read())
        rc, out, _ = self.todo("done", 3, ticket="XX-0007")
        self.assertEqual(rc, 0)
        self.assertEqual(self.read().count("XX-0007"), 1)
        self.assertIn("•", out)                           # already done, no-op

    def test_done_on_a_missing_item_fails_and_writes_nothing(self):
        self.seed()
        before = self.read()
        self.assertEqual(self.todo("done", 9)[0], 1)
        self.assertEqual(self.todo("done", 0)[0], 1)
        self.assertEqual(self.todo("done")[0], 1)         # no number at all
        self.assertEqual(self.read(), before)

    def test_no_list_yet_is_not_an_error(self):
        rc, out, _ = self.todo()
        self.assertEqual(rc, 0)
        self.assertIn("no to-do list yet", out)
        self.assertEqual(json.loads(self.todo(json=True)[1]), [])

    def test_a_second_ticket_extends_the_reference(self):
        repo = ticket.Repo(self.root)

        def link(text, tid):
            return ticket.link_todo_text(repo, text, tid)

        self.assertEqual(link("write the docs", "XX-0001"),
                         "write the docs (XX-0001)")
        self.assertEqual(link("write the docs (XX-0001)", "XX-0002"),
                         "write the docs (XX-0001, XX-0002)")
        self.assertEqual(link("write the docs (XX-0001, XX-0002)", "XX-0001"),
                         "write the docs (XX-0001, XX-0002)")

    def test_ids_follow_the_repo_scheme_not_a_guess(self):
        """The prefix and width come from config — `AB-12` is not an id here."""
        repo = ticket.Repo(self.root)                      # XX, 4 digits
        self.assertEqual(repo.link_re.findall("XX-0001 and AB-12 and XX-9"),
                         ["XX-0001"])


class TestScratch(RepoCase):
    """`interfacile scratch` — capture from prose, annotated never cut."""

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.root, ".interfacile", "scratchpad.md")
        write(self.path, "# notes\n\nthe closed view is a mess\n"
                         "needs grouping by epic\n\nask about the bill\n")

    def scratch(self, action="list", n=None, **kw):
        args = dict(repo=self.root, action=action, n=n, ticket=None, json=False)
        args.update(kw)
        return run(ticket.cmd_scratch, **args)

    def read(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def test_blocks_are_runs_of_non_blank_lines(self):
        payload = json.loads(self.scratch(json=True)[1])
        self.assertEqual([b["n"] for b in payload], [1, 2, 3])
        self.assertEqual(payload[1]["text"],
                         "the closed view is a mess\nneeds grouping by epic")

    def test_link_annotates_the_last_line_and_nothing_else(self):
        rc, _, _ = self.scratch("link", 2, ticket="xx-0007")   # id normalised
        self.assertEqual(rc, 0)
        self.assertEqual(self.read(),
                         "# notes\n\nthe closed view is a mess\n"
                         "needs grouping by epic → XX-0007\n\nask about the bill\n")

    def test_a_second_ticket_extends_the_arrow_and_repeats_are_no_ops(self):
        self.scratch("link", 3, ticket="XX-0001")
        self.scratch("link", 3, ticket="XX-0002")
        self.assertIn("ask about the bill → XX-0001, XX-0002\n", self.read())
        before = self.read()
        rc, out, _ = self.scratch("link", 3, ticket="XX-0002")
        self.assertEqual((rc, self.read()), (0, before))
        self.assertIn("•", out)

    def test_link_preserves_a_file_with_no_trailing_newline(self):
        write(self.path, "one\n\ntwo")
        self.scratch("link", 2, ticket="XX-0009")
        self.assertEqual(self.read(), "one\n\ntwo → XX-0009")

    def test_link_needs_a_block_and_a_ticket(self):
        before = self.read()
        self.assertEqual(self.scratch("link", 9, ticket="XX-0001")[0], 1)
        self.assertEqual(self.scratch("link", 1)[0], 1)     # no --ticket
        self.assertEqual(self.read(), before)

    def test_an_id_the_repo_could_never_resolve_is_refused(self):
        before = self.read()
        rc, _, err = self.scratch("link", 1, ticket="nonsense")
        self.assertEqual((rc, self.read()), (1, before))    # nothing written
        self.assertIn("XX-0000", err)                       # says what it wants

    def test_an_empty_scratchpad_is_not_an_error(self):
        os.remove(self.path)
        rc, out, _ = self.scratch()
        self.assertEqual(rc, 0)
        self.assertIn("nothing in the scratchpad", out)


class TestCaptureTracksItsTicket(RepoCase):
    """A note stores an id; the status is resolved from the board, every time."""

    def test_listings_resolve_the_referenced_ticket(self):
        self.new("Real ticket")                            # XX-0001
        write(os.path.join(self.root, ".interfacile", "todo.md"),
              "- [x] shipped thing (XX-0001)\n- [x] ghost (XX-0404)\n")
        payload = json.loads(run(ticket.cmd_todo, repo=self.root, action="list",
                                 n=None, ticket=None, all=True, json=True)[1])
        self.assertEqual(payload[0]["tickets"],
                         [{"id": "XX-0001", "status": "OPEN",
                           "title": "Real ticket"}])
        self.assertEqual(payload[1]["tickets"][0]["status"], "unknown")

        self.close("XX-0001", note="done")
        out = run(ticket.cmd_todo, repo=self.root, action="list", n=None,
                  ticket=None, all=True, json=False)[1]
        self.assertIn("XX-0001 CLOSED", out)               # read live, not stored
        self.assertIn("XX-0404 unknown", out)


class TestIdScheme(unittest.TestCase):
    def test_prefix_inferred_from_ticket_files_else_folder_name(self):
        root = tempfile.mkdtemp(prefix="ifc-test-")
        self.addCleanup(shutil.rmtree, root, True)
        self.assertIsNone(ticket.infer_prefix(root))
        write(os.path.join(root, "tickets", "QQ-E001-x", "open",
                           "QQ-0001-y.md"), "---\nid: QQ-0001\n---\n")
        self.assertEqual(ticket.infer_prefix(root), "QQ")

    def test_fallback_prefix_uses_initials(self):
        self.assertEqual(ticket.fallback_prefix("/x/Andys Automates"), "AA")
        self.assertEqual(ticket.fallback_prefix("/x/widget"), "WI")

    def test_parse_ids_accepts_every_spelling(self):
        for raw in ("[A-1, B-2]", "A-1, B-2", "A-1 B-2"):
            self.assertEqual(ticket.parse_ids(raw), ["A-1", "B-2"])
        self.assertEqual(ticket.parse_ids(""), [])

    def test_unquote_tolerates_hand_quoted_values(self):
        self.assertEqual(ticket.unquote('"quoted"'), "quoted")
        self.assertEqual(ticket.unquote("plain: text"), "plain: text")


if __name__ == "__main__":
    unittest.main()

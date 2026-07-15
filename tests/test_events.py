"""The hub-wide interaction log: counts, cursor, and never getting in the way."""
import os
import shutil
import tempfile
import unittest

from interfacile import events


class EventsCase(unittest.TestCase):
    def setUp(self):
        # The log lives under XDG_CONFIG_HOME — point it at a temp dir so a
        # test can never touch the developer's real one.
        self.home = tempfile.mkdtemp(prefix="ifc-events-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.home
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old

    def test_empty_log_reads_as_zero(self):
        log = events.load()
        self.assertEqual(log["seq"], 0)
        self.assertEqual(log["counts"], {})
        self.assertEqual(events.counts(), {})
        self.assertEqual(events.since(0), [])

    def test_record_counts_globally_and_per_interface(self):
        events.record("close", ticket="EM-1", interface="clean-paste", repo="/a")
        events.record("close", ticket="TK-9", interface="th-tools", repo="/b")
        events.record("pin", ticket="EM-2", interface="clean-paste", repo="/a")
        # hub-wide: a close is a close, whichever project it happened in
        self.assertEqual(events.counts(), {"close": 2, "pin": 1})
        self.assertEqual(events.counts("clean-paste"), {"close": 1, "pin": 1})
        self.assertEqual(events.counts("th-tools"), {"close": 1})
        self.assertEqual(events.counts("nobody"), {})

    def test_seq_is_a_cursor_an_automation_can_poll(self):
        first = events.record("review", ticket="EM-1", interface="cp")
        events.record("unreview", ticket="EM-1", interface="cp")
        third = events.record("close", ticket="EM-2", interface="cp")
        self.assertEqual([first["seq"], third["seq"]], [1, 3])
        after = events.since(first["seq"])
        self.assertEqual([e["kind"] for e in after], ["unreview", "close"])
        self.assertEqual(events.since(third["seq"]), [])   # caught up
        self.assertEqual(events.load()["seq"], 3)

    def test_event_carries_what_an_automation_needs(self):
        ev = events.record("close", ticket="EM-7", interface="cp", repo="/repo")
        self.assertEqual(ev["kind"], "close")
        self.assertEqual(ev["id"], "EM-7")
        self.assertEqual(ev["interface"], "cp")
        self.assertEqual(ev["repo"], "/repo")
        self.assertIn("at", ev)

    def test_recent_is_capped_but_counts_are_not(self):
        for _ in range(events.MAX_RECENT + 5):
            events.record("pin", ticket="EM-1", interface="cp")
        log = events.load()
        self.assertEqual(len(log["recent"]), events.MAX_RECENT)
        self.assertEqual(log["counts"]["pin"], events.MAX_RECENT + 5)
        self.assertEqual(log["seq"], events.MAX_RECENT + 5)

    def test_cli_close_lands_in_the_hub_wide_log(self):
        # the whole point: an automation watching for "a ticket closed" hears
        # it whether the close came from a dashboard click or `interfacile close`
        import json as _json
        import shutil as _shutil
        import tempfile as _tempfile
        from types import SimpleNamespace
        from interfacile import ticket

        root = _tempfile.mkdtemp(prefix="ifc-ev-repo-")
        self.addCleanup(_shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, ".interfacile"), exist_ok=True)
        with open(os.path.join(root, ".interfacile", "config.json"), "w",
                  encoding="utf-8") as fh:
            _json.dump({"ids": {"prefix": "XX", "digits": 4}}, fh)
        epic = os.path.join(root, "tickets", "XX-E001-e")
        os.makedirs(os.path.join(epic, "open"), exist_ok=True)
        os.makedirs(os.path.join(epic, "closed"), exist_ok=True)
        with open(os.path.join(epic, "XX-E001-e.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nid: XX-E001\ntitle: E\nstatus: OPEN\n"
                     "index_exempt: true\n---\n\n# XX-E001\n")

        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            ticket.cmd_new(SimpleNamespace(
                repo=root, title="A thing", epic="E1", id=None, risk="LOW",
                priority=3, effort="2h", depends_on="", dry_run=False))
            ticket.cmd_close(SimpleNamespace(
                repo=root, ticket_id="XX-0001", force=False, note=None))

        self.assertEqual(events.counts(), {"new": 1, "close": 1})
        closes = [e for e in events.since(0) if e["kind"] == "close"]
        self.assertEqual(closes[0]["id"], "XX-0001")
        self.assertEqual(closes[0]["repo"], root)

    def test_a_corrupt_log_never_breaks_the_interaction(self):
        os.makedirs(os.path.dirname(events.events_file()), exist_ok=True)
        with open(events.events_file(), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(events.load()["seq"], 0)      # reads as empty
        self.assertIsNotNone(events.record("close", ticket="EM-1"))
        self.assertEqual(events.counts(), {"close": 1})   # and recovers


class InterfaceNameCase(unittest.TestCase):
    """The CLI and the server must name an interface the same way, or one
    project's events end up split across two keys and every per-interface
    count under-reports."""

    def test_slug_is_url_safe_and_stable(self):
        from interfacile import server
        self.assertEqual(server.iface_slug("/x/clean_paste_lite"), "clean-paste-lite")
        self.assertEqual(server.iface_slug("/x/My Project"), "my-project")
        self.assertEqual(server.iface_slug("/x/interfacile/"), "interfacile")
        self.assertEqual(server.iface_slug("/x/___"), "iface")

    def test_cli_and_server_agree_on_a_name_with_an_underscore(self):
        """The regression: `clean_paste_lite` logged as itself from the terminal
        and as `clean-paste-lite` from the dashboard."""
        from interfacile import server
        from interfacile import ticket
        root = "/tmp/whatever/clean_paste_lite"
        cli = server.iface_slug(root)                       # what ticket._log writes
        served = server._slugify(os.path.basename(root), set())   # what the hub indexes
        self.assertEqual(cli, served)
        self.assertEqual(cli, "clean-paste-lite")
        self.assertNotEqual(cli, os.path.basename(root))    # and not the raw folder


if __name__ == "__main__":
    unittest.main()

"""What a ticket page says about being blocked — and that it agrees with the board.

Standard library only — run with either:

    python -m unittest discover tests
    pytest tests
"""
import json
import os
import shutil
import tempfile
import unittest

from interfacile import server


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def graph(nodes, edges):
    """A dep graph the way scan() hands one to the ticket page."""
    return {"nodes": {i: {"id": i, "status": st} for i, st in nodes.items()},
            "edges": [list(e) for e in edges]}


class DepButtonCase(unittest.TestCase):
    """A blocker that is CLOSED is a blocker that is *satisfied*. Counting edges
    instead of open blockers had a ready ticket reading `blocked by 3` (IF-0104)."""

    def button(self, nodes, edges, tid="XX-0003"):
        return server._dep_button(tid, graph(nodes, edges))

    def test_an_open_blocker_still_blocks(self):
        out = self.button({"XX-0001": "OPEN", "XX-0003": "OPEN"},
                          [("XX-0001", "XX-0003")])
        self.assertIn("blocked by 1", out)
        self.assertIn("tb dep blocked", out)

    def test_only_the_open_blockers_are_counted(self):
        out = self.button({"XX-0001": "CLOSED", "XX-0002": "OPEN",
                           "XX-0003": "OPEN"},
                          [("XX-0001", "XX-0003"), ("XX-0002", "XX-0003")])
        self.assertIn("blocked by 1", out)          # not 2
        self.assertNotIn("unblocked", out)

    def test_all_blockers_closed_reads_as_unblocked(self):
        """The regression: three closed blockers used to read as `blocked by 3`."""
        out = self.button({"XX-0001": "CLOSED", "XX-0002": "CLOSED",
                           "XX-0003": "OPEN"},
                          [("XX-0001", "XX-0003"), ("XX-0002", "XX-0003")])
        self.assertNotIn("blocked by", out)
        self.assertIn("unblocked", out)
        self.assertIn("all 2 cleared", out)
        self.assertIn("tb dep clear", out)

    def test_a_wont_fix_blocker_satisfies_the_dependency(self):
        """Exactly as `interfacile ready` treats it — DONE is CLOSED or WONT_FIX."""
        out = self.button({"XX-0001": "WONT_FIX", "XX-0003": "OPEN"},
                          [("XX-0001", "XX-0003")])
        self.assertNotIn("blocked by", out)
        self.assertIn("unblocked", out)

    def test_a_ticket_that_never_had_blockers_is_not_dressed_as_good_news(self):
        out = self.button({"XX-0003": "OPEN", "XX-0004": "OPEN"},
                          [("XX-0003", "XX-0004")])
        self.assertNotIn("unblocked", out)
        self.assertNotIn("blocked by", out)
        self.assertIn("blocks 1", out)              # what it holds up, still said
        self.assertNotIn("tb dep clear", out)

    def test_it_only_counts_the_open_tickets_it_holds_up(self):
        out = self.button({"XX-0003": "OPEN", "XX-0004": "CLOSED", "XX-0005": "OPEN"},
                          [("XX-0003", "XX-0004"), ("XX-0003", "XX-0005")])
        self.assertIn("blocks 1", out)              # not 2 — XX-0004 is done
        self.assertNotIn("blocks 2", out)

    def test_a_ticket_outside_the_graph_gets_no_button(self):
        self.assertEqual(self.button({"XX-0001": "OPEN"}, [], tid="XX-9999"), "")


class IdToolsCase(unittest.TestCase):
    """Copy-the-id and trace-the-dependencies, in one place on every card (IF-0105)."""

    def setUp(self):
        self._saved = server.DEP_IDS
        server.DEP_IDS = {"XX-0001"}
        self.addCleanup(self._restore)

    def _restore(self):
        server.DEP_IDS = self._saved

    def test_a_card_always_offers_copy(self):
        out = server._id_tools("XX-0002")          # not in the graph
        self.assertIn('data-copy="XX-0002"', out)
        self.assertIn("copy-one", out)

    def test_the_dep_control_only_appears_for_tickets_in_the_graph(self):
        self.assertIn("dep-link", server._id_tools("XX-0001"))
        self.assertNotIn("dep-link", server._id_tools("XX-0002"))

    def test_on_a_card_the_dep_control_is_not_an_anchor(self):
        """Cards are one big <a>; a nested <a> is invalid and browsers unnest it,
        which would break the card's own link. Hence role=link + a handler."""
        out = server._id_tools("XX-0001")           # anchor_safe by default
        self.assertNotIn("<a ", out)
        self.assertIn('role="link"', out)
        self.assertIn('data-dep="XX-0001"', out)
        self.assertIn('tabindex="0"', out)

    def test_in_a_table_row_it_is_a_real_link(self):
        """No wrapping anchor there — so it can be middle-clicked and opened in a tab."""
        out = server._id_tools("XX-0001", anchor_safe=False)
        self.assertIn('href="/deps?id=XX-0001"', out)
        self.assertNotIn("data-dep", out)

    def test_the_id_is_escaped_into_the_attribute(self):
        server.DEP_IDS = {'X"Y'}
        out = server._id_tools('X"Y')
        self.assertNotIn('data-copy="X"Y"', out)
        self.assertIn("&quot;", out)


class BoardAgreesWithTicketPageCase(unittest.TestCase):
    """The board and the ticket page must not disagree about the same ticket."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ifc-deps-")
        self.addCleanup(shutil.rmtree, self.root, True)
        write(os.path.join(self.root, ".interfacile", "config.json"),
              json.dumps({"ids": {"prefix": "XX", "digits": 4}}))
        edir = os.path.join(self.root, "tickets", "XX-E001-first")
        write(os.path.join(edir, "XX-E001-first.md"),
              "---\nid: XX-E001\ntitle: First\nstatus: OPEN\nindex_exempt: true\n---\n")

        def card(tid, status, bay, depends=""):
            dep = ("depends_on: [%s]\n" % depends) if depends else ""
            write(os.path.join(edir, bay, "%s-t.md" % tid),
                  "---\nid: %s\ntitle: %s\nepic: XX-E001\nstatus: %s\nrisk: LOW\n"
                  "priority: 3\neffort: 2h\ncreated: 2026-01-01\n%s%s---\n\n# %s\n"
                  % (tid, tid, status, dep,
                     "closed: 2026-01-02\n" if status == "CLOSED" else "", tid))

        card("XX-0001", "CLOSED", "closed")                       # the blocker, done
        card("XX-0002", "OPEN", "open", depends="XX-0001")        # ready now
        card("XX-0003", "OPEN", "open")                           # a live blocker
        card("XX-0004", "OPEN", "open", depends="XX-0003")        # genuinely blocked

        self._saved = (server.REPO_ROOT, server.TICKETS_DIR, server.PFX)
        server.REPO_ROOT = self.root
        server.TICKETS_DIR = os.path.join(self.root, "tickets")
        server.apply_config({"ids": {"prefix": "XX", "digits": 4}})
        self.addCleanup(self._restore)

    def _restore(self):
        server.REPO_ROOT, server.TICKETS_DIR, server.PFX = self._saved
        server.apply_config({"ids": {"prefix": self._saved[2]}})

    def test_the_page_and_the_board_say_the_same_thing(self):
        data, _index = server.scan()
        dep_graph = data["depGraph"]
        blocked = {t["id"]: t["blocked"]
                   for e in data["epics"] for t in e["openTickets"]}

        self.assertFalse(blocked["XX-0002"])       # its only blocker is closed
        self.assertTrue(blocked["XX-0004"])        # its blocker is open

        for tid, is_blocked in blocked.items():
            out = server._dep_button(tid, dep_graph)
            self.assertEqual("blocked by" in out, is_blocked,
                             "%s: page and board disagree — %r" % (tid, out))

        self.assertIn("unblocked", server._dep_button("XX-0002", dep_graph))


if __name__ == "__main__":
    unittest.main()

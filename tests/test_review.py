"""The review tick lives in the ticket: a `## Reviewed ✅` body section."""
import os
import shutil
import tempfile
import unittest

from interfacile import server


TICKET = """---
id: TK-0001
title: A ticket
epic: TK-E001
status: CLOSED
created: 2026-07-01
updated: 2026-07-01
---

# TK-0001 · A ticket

## Context

Something.

## Acceptance criteria

- [x] Done.
"""


class ReviewSectionCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ifc-review-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "TK-0001-a-ticket.md")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(TICKET)

    def read(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def body(self):
        return server.split_frontmatter(self.read())[1]

    def test_round_trip(self):
        self.assertFalse(server.is_reviewed(self.body()))
        server.set_reviewed(self.path, True, when="2026-07-14")
        self.assertTrue(server.is_reviewed(self.body()))
        self.assertIn("## Reviewed ✅", self.read())
        self.assertIn("2026-07-14", self.read())
        server.set_reviewed(self.path, False)
        self.assertFalse(server.is_reviewed(self.body()))
        self.assertNotIn("Reviewed", self.read())
        # the rest of the ticket survives both edits verbatim
        self.assertIn("## Acceptance criteria", self.read())
        self.assertTrue(self.read().endswith("- [x] Done.\n"))

    def test_marking_twice_keeps_one_section(self):
        server.set_reviewed(self.path, True, when="2026-07-13")
        server.set_reviewed(self.path, True, when="2026-07-14")
        self.assertEqual(self.read().count("## Reviewed"), 1)
        self.assertIn("2026-07-14", self.read())
        self.assertNotIn("2026-07-13", self.read())

    def test_removal_stops_at_next_heading(self):
        # a hand-moved section mid-file must not take its neighbours with it
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("\n## Reviewed ✅\n\n2026-07-10\n\n## Notes\n\nKeep me.\n")
        server.set_reviewed(self.path, False)
        self.assertNotIn("## Reviewed", self.read())
        self.assertIn("## Notes\n\nKeep me.\n", self.read())

    def test_plain_heading_and_vs16_variants_detected(self):
        for heading in ("## Reviewed", "## Reviewed ✅️"):
            server.set_reviewed(self.path, False)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write("\n%s\n\n2026-07-14\n" % heading)
            self.assertTrue(server.is_reviewed(self.body()), heading)


if __name__ == "__main__":
    unittest.main()

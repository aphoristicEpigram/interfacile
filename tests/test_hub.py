"""Hub mechanics: positional shortcut keys, registry order persistence,
cross-interface ticket ownership, and document-link rules."""
import json
import os
import shutil
import tempfile
import unittest

from interfacile import server


def make_repo(base, name, prefix=None):
    root = os.path.join(base, name)
    os.makedirs(os.path.join(root, "tickets"), exist_ok=True)
    if prefix:
        os.makedirs(os.path.join(root, ".interfacile"), exist_ok=True)
        with open(os.path.join(root, ".interfacile", "config.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"ids": {"prefix": prefix}}, fh)
    return root


class TestPositionalKeys(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ifc-test-")
        self.addCleanup(shutil.rmtree, self.base, True)

    def test_first_ten_get_keys_in_order(self):
        roots = [make_repo(self.base, "repo-%02d" % i) for i in range(12)]
        server.build_registry(roots)
        keys = [it.shortcut for it in server.INTERFACES]
        self.assertEqual(keys[:10], list("1234567890"))
        self.assertEqual(keys[10:], ["", ""])

    def test_owner_interface_resolves_by_prefix(self):
        server.build_registry([make_repo(self.base, "alpha", "AA"),
                               make_repo(self.base, "beta", "BB")])
        server.ACTIVE_SLUG = server.INTERFACES[0].slug
        owner = server._owner_interface("BB-0042")
        self.assertEqual(owner.slug, "beta")
        # The active interface's own ids never bounce, nor unknown prefixes.
        self.assertIsNone(server._owner_interface("AA-0001"))
        self.assertIsNone(server._owner_interface("ZZ-0001"))
        self.assertIsNone(server._owner_interface("not-an-id"))


class TestRegistryOrder(unittest.TestCase):
    def test_save_keeps_unserved_repos_and_extra_keys(self):
        base = tempfile.mkdtemp(prefix="ifc-test-")
        self.addCleanup(shutil.rmtree, base, True)
        path = os.path.join(base, "registry.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"repos": ["/a", "/b", "/no-tickets"],
                       "other": "kept"}, fh)

        server.save_registry_order(path, ["/b", "/a"])
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["repos"], ["/b", "/a", "/no-tickets"])
        self.assertEqual(data["other"], "kept")


class TestDocRules(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ifc-test-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self._repo_root = server.REPO_ROOT
        self._rules = server.DOC_RULES
        server.REPO_ROOT = self.root
        self.addCleanup(self._restore)

    def _restore(self):
        server.REPO_ROOT = self._repo_root
        server.DOC_RULES = self._rules

    def doc(self, rel, text="# Title\n"):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_config_rules_are_sanitized(self):
        server.apply_config({"documents": [
            {"prefix": "pr", "dir": "docs/product/", "title": "Product reqs"},
            {"prefix": "ADR", "dir": "x"},          # built-in already
            {"prefix": "B AD", "dir": "y"},         # malformed prefix
            {"prefix": "UP", "dir": "../escape"},   # path traversal
            "not-a-dict",
        ]})
        self.assertEqual(server.DOC_RULES, [{"prefix": "PR",
                                             "dir": "docs/product",
                                             "title": "Product reqs"}])
        server.apply_config({})
        self.assertEqual(server.DOC_RULES, [])

    def test_mentions_link_to_the_doc_and_collisions_to_the_index(self):
        self.doc("docs/product/PR-001-spec.md")
        self.doc("docs/product/2026/PR-002-a.md")
        self.doc("docs/product/2026/PR-003-x.md")
        self.doc("docs/product/PR-003-y.md")       # duplicate number
        server.DOC_RULES = [{"prefix": "PR", "dir": "docs/product"}]

        out = server.autolink_ids("see PR-001, PR-002, PR-003 and PR-999")
        self.assertIn('<a href="/doc/docs/product/PR-001-spec.md">PR-001</a>', out)
        self.assertIn('<a href="/doc/docs/product/2026/PR-002-a.md">PR-002</a>', out)
        self.assertIn("and PR-999", out)                     # unknown: untouched
        self.assertIn('<a href="/docs/PR">PR-003</a>', out)  # collision: index

    def test_series_index_parses_title_status_date(self):
        self.doc("docs/product/PR-007-streaming.md",
                 "# PR-007: Streaming\n\n**Status:** Accepted\n"
                 "**Date:** 2026-07-01\n")
        recs, by_num = server.series_index(
            "PR", os.path.join(self.root, "docs", "product"))
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r["num"], r["title"], r["status"], r["date"], r["kind"]),
                         ("PR-007", "Streaming", "Accepted", "2026-07-01", "pr"))
        self.assertEqual(by_num[7], "docs/product/PR-007-streaming.md")

    def test_doc_series_always_includes_adr_plus_rules(self):
        server.DOC_RULES = [{"prefix": "PR", "dir": "docs/product",
                             "title": ""}]
        series = server.doc_series()
        self.assertEqual([s["prefix"] for s in series], ["ADR", "PR"])
        self.assertEqual(series[1]["href"], "/docs/PR")
        self.assertEqual(series[1]["title"], "PR documents")

    def test_pre_blocks_are_left_alone(self):
        self.doc("docs/product/PR-001-spec.md")
        server.DOC_RULES = [{"prefix": "PR", "dir": "docs/product"}]
        out = server.autolink_ids("<pre>PR-001</pre>")
        self.assertEqual(out, "<pre>PR-001</pre>")


class TestIdIndex(unittest.TestCase):
    """`/api/ids` — what the notes pop-out resolves a captured id against."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ifc-test-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self._saved = (server.REPO_ROOT, server.TICKETS_DIR, server.PFX)
        self.addCleanup(self._restore)
        server.REPO_ROOT = self.root
        server.TICKETS_DIR = os.path.join(self.root, "tickets")
        server.apply_config({"ids": {"prefix": "ZZ"}})

    def _restore(self):
        server.REPO_ROOT, server.TICKETS_DIR, server.PFX = self._saved
        server.apply_config({"ids": {"prefix": self._saved[2]}})

    def card(self, rel, tid, title, status):
        full = os.path.join(self.root, "tickets", rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("---\nid: %s\ntitle: %s\nepic: ZZ-E001\nstatus: %s\n"
                     "created: 2026-01-01\n---\n\n# %s\n"
                     % (tid, title, status, tid))

    def test_every_ticket_lands_in_the_index_with_its_status(self):
        self.card("ZZ-E001-x/open/ZZ-0001-a.md", "ZZ-0001", "Open one", "OPEN")
        self.card("ZZ-E001-x/closed/ZZ-0002-b.md", "ZZ-0002", "Shut one", "CLOSED")
        index = server.scan()[0]["index"]
        self.assertEqual(index["ZZ-0001"],
                         {"status": "OPEN", "title": "Open one"})
        self.assertEqual(index["ZZ-0002"]["status"], "CLOSED")


if __name__ == "__main__":
    unittest.main()

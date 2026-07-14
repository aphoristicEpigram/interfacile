"""The header quote: packaged `quote --- author` lines, repo-overridable."""
import os
import shutil
import tempfile
import unittest

from interfacile import server


class QuotesCase(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ifc-quotes-")
        self.addCleanup(shutil.rmtree, self.state, True)
        self._pins, self._on = server.PINS_FILE, server.QUOTES_ON
        server.PINS_FILE = os.path.join(self.state, "pins.json")
        server.QUOTES_ON = True
        self.addCleanup(self._restore)

    def _restore(self):
        server.PINS_FILE, server.QUOTES_ON = self._pins, self._on

    def test_packaged_default_parses(self):
        quotes = server.load_quotes()
        self.assertGreater(len(quotes), 100)
        self.assertIn(("Where focus goes, energy flows", "Tony Robbins"), quotes)
        for q, a in quotes:            # no comment lines, no stray separators
            self.assertFalse(q.startswith("#"))
            self.assertNotIn("---", q)

    def test_repo_file_overrides_and_tolerates_mess(self):
        with open(os.path.join(self.state, "quotes.txt"), "w", encoding="utf-8") as fh:
            fh.write("# mine\n\nJust do it --- Someone\nNo author here\n")
        self.assertEqual(server.load_quotes(),
                         [("Just do it", "Someone"), ("No author here", "")])

    def test_quote_html_escapes_and_toggles(self):
        with open(os.path.join(self.state, "quotes.txt"), "w", encoding="utf-8") as fh:
            fh.write("a < b --- O'Brien & Co\n")
        out = server.quote_html()
        self.assertIn("a &lt; b", out)
        self.assertIn("O&#x27;Brien &amp; Co", out)
        self.assertIn('class="h-quote"', out)
        server.QUOTES_ON = False       # the config checkbox: "quotes": false
        self.assertEqual(server.quote_html(), "")


if __name__ == "__main__":
    unittest.main()

"""Tests for how typed and copied text is classified.

Pure functions from blerp_gui - importing it does not create a window.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import blerp_gui  # noqa: E402

HEX24 = "a1b2c3d4e5f6a7b8c9d0e1f2"


class TestDetectMode(unittest.TestCase):
    def test_profile_url_is_bulk(self):
        self.assertEqual(blerp_gui.detect_mode(f"https://blerp.com/u/someone"),
                         ("bulk", "someone"))

    def test_soundbite_url_is_single(self):
        mode, value = blerp_gui.detect_mode(f"https://blerp.com/soundbites/{HEX24}")
        self.assertEqual(mode, "single")
        self.assertIn(HEX24, value)

    def test_bare_username_is_bulk(self):
        self.assertEqual(blerp_gui.detect_mode("someone"), ("bulk", "someone"))

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(blerp_gui.detect_mode("  someone \n"), ("bulk", "someone"))


class TestClipboardDetection(unittest.TestCase):
    """This runs unattended against whatever is on the clipboard, and with
    auto-download on it starts a fetch with no click, so it has to be strict."""

    def test_accepts_genuine_blerp_links(self):
        for url in (f"https://blerp.com/soundbites/{HEX24}",
                    f"https://www.blerp.com/soundbites/{HEX24}",
                    f"http://blerp.com/soundbites/{HEX24}"):
            self.assertIsNotNone(blerp_gui.looks_like_blerp_soundbite_url(url), url)

    def test_rejects_lookalike_hosts(self):
        """"blerp.com" appearing somewhere in a string is not the same as the
        URL pointing at Blerp."""
        for url in (f"https://evil.example/blerp.com/{HEX24}",
                    f"https://blerp.com.attacker.tld/soundbites/{HEX24}",
                    f"https://attacker.tld/x?ref=blerp.com&id={HEX24}",
                    f"https://notblerp.com/soundbites/{HEX24}"):
            self.assertIsNone(blerp_gui.looks_like_blerp_soundbite_url(url), url)

    def test_rejects_non_web_schemes(self):
        for url in (f"file:///C:/blerp.com/{HEX24}", f"ftp://blerp.com/{HEX24}"):
            self.assertIsNone(blerp_gui.looks_like_blerp_soundbite_url(url), url)

    def test_ignores_unrelated_hex_strings(self):
        """A git SHA or UUID fragment on the clipboard must not trigger a
        download."""
        for text in ("deadbeefdeadbeefdeadbeef",
                     "commit 1234567890abcdef1234567890abcdef",
                     "", "   ", "just some copied prose"):
            self.assertIsNone(blerp_gui.looks_like_blerp_soundbite_url(text), repr(text))

    def test_none_is_handled(self):
        self.assertIsNone(blerp_gui.looks_like_blerp_soundbite_url(None))

    def test_a_blerp_url_without_an_id_is_not_a_soundbite(self):
        self.assertIsNone(blerp_gui.looks_like_blerp_soundbite_url("https://blerp.com/"))


if __name__ == "__main__":
    unittest.main()

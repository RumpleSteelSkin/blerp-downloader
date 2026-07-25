"""Tests for resolving a soundbite page to its media URLs.

Driven from tests/fixtures/soundbite_page.html, which captures the shape of
blerp.com's embedded __NEXT_DATA__. If the site changes that shape, these are
what should fail - before a release, rather than in a bug report.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import scraping  # noqa: E402
from blerp_downloader.errors import BlerpError  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAGE = (FIXTURES / "soundbite_page.html").read_text(encoding="utf-8")
BITE_ID = "68a1b2c3d4e5f60718293a4b"
URL = f"https://blerp.com/soundbites/{BITE_ID}"


def _with_page(html: str):
    return mock.patch.object(scraping, "http_get", lambda url: html.encode("utf-8"))


class TestFetchBiteMedia(unittest.TestCase):
    def test_reads_the_real_page_shape(self):
        with _with_page(PAGE):
            media = scraping.fetch_bite_media(URL)
        self.assertEqual(media.bite_id, BITE_ID)
        self.assertEqual(media.title, "Test Blerp")
        self.assertEqual(media.audio_url, "https://cdn.blerp.com/audio/test.mp3")
        self.assertEqual(media.image_url, "https://cdn.blerp.com/image/test.webp")
        self.assertAlmostEqual(media.audio_duration_s, 5.5)   # ms in the page

    def test_missing_next_data_is_reported_as_a_site_change(self):
        with _with_page("<html><body>nothing here</body></html>"), \
             self.assertRaises(BlerpError) as cm:
            scraping.fetch_bite_media(URL)
        self.assertIn("__NEXT_DATA__", str(cm.exception))

    def test_missing_audio_url_is_reported(self):
        page = PAGE.replace('"mp3":{"url":"https://cdn.blerp.com/audio/test.mp3"}',
                            '"mp3":{}')
        with _with_page(page), self.assertRaises(BlerpError) as cm:
            scraping.fetch_bite_media(URL)
        self.assertIn("audio", str(cm.exception).lower())

    def test_unparseable_embedded_json_is_a_blerp_error(self):
        """Not a raw JSONDecodeError: the CLI only catches BlerpError, so
        anything else exits with a traceback."""
        page = PAGE.replace('{"props"', '{{{not json')
        with _with_page(page), self.assertRaises(BlerpError):
            scraping.fetch_bite_media(URL)


class TestWrongBiteFallback(unittest.TestCase):
    """A removed or private blerp still renders a page whose Apollo cache holds
    related bites. Returning one of those was reported to the user as success."""

    def _page_with(self, cache: dict) -> str:
        payload = {"props": {"pageProps": {"initialApolloState": cache}}}
        return ('<script id="__NEXT_DATA__" type="application/json">'
                + json.dumps(payload) + "</script>")

    def _bite(self, i: str) -> dict:
        return {
            f"Bite:{i}": {"_id": i, "title": f"Other {i}", "audioDuration": 1000,
                          "audio": {"__ref": f"A:{i}"}, "image": {"__ref": f"I:{i}"}},
            f"A:{i}": {"mp3": {"url": f"https://cdn.blerp.com/{i}.mp3"}},
            f"I:{i}": {"original": {"url": f"https://cdn.blerp.com/{i}.webp"}},
        }

    def test_refuses_to_guess_between_several_other_bites(self):
        cache = {}
        for i in ("bbbbbbbbbbbbbbbbbbbbbbb1", "bbbbbbbbbbbbbbbbbbbbbbb2"):
            cache.update(self._bite(i))
        with _with_page(self._page_with(cache)), self.assertRaises(BlerpError) as cm:
            scraping.fetch_bite_media(URL)
        self.assertIn("removed", str(cm.exception).lower())

    def test_accepts_a_single_unambiguous_bite(self):
        """A redirect to the canonical URL leaves exactly one bite; that is not
        a guess, so it should still work."""
        cache = self._bite("bbbbbbbbbbbbbbbbbbbbbbb1")
        with _with_page(self._page_with(cache)):
            media = scraping.fetch_bite_media(URL)
        self.assertEqual(media.audio_url,
                         "https://cdn.blerp.com/bbbbbbbbbbbbbbbbbbbbbbb1.mp3")


class TestHostValidation(unittest.TestCase):
    def test_only_blerp_urls_are_fetched(self):
        """OBJECTID_RE matches any 24-hex run in any string, so without a host
        check a link to another site would be requested and scraped."""
        for url in ("https://evil.example/" + "a" * 24,
                    "file:///C:/Windows/" + "a" * 24,
                    "https://blerp.com.attacker.tld/soundbites/" + "a" * 24):
            with self.assertRaises(BlerpError, msg=url):
                scraping.fetch_bite_media(url)

    def test_is_blerp_url(self):
        for good in ("https://blerp.com/soundbites/x", "https://www.blerp.com/u/y",
                     "http://blerp.com/"):
            self.assertTrue(scraping.is_blerp_url(good), good)
        for bad in ("https://evil.tld/?x=blerp.com", "https://blerp.com.evil.tld/",
                    "file:///blerp.com", "blerp.com", ""):
            self.assertFalse(scraping.is_blerp_url(bad), bad)


class TestParseBiteId(unittest.TestCase):
    def test_extracts_the_object_id(self):
        self.assertEqual(scraping.parse_bite_id(URL), BITE_ID)

    def test_rejects_a_url_without_one(self):
        with self.assertRaises(BlerpError):
            scraping.parse_bite_id("https://blerp.com/search/anime")


class TestResolveRef(unittest.TestCase):
    def test_follows_a_ref(self):
        cache = {"A:1": {"url": "x"}}
        self.assertEqual(scraping._resolve_ref(cache, {"__ref": "A:1"}), {"url": "x"})

    def test_dangling_ref_yields_empty(self):
        self.assertEqual(scraping._resolve_ref({}, {"__ref": "A:missing"}), {})

    def test_passes_through_an_inline_object(self):
        self.assertEqual(scraping._resolve_ref({}, {"url": "x"}), {"url": "x"})
        self.assertEqual(scraping._resolve_ref({}, None), {})


if __name__ == "__main__":
    unittest.main()

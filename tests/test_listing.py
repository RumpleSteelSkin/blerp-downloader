"""Tests for the paginated profile listing.

Driven from tests/fixtures/profile_page*.json, which capture the shape of
blerp.com's soundEmotesFeaturedContentPagination response.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import listing  # noqa: E402
from blerp_downloader.errors import BlerpError  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAGES = [json.loads((FIXTURES / f"profile_page{i}.json").read_text(encoding="utf-8"))["data"]
         for i in (1, 2)]


class _Paginated(unittest.TestCase):
    """Serves the fixture pages, recording how many requests were made."""

    def setUp(self):
        self.calls = []
        self.patches = [
            mock.patch.object(listing, "fetch_user_id", lambda u: ("uid", u)),
            mock.patch.object(listing, "time", mock.Mock()),   # no real backoff sleeps
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _graphql(self, responses):
        """responses: list of dicts or Exceptions, consumed per call."""
        def fake(query, variables):
            self.calls.append(variables["page"])
            r = responses[min(len(self.calls) - 1, len(responses) - 1)]
            if isinstance(r, Exception):
                raise r
            return r
        return mock.patch.object(listing, "graphql", fake)


class TestPagination(_Paginated):
    def test_walks_pages_until_has_next_page_is_false(self):
        with self._graphql(PAGES):
            bites = listing.list_user_bites("someone")
        self.assertEqual(self.calls, [1, 2])
        # 2 usable on page 1, 2 on page 2, one of which repeats -> 3 unique
        self.assertEqual([b.bite_id[-1] for b in bites], ["1", "3", "4"])

    def test_counts_entries_with_no_media_instead_of_hiding_them(self):
        with self._graphql(PAGES):
            bites = listing.list_user_bites("someone")
        self.assertEqual(bites.dropped, 1)

    def test_reads_the_fields_the_pipeline_needs(self):
        with self._graphql(PAGES):
            first = listing.list_user_bites("someone")[0]
        self.assertEqual(first.title, "First")
        self.assertEqual(first.audio_url, "https://cdn.blerp.com/a1.mp3")
        self.assertEqual(first.image_url, "https://cdn.blerp.com/i1.webp")
        self.assertAlmostEqual(first.audio_duration_s, 1.0)   # ms -> s

    def test_max_pages_bounds_a_server_that_never_ends(self):
        endless = {"soundEmotes": {"soundEmotesFeaturedContentPagination": {
            "pageInfo": {"hasNextPage": True},
            "items": PAGES[0]["soundEmotes"]["soundEmotesFeaturedContentPagination"]["items"],
        }}}
        with self._graphql([endless]):
            listing.list_user_bites("someone", max_pages=4)
        self.assertEqual(self.calls, [1, 2, 3, 4])

    def test_missing_page_info_ends_the_walk_rather_than_raising(self):
        broken = {"soundEmotes": {"soundEmotesFeaturedContentPagination": {
            "items": PAGES[0]["soundEmotes"]["soundEmotesFeaturedContentPagination"]["items"],
        }}}
        with self._graphql([broken]):
            bites = listing.list_user_bites("someone")
        self.assertEqual(len(bites), 2)

    def test_unexpected_shape_is_not_fatal(self):
        with self._graphql([{"soundEmotes": None}]):
            self.assertEqual(len(listing.list_user_bites("someone")), 0)


class TestResilience(_Paginated):
    def test_a_transient_failure_is_retried_not_fatal(self):
        """One hiccup used to discard every bite collected so far and download
        nothing at all."""
        responses = [BlerpError("502 Bad Gateway"), PAGES[0], PAGES[1]]
        with self._graphql(responses):
            bites = listing.list_user_bites("someone")
        self.assertEqual(len(bites), 3)

    def test_gives_up_after_repeated_failures(self):
        with self._graphql([BlerpError("down")]), self.assertRaises(BlerpError):
            listing.list_user_bites("someone")

    def test_cancel_stops_between_pages(self):
        class Cancelled:
            def is_set(self):
                return True

        with self._graphql(PAGES):
            bites = listing.list_user_bites("someone", cancel=Cancelled())
        self.assertEqual(self.calls, [])       # Stop is effective during the scan
        self.assertEqual(len(bites), 0)

    def test_progress_is_reported_per_page(self):
        seen = []
        with self._graphql(PAGES):
            listing.list_user_bites("someone", on_progress=lambda p, n: seen.append((p, n)))
        self.assertEqual(seen, [(1, 2), (2, 3)])


class TestEdgeToMedia(unittest.TestCase):
    def _edge(self, **over):
        bite = {"_id": "x" * 24, "title": "T", "audioDuration": 1500,
                "audio": {"mp3": {"url": "https://cdn.blerp.com/a.mp3"}},
                "image": {"original": {"url": "https://cdn.blerp.com/i.webp"}}}
        bite.update(over)
        return {"biteId": "x" * 24, "bite": bite}

    def test_happy_path(self):
        m = listing._edge_to_media(self._edge())
        self.assertEqual(m.audio_url, "https://cdn.blerp.com/a.mp3")

    def test_missing_pieces_yield_none(self):
        for over in ({"audio": None}, {"image": None}, {"audio": {}}, {"image": {}}):
            self.assertIsNone(listing._edge_to_media(self._edge(**over)), over)
        self.assertIsNone(listing._edge_to_media({"bite": {}}))
        self.assertIsNone(listing._edge_to_media({}))

    def test_title_falls_back_to_the_id(self):
        m = listing._edge_to_media(self._edge(title=None))
        self.assertEqual(m.title, "x" * 24)


class TestParseUsername(unittest.TestCase):
    def test_extracts_from_a_profile_url(self):
        self.assertEqual(listing.parse_username("https://blerp.com/u/someone"), "someone")

    def test_none_when_absent(self):
        for s in ("https://blerp.com/soundbites/abc", "", "someone"):
            self.assertIsNone(listing.parse_username(s), s)


if __name__ == "__main__":
    unittest.main()

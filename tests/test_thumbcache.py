"""Tests for the picture fetcher's thread pool.

The one that matters is test_a_parked_worker_does_not_shrink_the_pool: the
picker asks for its first screenful from inside its own constructor, while the
download worker is alive but parked waiting for the answer. Reading that as "a
download is using the connection" left the first screenful on one fetcher, so
nothing appeared until a scroll happened to re-ask with the flag finally set.
"""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_ui import thumbcache  # noqa: E402


def _has_display() -> bool:
    try:
        tk.Tk().destroy()
        return True
    except tk.TclError:
        return False


@unittest.skipUnless(_has_display(), "needs a display")
class TestFetcherPool(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.busy = False
        self.gate = threading.Event()
        self.fetched: list = []

        def fetch(bite_id, url):
            self.fetched.append(bite_id)
            self.gate.wait(5)          # hold the thread so the pool is observable
            return None

        patch = mock.patch.object(thumbcache.thumbs, "fetch", fetch)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.gate.set)

        patch2 = mock.patch.object(thumbcache.thumbs, "cached_png", lambda _b: None)
        patch2.start()
        self.addCleanup(patch2.stop)

        self.cache = thumbcache.ThumbCache(self.root, lambda _b: None,
                                           busy=lambda: self.busy)
        self.addCleanup(self.cache.close)

    def _alive(self) -> int:
        return sum(1 for t in self.cache._workers if t.is_alive())

    def _wait_for(self, predicate, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end and not predicate():
            time.sleep(0.02)
        return predicate()

    def _ask(self, n=12):
        self.cache.want((f"{i:024d}", f"https://cdn.blerp.com/thumbnails/{i}")
                        for i in range(n))

    def test_idle_uses_the_whole_pool(self):
        self._ask()
        self.assertTrue(self._wait_for(
            lambda: self._alive() == thumbcache.MAX_FETCHERS_IDLE),
            f"expected {thumbcache.MAX_FETCHERS_IDLE} fetchers, got {self._alive()}")

    def test_a_running_download_keeps_the_pool_to_one(self):
        """Bandwidth belongs to the download the user actually asked for."""
        self.busy = True
        self._ask()
        self.assertTrue(self._wait_for(lambda: self._alive() >= 1))
        self.assertEqual(self._alive(), thumbcache.MAX_FETCHERS_BUSY)

    def test_a_parked_worker_does_not_shrink_the_pool(self):
        """busy() is false while the picker is up, even though the download
        thread is alive - it is parked waiting for the user to answer."""
        self.busy = False          # what the picking flag produces
        self._ask()
        self.assertTrue(self._wait_for(
            lambda: self._alive() == thumbcache.MAX_FETCHERS_IDLE),
            "the first screenful was left on a single fetcher")

    def test_nothing_is_asked_for_twice(self):
        self._ask(4)
        self._wait_for(lambda: len(self.fetched) >= 1)
        self._ask(4)               # same ids again
        time.sleep(0.2)
        self.assertEqual(len(self.fetched), len(set(self.fetched)))

    def test_requests_are_capped(self):
        self.cache.want((f"{i:024d}", f"https://x/{i}")
                        for i in range(thumbcache.MAX_FETCHES_PER_REQUEST + 40))
        self.assertLessEqual(self.cache._wanted.qsize() + self._alive(),
                             thumbcache.MAX_FETCHES_PER_REQUEST)

    def test_disabled_asks_for_nothing(self):
        cache = thumbcache.ThumbCache(self.root, lambda _b: None,
                                      enabled=lambda: False)
        self.addCleanup(cache.close)
        cache.want([("0" * 24, "https://x/0")])
        self.assertEqual(cache._wanted.qsize(), 0)
        self.assertEqual(cache._workers, [])


if __name__ == "__main__":
    unittest.main()

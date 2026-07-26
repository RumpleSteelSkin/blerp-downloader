"""Tests for the cached blerp images.

The bite id these are keyed on comes from the download list, which is a file
anything running as the user can write, and it becomes a path - so most of this
is about what the cache must refuse to write.
"""

from __future__ import annotations

import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import theme, thumbs  # noqa: E402
from blerp_downloader.errors import BlerpError  # noqa: E402

BITE = "0123456789abcdef01234567"

HOSTILE_IDS = ("../../evil", r"..\..\evil", "C:/Windows/System32/x",
               "/etc/passwd", "", "   ", BITE + "x", "nothex!!" * 3)


def _webp_bytes(frames: int = 3, size: int = 200) -> bytes:
    """A real animated WebP, so the decode path is genuinely exercised."""
    from PIL import Image
    images = [Image.new("RGBA", (size, size), (i * 60 % 255, 40, 90, 255))
              for i in range(frames)]
    buf = io.BytesIO()
    images[0].save(buf, format="WEBP", save_all=True, append_images=images[1:],
                   duration=100, loop=0)
    return buf.getvalue()


class _TempThumbs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._patch = mock.patch.object(thumbs, "state_dir", lambda: self.root)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()


class TestStore(_TempThumbs):
    def test_a_png_and_an_ico_are_written(self):
        png = thumbs.store(BITE, _webp_bytes())
        self.assertIsNotNone(png)
        self.assertTrue(png.exists())
        self.assertEqual(thumbs.cached_png(BITE), png)
        self.assertIsNotNone(thumbs.cached_ico(BITE))

    def test_the_png_is_thumbnail_sized(self):
        from PIL import Image
        thumbs.store(BITE, _webp_bytes(size=512))
        with Image.open(thumbs.cached_png(BITE)) as im:
            self.assertLessEqual(max(im.size), thumbs.THUMB_PX)

    def test_the_write_is_atomic(self):
        thumbs.store(BITE, _webp_bytes())
        self.assertEqual(list(thumbs.thumbs_dir().glob("*.part")), [])

    def test_a_static_image_works_too(self):
        """Not every blerp's image is animated, and seek(0) has to cope."""
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (120, 120), "red").save(buf, format="PNG")
        self.assertIsNotNone(thumbs.store(BITE, buf.getvalue()))

    def test_corrupt_bytes_produce_nothing_and_do_not_raise(self):
        """This runs inside a download; a broken image must not fail it."""
        self.assertIsNone(thumbs.store(BITE, b"not an image at all"))
        self.assertIsNone(thumbs.cached_png(BITE))

    def test_empty_bytes_produce_nothing(self):
        self.assertIsNone(thumbs.store(BITE, b""))


class TestRefusesHostileIds(_TempThumbs):
    def test_nothing_is_written_outside_the_cache(self):
        for bad in HOSTILE_IDS:
            self.assertIsNone(thumbs.store(bad, _webp_bytes()), bad)
        # Not one stray file anywhere under the state directory.
        written = [p for p in self.root.rglob("*") if p.is_file()]
        self.assertEqual(written, [], written)

    def test_lookups_refuse_them_too(self):
        for bad in HOSTILE_IDS:
            self.assertIsNone(thumbs.cached_png(bad), bad)
            self.assertIsNone(thumbs.cached_ico(bad), bad)

    def test_fetch_refuses_before_going_to_the_network(self):
        with mock.patch.object(thumbs, "http_get") as get:
            for bad in HOSTILE_IDS:
                self.assertIsNone(thumbs.fetch(bad, "https://cdn.blerp.com/i.webp"))
        get.assert_not_called()


class TestFetch(_TempThumbs):
    def test_a_cached_image_is_not_downloaded_again(self):
        thumbs.store(BITE, _webp_bytes())
        with mock.patch.object(thumbs, "http_get") as get:
            self.assertIsNotNone(thumbs.fetch(BITE, "https://cdn.blerp.com/i.webp"))
        get.assert_not_called()

    def test_it_downloads_when_nothing_is_cached(self):
        with mock.patch.object(thumbs, "http_get", return_value=_webp_bytes()):
            self.assertIsNotNone(thumbs.fetch(BITE, "https://cdn.blerp.com/i.webp"))
        self.assertIsNotNone(thumbs.cached_png(BITE))

    def test_a_network_failure_is_not_an_error(self):
        with mock.patch.object(thumbs, "http_get", side_effect=BlerpError("404")):
            self.assertIsNone(thumbs.fetch(BITE, "https://cdn.blerp.com/i.webp"))

    def test_the_download_is_capped(self):
        """An oversized body raises rather than being truncated, which is what
        keeps a hostile response from filling memory."""
        with mock.patch.object(thumbs, "http_get", return_value=_webp_bytes()) as get:
            thumbs.fetch(BITE, "https://cdn.blerp.com/i.webp")
        self.assertEqual(get.call_args.kwargs["limit"], thumbs.THUMB_MAX_BYTES)


class TestStoreFromWebp(_TempThumbs):
    def test_it_uses_the_file_the_pipeline_already_has(self):
        src = self.root / "image.webp"
        src.write_bytes(_webp_bytes())
        with mock.patch.object(thumbs, "http_get") as get:
            self.assertIsNotNone(thumbs.store_from_webp(BITE, src))
        get.assert_not_called()

    def test_a_missing_file_is_not_an_error(self):
        self.assertIsNone(thumbs.store_from_webp(BITE, self.root / "gone.webp"))


class TestSweep(_TempThumbs):
    def _make(self, n: int):
        thumbs.thumbs_dir().mkdir(parents=True, exist_ok=True)
        now = time.time()
        for i in range(n):
            p = thumbs.thumbs_dir() / f"{i:024d}.png"
            p.write_bytes(b"x")
            import os
            os.utime(p, (now - (n - i), now - (n - i)))   # oldest first

    def test_it_keeps_the_most_recently_used(self):
        self._make(10)
        self.assertEqual(thumbs.sweep(max_files=4), 6)
        left = sorted(p.name for p in thumbs.thumbs_dir().glob("*.png"))
        self.assertEqual(len(left), 4)
        self.assertIn(f"{9:024d}.png", left)      # newest kept
        self.assertNotIn(f"{0:024d}.png", left)   # oldest gone

    def test_under_the_cap_nothing_goes(self):
        self._make(3)
        self.assertEqual(thumbs.sweep(max_files=10), 0)

    def test_an_absent_cache_is_not_an_error(self):
        self.assertEqual(thumbs.sweep(), 0)


class TestFitsTheRow(unittest.TestCase):
    def test_the_thumbnail_fits_the_list_row(self):
        """clam clips whatever the row height cannot hold, and a thumbnail
        taller than the row would be silently cut in half."""
        self.assertLess(thumbs.THUMB_PX, theme.LIST_ROW_HEIGHT)


if __name__ == "__main__":
    unittest.main()

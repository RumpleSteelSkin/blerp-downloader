"""Tests for settings persistence."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import settings as st  # noqa: E402


class _TempSettings(unittest.TestCase):
    """Redirects the settings file into a temporary directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "settings.ini"
        self._patch = mock.patch.object(st, "_settings_path", lambda: self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()


class TestRoundTrip(_TempSettings):
    def test_defaults_when_absent(self):
        self.assertEqual(st.load_settings(), st.Settings())

    def test_every_field_survives(self):
        original = st.Settings(
            output_dir="D:/out", overwrite=True, bulk_limit=7, bulk_delay=1.5,
            ffmpeg_dir="C:/ffmpeg/bin", window_width=800, window_height=600,
            clipboard_watch=True, clipboard_mode="auto", theme="dark",
        )
        st.save_settings(original)
        self.assertEqual(st.load_settings(), original)

    def test_theme_is_written(self):
        st.save_settings(st.Settings(theme="light"))
        self.assertIn("theme = light", self.path.read_text(encoding="utf-8"))


class TestTolerance(_TempSettings):
    def test_utf8_bom_does_not_discard_the_file(self):
        """Regression: the file is documented as hand-editable, and Windows
        editors commonly add a BOM. Read as plain utf-8 the BOM lands in the
        section header and every setting silently reverts to its default."""
        self.path.write_bytes(
            "\ufeff[general]\noutput_dir = D:/kept\nclipboard_watch = True\n"
            .encode("utf-8"))
        loaded = st.load_settings()
        self.assertEqual(loaded.output_dir, "D:/kept")
        self.assertTrue(loaded.clipboard_watch)

    def test_unparseable_file_falls_back_to_defaults(self):
        self.path.write_text("this is not ini [[[", encoding="utf-8")
        self.assertEqual(st.load_settings(), st.Settings())

    def test_bad_values_fall_back_per_field(self):
        self.path.write_text(
            "[general]\noutput_dir = D:/good\noverwrite = maybe\n"
            "bulk_limit = many\nbulk_delay = fast\nwindow_width = huge\n",
            encoding="utf-8")
        loaded = st.load_settings()
        self.assertEqual(loaded.output_dir, "D:/good")   # the valid one survives
        self.assertFalse(loaded.overwrite)
        self.assertIsNone(loaded.bulk_limit)
        self.assertEqual(loaded.bulk_delay, st.Settings().bulk_delay)
        self.assertEqual(loaded.window_width, st.Settings().window_width)

    def test_unknown_theme_falls_back_to_auto(self):
        for bad in ("neon", "", "DARKK"):
            self.path.write_text(f"[general]\ntheme = {bad}\n", encoding="utf-8")
            self.assertEqual(st.load_settings().theme, "auto", bad)

    def test_theme_is_case_insensitive(self):
        self.path.write_text("[general]\ntheme = DARK\n", encoding="utf-8")
        self.assertEqual(st.load_settings().theme, "dark")


if __name__ == "__main__":
    unittest.main()

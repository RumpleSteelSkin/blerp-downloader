"""Tests for settings persistence."""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import settings as st  # noqa: E402

# Fields whose value has to come from a fixed set rather than "anything but the
# default" - load_settings clamps these, so a made-up value wouldn't round-trip.
_CONSTRAINED = {
    "theme": "dark",
    "clipboard_mode": "auto",
    "bulk_limit": 7,          # int | None, and the default is None
    "output_dir": "D:/out",
    "ffmpeg_dir": "C:/ffmpeg/bin",
}


def _non_default(field: dataclasses.Field):
    """A valid value for `field` that differs from its default.

    Derived from the field rather than written out per name, so a field added to
    Settings is covered by the round-trip test without anyone remembering to
    extend a literal.
    """
    if field.name in _CONSTRAINED:
        return _CONSTRAINED[field.name]
    default = field.default
    if isinstance(default, bool):
        return not default
    if isinstance(default, int):
        return default + 137
    if isinstance(default, float):
        return default + 1.25
    raise AssertionError(f"no non-default value known for {field.name!r} "
                         f"(default {default!r}) - add one to _CONSTRAINED")


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
        """Built from the dataclass, not by hand: a hand-listed Settings(...)
        takes the default for any field added later, so both sides would match
        without the new field ever being written."""
        original = st.Settings(**{f.name: _non_default(f)
                                  for f in dataclasses.fields(st.Settings)})
        st.save_settings(original)
        self.assertEqual(st.load_settings(), original)

    def test_every_field_is_written(self):
        """A field missing from the file loads as its default, so an omission is
        invisible to a round-trip test that happens to use that default."""
        st.save_settings(st.Settings())
        text = self.path.read_text(encoding="utf-8")
        for f in dataclasses.fields(st.Settings):
            self.assertIn(f"{f.name} = ", text, f.name)

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

    def test_unknown_clipboard_mode_falls_back_to_ask(self):
        """Anything but "auto" used to be treated as "ask" by the one caller, so
        a typo happened to work; now that the value is read in several places it
        is clamped on load instead of relying on that."""
        for bad in ("automatic", "", "yes"):
            self.path.write_text(f"[general]\nclipboard_mode = {bad}\n",
                                 encoding="utf-8")
            self.assertEqual(st.load_settings().clipboard_mode, "ask", bad)

    def test_new_toggles_default_to_on(self):
        """These gate behaviour the user asked for, so an absent key must not
        silently disable them."""
        self.path.write_text("[general]\noutput_dir = D:/x\n", encoding="utf-8")
        loaded = st.load_settings()
        for name in ("close_to_tray", "tray_enabled", "notify_balloons",
                     "notify_card", "thumbnails"):
            self.assertTrue(getattr(loaded, name), name)
        self.assertFalse(loaded.log_expanded)


if __name__ == "__main__":
    unittest.main()

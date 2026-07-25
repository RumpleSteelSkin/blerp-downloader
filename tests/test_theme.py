"""Tests for light/dark theme resolution."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import theme as th  # noqa: E402


class TestDetectWindowsTheme(unittest.TestCase):
    def test_non_windows_reports_light(self):
        with mock.patch.object(th.os, "name", "posix"):
            self.assertEqual(th.detect_windows_theme(), "light")

    def test_missing_registry_value_reports_light(self):
        """Windows itself treats an absent value as light (Server Core, LTSC),
        so guessing dark there would be wrong."""
        with mock.patch.object(th.os, "name", "nt"), \
             mock.patch("winreg.OpenKey", side_effect=FileNotFoundError):
            self.assertEqual(th.detect_windows_theme(), "light")

    def test_permission_error_reports_light(self):
        with mock.patch.object(th.os, "name", "nt"), \
             mock.patch("winreg.OpenKey", side_effect=OSError("denied")):
            self.assertEqual(th.detect_windows_theme(), "light")

    def test_reads_the_value(self):
        for raw, expected in ((0, "dark"), (1, "light")):
            with mock.patch.object(th.os, "name", "nt"), \
                 mock.patch("winreg.OpenKey", mock.MagicMock()), \
                 mock.patch("winreg.QueryValueEx", return_value=(raw, 4)):
                self.assertEqual(th.detect_windows_theme(), expected, raw)


class TestResolveMode(unittest.TestCase):
    def test_explicit_modes_ignore_the_system(self):
        with mock.patch.object(th, "detect_windows_theme",
                               side_effect=AssertionError("should not be consulted")):
            self.assertEqual(th.resolve_mode("dark"), "dark")
            self.assertEqual(th.resolve_mode("light"), "light")

    def test_auto_follows_the_system(self):
        with mock.patch.object(th, "detect_windows_theme", return_value="dark"):
            self.assertEqual(th.resolve_mode("auto"), "dark")

    def test_unknown_setting_behaves_as_auto(self):
        with mock.patch.object(th, "detect_windows_theme", return_value="dark"):
            self.assertEqual(th.resolve_mode("nonsense"), "dark")


class TestPalettes(unittest.TestCase):
    def test_palette_for(self):
        self.assertIs(th.palette_for("dark"), th.DARK)
        self.assertIs(th.palette_for("light"), th.LIGHT)

    def test_every_colour_is_defined_in_both(self):
        for field in th.Palette.__dataclass_fields__:
            for p in (th.DARK, th.LIGHT):
                value = getattr(p, field)
                self.assertRegex(value, r"^#[0-9a-fA-F]{6}$", f"{field} on {p}")

    def test_themes_actually_differ(self):
        self.assertNotEqual(th.DARK.bg, th.LIGHT.bg)
        self.assertNotEqual(th.DARK.text, th.LIGHT.text)

    def test_text_contrasts_with_its_background(self):
        """Guards against a palette edit that makes text unreadable."""
        def luminance(hex_colour: str) -> float:
            r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
            chan = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                    for c in (r, g, b)]
            return 0.2126 * chan[0] + 0.7152 * chan[1] + 0.0722 * chan[2]

        def ratio(a: str, b: str) -> float:
            la, lb = sorted((luminance(a), luminance(b)))
            return (lb + 0.05) / (la + 0.05)

        for p in (th.DARK, th.LIGHT):
            self.assertGreaterEqual(ratio(p.text, p.bg), 4.5)
            self.assertGreaterEqual(ratio(p.accent_fg, p.accent), 3.0)
            self.assertGreaterEqual(ratio(p.muted, p.bg), 3.0)


if __name__ == "__main__":
    unittest.main()

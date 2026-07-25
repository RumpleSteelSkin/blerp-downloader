"""Tests for binary resolution and the no-console subprocess flags."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import ffmpeg_utils as fu  # noqa: E402


class TestHiddenProcessKwargs(unittest.TestCase):
    def test_windows_hides_via_both_mechanisms(self):
        kw = fu.hidden_process_kwargs()
        if os.name != "nt":
            self.assertEqual(kw, {})
            return
        # CREATE_NO_WINDOW covers our own child; SW_HIDE is inherited further down.
        self.assertEqual(kw["creationflags"], subprocess.CREATE_NO_WINDOW)
        si = kw["startupinfo"]
        self.assertTrue(si.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(si.wShowWindow, subprocess.SW_HIDE)

    def test_returns_a_fresh_startupinfo_each_call(self):
        if os.name != "nt":
            self.skipTest("Windows only")
        self.assertIsNot(fu.hidden_process_kwargs()["startupinfo"],
                         fu.hidden_process_kwargs()["startupinfo"])


class TestDereferenceShim(unittest.TestCase):
    """The Chocolatey layout is  <root>\\bin\\x.exe  (shim)
                          and    <root>\\lib\\<pkg>\\tools\\...\\x.exe  (real)."""

    def _layout(self, root: Path, shim_bytes: int, real_bytes: int) -> tuple[Path, Path]:
        shim = root / "chocolatey" / "bin" / "ffmpeg.exe"
        real = root / "chocolatey" / "lib" / "ffmpeg" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
        for p, n in ((shim, shim_bytes), (real, real_bytes)):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\0" * n)
        return shim, real

    def test_resolves_shim_to_the_real_binary(self):
        with tempfile.TemporaryDirectory() as td:
            shim, real = self._layout(Path(td), 1000, 100_000)
            self.assertEqual(fu._dereference_shim(shim), real)

    def test_leaves_a_normal_path_alone(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ffmpeg" / "bin" / "ffmpeg.exe"
            p.parent.mkdir(parents=True)
            p.write_bytes(b"\0" * 100_000)
            self.assertEqual(fu._dereference_shim(p), p)

    def test_ignores_a_similarly_sized_candidate(self):
        # Guards against pointing at another stub rather than the real program.
        with tempfile.TemporaryDirectory() as td:
            shim, _ = self._layout(Path(td), 1000, 1200)
            self.assertEqual(fu._dereference_shim(shim), shim)

    def test_missing_lib_directory_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            shim = Path(td) / "chocolatey" / "bin" / "ffmpeg.exe"
            shim.parent.mkdir(parents=True)
            shim.write_bytes(b"\0" * 1000)
            self.assertEqual(fu._dereference_shim(shim), shim)


class TestResolveBinary(unittest.TestCase):
    def setUp(self):
        self._saved = dict(fu._resolved)
        fu._resolved.clear()

    def tearDown(self):
        fu._resolved.clear()
        fu._resolved.update(self._saved)

    def test_a_failed_lookup_is_not_cached(self):
        """ffmpeg can be installed while the app is open (the winget offer),
        so a miss must not stick."""
        fu._resolve_binary("definitely-not-a-real-binary-xyz")
        self.assertEqual(fu._resolved, {})

    def test_override_directory_wins(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            exe.write_bytes(b"\0" * 10)
            saved = fu.load_settings
            fu.load_settings = lambda: type("S", (), {"ffmpeg_dir": td})()
            try:
                self.assertEqual(fu._resolve_binary("ffmpeg"), str(exe))
            finally:
                fu.load_settings = saved


if __name__ == "__main__":
    unittest.main()

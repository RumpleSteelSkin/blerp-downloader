"""Tests for cache clearing.

This is the only code in the app that deletes things, and one of the paths
reaches into a folder the user chose, so the cases below are mostly about what
it must *refuse* to remove.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import maintenance, updater  # noqa: E402


class TestPartGlob(unittest.TestCase):
    def test_selects_only_our_own_part_files(self):
        """One character wider ("*.mp4*") and this deletes the user's videos."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for name in ("a.mp4", "a.mp4.part", "b.part", "c.mp4.part.txt",
                         "d.MP4", "keep.txt"):
                (d / name).write_text("x", encoding="utf-8")
            result = maintenance.CleanupResult()
            maintenance.clear_part_files(result, [d])
            left = sorted(p.name for p in d.iterdir())
            self.assertEqual(left, ["a.mp4", "b.part", "c.mp4.part.txt",
                                    "d.MP4", "keep.txt"])
            self.assertEqual(result.removed, 1)

    def test_does_not_recurse_into_subfolders(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "sub").mkdir()
            (d / "sub" / "deep.mp4.part").write_text("x", encoding="utf-8")
            maintenance.clear_part_files(maintenance.CleanupResult(), [d])
            self.assertTrue((d / "sub" / "deep.mp4.part").exists())


class TestTempSweep(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._patch = mock.patch.object(maintenance.tempfile, "gettempdir",
                                        lambda: str(self.root))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _scratch(self, name: str, *, locked: bool, age_seconds: int = 0) -> Path:
        d = self.root / name
        d.mkdir()
        (d / "frame_00000.png").write_bytes(b"x" * 100)
        if locked:
            (d / maintenance.LOCK_NAME).write_text("", encoding="utf-8")
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(d, (old, old))
        return d

    def test_removes_abandoned_scratch(self):
        d = self._scratch("blerpdl_dead", locked=False,
                          age_seconds=maintenance.STALE_TEMP_SECONDS + 60)
        result = maintenance.CleanupResult()
        maintenance.clear_temp(result)
        self.assertFalse(d.exists())
        self.assertGreater(result.freed_bytes, 0)

    def test_leaves_unrelated_folders_alone(self):
        other = self.root / "something_else"
        other.mkdir()
        maintenance.clear_temp(maintenance.CleanupResult())
        self.assertTrue(other.exists())

    def test_a_young_unlocked_folder_is_left_alone(self):
        """No lock file means an older build wrote it, so age is all there is -
        and the folder sits idle for the whole mux, so the window has to be
        generous."""
        d = self._scratch("blerp_recent", locked=False, age_seconds=60)
        maintenance.clear_temp(maintenance.CleanupResult())
        self.assertTrue(d.exists())

    @unittest.skipUnless(sys.platform == "win32", "the lock probe is Windows-only")
    def test_a_folder_whose_lock_is_held_open_is_skipped(self):
        """A live download holds its lock open; Windows then refuses the unlink,
        which is exactly the signal. Deleting it would destroy the frames of a
        run in progress."""
        d = self._scratch("blerpdl_live", locked=True)
        result = maintenance.CleanupResult()
        with (d / maintenance.LOCK_NAME).open("a"):
            maintenance.clear_temp(result)
        self.assertTrue(d.exists())
        self.assertEqual(result.in_use, 1)

    @unittest.skipUnless(sys.platform == "win32", "the lock probe is Windows-only")
    def test_a_folder_whose_lock_is_closed_is_removed(self):
        d = self._scratch("blerpdl_finished", locked=True)
        maintenance.clear_temp(maintenance.CleanupResult())
        self.assertFalse(d.exists())


class TestUpdatesSweep(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        # Patched on the module attribute, which is how the rest of the suite
        # does it - maintenance calls updater.updates_dir() rather than
        # importing the name, so this interception works.
        self._patch = mock.patch.object(updater, "updates_dir", lambda: self.dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_removes_installers_of_any_age(self):
        (self.dir / "BlerpDownloader-Setup-1.0.5.exe").write_bytes(b"x" * 2048)
        (self.dir / "BlerpDownloader-Setup-1.0.6.exe.part").write_bytes(b"x" * 512)
        result = maintenance.CleanupResult()
        maintenance.clear_updates(result)
        self.assertEqual(list(self.dir.iterdir()), [])
        self.assertEqual(result.freed_bytes, 2560)

    def test_leaves_other_files_alone(self):
        keep = self.dir / "notes.txt"
        keep.write_text("x", encoding="utf-8")
        maintenance.clear_updates(maintenance.CleanupResult())
        self.assertTrue(keep.exists())


class TestOutputDirs(unittest.TestCase):
    """The folders clear-cache is willing to delete inside.

    saved_jobs is stubbed rather than left alone: without it these read the real
    %LOCALAPPDATA%, so the answer would depend on whatever the person running
    the tests happens to have half-downloaded.
    """

    def setUp(self):
        patch = mock.patch.object(maintenance.jobs, "saved_jobs", lambda: [])
        patch.start()
        self.addCleanup(patch.stop)

    def test_deduplicates_and_keeps_only_real_folders(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("blerp_downloader.settings.load_settings",
                            lambda: mock.Mock(output_dir=td)):
                dirs = maintenance.output_dirs(td, td.upper(), "", "Z:/does-not-exist")
        self.assertEqual(len(dirs), 1)

    def test_nothing_configured_yields_nothing(self):
        """With no output folder set the destination is relative to the working
        directory - not something to go deleting in."""
        with mock.patch("blerp_downloader.settings.load_settings",
                        lambda: mock.Mock(output_dir="")):
            self.assertEqual(maintenance.output_dirs(), [])

    def test_a_saved_listing_contributes_its_folder(self):
        """A profile scanned into a folder is one the app can prove it wrote to,
        so leftover .part files there are fair game."""
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(maintenance.jobs, "saved_jobs",
                                   lambda: [mock.Mock(out_dir=td)]), \
                 mock.patch("blerp_downloader.settings.load_settings",
                            lambda: mock.Mock(output_dir="")):
                self.assertEqual([str(p) for p in maintenance.output_dirs()],
                                 [str(Path(td).resolve())])


if __name__ == "__main__":
    unittest.main()

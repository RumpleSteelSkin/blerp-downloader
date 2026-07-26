"""Tests for the saved bulk-download job.

The file lives in a directory anything running as the user can write, and its
URLs are handed to http_get, so the loader is treated as parsing untrusted input.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import jobs  # noqa: E402
from blerp_downloader.scraping import BiteMedia  # noqa: E402


def _bite(n: int = 1) -> BiteMedia:
    return BiteMedia(bite_id=f"{n:024d}", title=f"Blerp {n}",
                     audio_url=f"https://cdn.blerp.com/a{n}.mp3",
                     image_url=f"https://cdn.blerp.com/i{n}.webp",
                     audio_duration_s=1.5)


class _TempJob(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "job.json"
        self._patch = mock.patch.object(jobs, "_job_path", lambda: self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _job(self, **over) -> jobs.Job:
        defaults = dict(username="someone", bites=[_bite(1), _bite(2)], dropped=1,
                        limit=None, overwrite=False, out_dir="D:/out",
                        created_at=time.time(), app_version="1.0.6")
        defaults.update(over)
        return jobs.Job(**defaults)


class TestRoundTrip(_TempJob):
    def test_saves_and_loads(self):
        jobs.save_job(self._job())
        loaded = jobs.load_job()
        self.assertEqual(loaded.username, "someone")
        self.assertEqual([b.bite_id for b in loaded.bites],
                         [f"{n:024d}" for n in (1, 2)])
        self.assertEqual(loaded.dropped, 1)
        self.assertEqual(loaded.out_dir, "D:/out")

    def test_bite_fields_survive(self):
        jobs.save_job(self._job(bites=[_bite(7)]))
        b = jobs.load_job().bites[0]
        self.assertEqual(b.title, "Blerp 7")
        self.assertEqual(b.audio_url, "https://cdn.blerp.com/a7.mp3")
        self.assertAlmostEqual(b.audio_duration_s, 1.5)

    def test_write_is_atomic(self):
        jobs.save_job(self._job())
        self.assertEqual(list(self.path.parent.glob("*.part")), [])

    def test_clear(self):
        jobs.save_job(self._job())
        jobs.clear_job()
        self.assertIsNone(jobs.load_job())
        jobs.clear_job()      # idempotent


class TestLoadIsTolerant(_TempJob):
    def test_missing_file(self):
        self.assertIsNone(jobs.load_job())

    def test_corrupt_json(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(jobs.load_job())

    def test_empty_file(self):
        self.path.write_text("", encoding="utf-8")
        self.assertIsNone(jobs.load_job())

    def test_unknown_schema_version_is_ignored(self):
        """A future build's format must not be half-read into today's fields."""
        self.path.write_text(json.dumps({"version": 999, "username": "x",
                                         "bites": [dataclasses.asdict(_bite())]}),
                             encoding="utf-8")
        self.assertIsNone(jobs.load_job())

    def test_utf8_bom_is_tolerated(self):
        jobs.save_job(self._job())
        raw = self.path.read_bytes()
        self.path.write_bytes(b"\xef\xbb\xbf" + raw)
        self.assertIsNotNone(jobs.load_job())

    def test_a_bad_record_is_dropped_and_the_rest_survive(self):
        jobs.save_job(self._job(bites=[_bite(1), _bite(2)]))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["bites"][0]["audio_url"] = None          # unusable
        del data["bites"][1]["bite_id"]               # unusable
        data["bites"].append(dataclasses.asdict(_bite(3)))
        self.path.write_text(json.dumps(data), encoding="utf-8")
        loaded = jobs.load_job()
        self.assertEqual([b.bite_id for b in loaded.bites], [f"{3:024d}"])

    def test_no_usable_records_is_no_job(self):
        jobs.save_job(self._job())
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for b in data["bites"]:
            b["image_url"] = ""
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(jobs.load_job())


class TestUrlsAreRevalidated(_TempJob):
    def test_non_http_urls_are_rejected(self):
        """The file is writable by anything running as the user and these go
        straight to http_get."""
        for bad in ("file:///C:/Windows/win.ini", "ftp://x/y", "data:text/plain,x"):
            jobs.save_job(self._job(bites=[_bite(1)]))
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data["bites"][0]["audio_url"] = bad
            self.path.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(jobs.load_job(), bad)


class TestMatching(_TempJob):
    def test_matches_the_same_username(self):
        self.assertTrue(self._job().matches("someone"))

    def test_username_is_case_and_space_insensitive(self):
        """list_user_bites resolves a canonical name and discards it, so the job
        holds whatever the user typed."""
        job = self._job(username="SomeOne")
        for typed in ("someone", "SOMEONE", "  SomeOne  "):
            self.assertTrue(job.matches(typed), typed)

    def test_does_not_match_another_user(self):
        self.assertFalse(self._job().matches("someone-else"))

    def test_output_folder_is_not_part_of_matching(self):
        """The listing belongs to the profile, not to where files are going -
        changing the folder must not throw the scan away."""
        job = self._job(out_dir="D:/somewhere-else")
        self.assertTrue(job.matches("someone"))


class TestUsability(_TempJob):
    def test_a_fresh_job_is_usable(self):
        self.assertTrue(self._job().is_usable())

    def test_an_overwrite_job_is_never_resumed(self):
        """Overwrite ignores what's on disk, so there is no way to tell how far
        it got: resuming would restart at the first blerp every time."""
        self.assertFalse(self._job(overwrite=True).is_usable())

    def test_an_old_job_is_not_usable(self):
        stale = time.time() - (jobs.MAX_AGE_DAYS + 1) * 86400
        self.assertFalse(self._job(created_at=stale).is_usable())

    def test_an_empty_job_is_not_usable(self):
        self.assertFalse(self._job(bites=[]).is_usable())

    def test_partial_scans_are_still_usable(self):
        self.assertTrue(self._job(scan_complete=False).is_usable())


class TestBiteMediaSchemaGuard(unittest.TestCase):
    def test_field_set_is_pinned(self):
        """The job file stores BiteMedia verbatim. Adding a field silently
        changes that format, so this fails until SCHEMA_VERSION is considered."""
        self.assertEqual(
            {f.name for f in dataclasses.fields(BiteMedia)},
            {"bite_id", "title", "audio_url", "image_url", "audio_duration_s"})


if __name__ == "__main__":
    unittest.main()

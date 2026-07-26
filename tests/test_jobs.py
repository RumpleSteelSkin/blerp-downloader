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
        self.root = Path(self._tmp.name)
        # state_dir rather than _job_path: listings are keyed by profile now, so
        # patching the path function would hide the very thing that decides
        # which file a job lands in.
        self._patch = mock.patch.object(jobs, "state_dir", lambda: self.root)
        self._patch.start()
        self.path = jobs._job_path("someone")
        self.path.parent.mkdir(parents=True, exist_ok=True)

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


class TestPerProfileListings(_TempJob):
    """One saved listing per profile.

    A single job.json meant only one profile could ever be resumed, which the
    download list makes visible: it can hold several profile rows at once.
    """

    def test_two_profiles_do_not_overwrite_each_other(self):
        jobs.save_job(self._job(username="alice", bites=[_bite(1)]))
        jobs.save_job(self._job(username="bob", bites=[_bite(2), _bite(3)]))
        self.assertEqual(len(jobs.load_job("alice").bites), 1)
        self.assertEqual(len(jobs.load_job("bob").bites), 2)

    def test_lookup_is_case_folded(self):
        """list_user_bites resolves a canonical name and discards it, so what
        reaches here is whatever the user typed."""
        jobs.save_job(self._job(username="SomeOne"))
        self.assertIsNotNone(jobs.load_job("someone"))
        self.assertIsNotNone(jobs.load_job("  SOMEONE  "))

    def test_a_username_shaped_like_a_path_cannot_escape(self):
        """The name is arbitrary text from a profile URL and becomes a filename."""
        for hostile in ("../../evil", r"..\..\evil", "C:/Windows/system32"):
            jobs.save_job(self._job(username=hostile))
            written = list(jobs._jobs_dir().glob("*.json"))
            for path in written:
                self.assertEqual(path.parent, jobs._jobs_dir(), hostile)
            self.assertIsNotNone(jobs.load_job(hostile), hostile)

    def test_clearing_one_profile_leaves_the_others(self):
        jobs.save_job(self._job(username="alice"))
        jobs.save_job(self._job(username="bob"))
        jobs.clear_job("alice")
        self.assertIsNone(jobs.load_job("alice"))
        self.assertIsNotNone(jobs.load_job("bob"))

    def test_clearing_with_no_name_removes_every_listing(self):
        jobs.save_job(self._job(username="alice"))
        jobs.save_job(self._job(username="bob"))
        jobs.clear_job()
        self.assertEqual(jobs.saved_jobs(), [])

    def test_no_username_loads_the_most_recent(self):
        """What the CLI does before it knows whether the profile has a listing."""
        jobs.save_job(self._job(username="alice"))
        time.sleep(0.02)
        jobs.save_job(self._job(username="bob"))
        self.assertEqual(jobs.load_job().username, "bob")

    def test_saved_jobs_lists_them_all(self):
        jobs.save_job(self._job(username="alice"))
        jobs.save_job(self._job(username="bob"))
        self.assertEqual({j.username for j in jobs.saved_jobs()}, {"alice", "bob"})

    def test_no_listings_at_all_is_not_an_error(self):
        self.assertIsNone(jobs.load_job())
        self.assertIsNone(jobs.load_job("nobody"))
        self.assertEqual(jobs.saved_jobs(), [])
        jobs.clear_job("nobody")


class TestLegacyMigration(_TempJob):
    def _write_legacy(self, username: str) -> Path:
        path = jobs._legacy_job_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": jobs.SCHEMA_VERSION, "username": username,
            "bites": [dataclasses.asdict(_bite(1))], "created_at": time.time(),
        }), encoding="utf-8")
        return path

    def test_a_pre_1_1_job_is_moved_under_its_profile(self):
        legacy = self._write_legacy("someone")
        loaded = jobs.load_job("someone")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.username, "someone")
        self.assertFalse(legacy.exists(), "the old file should be gone, not copied")

    def test_migration_does_not_clobber_a_newer_listing(self):
        jobs.save_job(self._job(username="someone", bites=[_bite(1), _bite(2)]))
        self._write_legacy("someone")
        self.assertEqual(len(jobs.load_job("someone").bites), 2)

    def test_an_unreadable_legacy_file_is_discarded_quietly(self):
        legacy = jobs._legacy_job_path()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("{not json", encoding="utf-8")
        self.assertIsNone(jobs.load_job("someone"))   # must not raise
        self.assertFalse(legacy.exists())


class TestSelection(_TempJob):
    """Which blerps of a profile the user ticked, so a restart doesn't re-ask."""

    def test_the_selection_round_trips(self):
        chosen = [_bite(1).bite_id]
        jobs.save_job(self._job(selected=chosen))
        self.assertEqual(jobs.load_job("someone").selected, chosen)

    def test_no_selection_means_all_of_them(self):
        jobs.save_job(self._job())
        self.assertEqual(jobs.load_job("someone").selected, [])

    def test_a_selection_entry_that_is_not_an_objectid_is_dropped(self):
        """It is matched against bite ids that become filenames."""
        self.path.write_text(json.dumps({
            "version": jobs.SCHEMA_VERSION, "username": "someone",
            "bites": [dataclasses.asdict(_bite(1))], "created_at": time.time(),
            "selected": ["../../evil", _bite(1).bite_id, "nothex"],
        }), encoding="utf-8")
        self.assertEqual(jobs.load_job("someone").selected, [_bite(1).bite_id])

    def test_a_version_1_file_still_loads(self):
        """The only change is an added optional field, so refusing an older file
        would throw away a scan the user is part way through for nothing."""
        self.path.write_text(json.dumps({
            "version": 1, "username": "someone",
            "bites": [dataclasses.asdict(_bite(1))], "created_at": time.time(),
        }), encoding="utf-8")
        loaded = jobs.load_job("someone")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.selected, [])

    def test_a_future_version_is_still_refused(self):
        self.path.write_text(json.dumps({
            "version": 99, "username": "someone",
            "bites": [dataclasses.asdict(_bite(1))],
        }), encoding="utf-8")
        self.assertIsNone(jobs.load_job("someone"))


if __name__ == "__main__":
    unittest.main()

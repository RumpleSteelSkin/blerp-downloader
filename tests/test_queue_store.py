"""Tests for the saved download list.

Mostly about what the loader must refuse. The file sits in a directory anything
running as the user can write, and a row in it is fetched with no further
confirmation once Start is pressed, so it is treated as untrusted input.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import queue_store as qs  # noqa: E402

URL = "https://blerp.com/soundbites/0123456789abcdef01234567"
BITE = "0123456789abcdef01234567"


def single(**over):
    item = qs.QueueItem(item_id="a" * 32, kind="single", url=URL, bite_id=BITE,
                        title="Airhorn", added_at=time.time())
    for k, v in over.items():
        setattr(item, k, v)
    return item


def profile(**over):
    item = qs.QueueItem(item_id="b" * 32, kind="profile", url="someuser",
                        username="someuser", added_at=time.time())
    for k, v in over.items():
        setattr(item, k, v)
    return item


class _TempQueue(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "queue.json"
        self._patch = mock.patch.object(qs, "_queue_path", lambda: self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def write(self, items, *, version=qs.SCHEMA_VERSION):
        self.path.write_text(json.dumps({"version": version, "items": items}),
                             encoding="utf-8")


class TestRoundTrip(_TempQueue):
    def test_absent_file_is_an_empty_list(self):
        self.assertEqual(qs.load_queue(), [])

    def test_every_field_survives(self):
        item = single(status=qs.DONE, out_path=r"D:\out\a.mp4", error="",
                      limit=12, overwrite=True, done_count=3, total_count=9)
        qs.save_queue([item])
        self.assertEqual(qs.load_queue(), [item])

    def test_order_is_preserved(self):
        items = [single(item_id=f"{i:032x}") for i in range(5)]
        qs.save_queue(items)
        self.assertEqual([i.item_id for i in qs.load_queue()],
                         [i.item_id for i in items])

    def test_write_is_atomic(self):
        qs.save_queue([single()])
        self.assertEqual(list(self.path.parent.glob("*.part")), [])

    def test_a_failed_write_leaves_the_previous_list_intact(self):
        qs.save_queue([single(title="kept")])
        with mock.patch.object(Path, "write_text", side_effect=OSError("disk full")):
            qs.save_queue([single(title="lost")])   # must not raise
        self.assertEqual(qs.load_queue()[0].title, "kept")

    def test_clear_removes_the_file(self):
        qs.save_queue([single()])
        qs.clear_queue()
        self.assertFalse(self.path.exists())
        qs.clear_queue()    # idempotent


class TestTolerance(_TempQueue):
    def test_corrupt_json_is_an_empty_list(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(qs.load_queue(), [])

    def test_empty_file_is_an_empty_list(self):
        self.path.write_text("", encoding="utf-8")
        self.assertEqual(qs.load_queue(), [])

    def test_a_bom_does_not_discard_the_file(self):
        self.path.write_bytes(b"\xef\xbb\xbf" + json.dumps(
            {"version": qs.SCHEMA_VERSION,
             "items": [{"item_id": "x", "kind": "single", "url": URL}]}
        ).encode("utf-8"))
        self.assertEqual(len(qs.load_queue()), 1)

    def test_another_schema_version_is_discarded_whole(self):
        """Half-reading a list the user can see is worse than showing none: they
        would have no way to tell which rows went missing."""
        self.write([{"item_id": "x", "kind": "single", "url": URL}],
                   version=qs.SCHEMA_VERSION + 1)
        self.assertEqual(qs.load_queue(), [])

    def test_items_not_being_a_list_is_an_empty_list(self):
        self.path.write_text(json.dumps({"version": qs.SCHEMA_VERSION,
                                         "items": "nope"}), encoding="utf-8")
        self.assertEqual(qs.load_queue(), [])

    def test_one_bad_record_costs_one_row(self):
        self.write([
            {"item_id": "good1", "kind": "single", "url": URL, "title": "keep me"},
            "not even a dict",
            {"item_id": "good2", "kind": "single", "url": URL, "title": "me too"},
        ])
        self.assertEqual([i.title for i in qs.load_queue()], ["keep me", "me too"])

    def test_a_record_with_no_id_is_dropped(self):
        """The id is the row's identity in the tree; without it progress
        messages could not be routed back to the right row."""
        self.write([{"kind": "single", "url": URL}])
        self.assertEqual(qs.load_queue(), [])


class TestRefusesUntrustedRecords(_TempQueue):
    def test_a_non_http_url_is_rejected(self):
        for bad in ("file:///C:/Windows/win.ini", "data:text/html,x",
                    "ftp://blerp.com/x", "javascript:alert(1)", ""):
            self.write([{"item_id": "x", "kind": "single", "url": bad}])
            self.assertEqual(qs.load_queue(), [], bad)

    def test_a_url_that_is_not_blerp_is_rejected(self):
        """Start fetches these without asking again, so a lookalike host is the
        difference between a download the user chose and one they didn't."""
        for bad in ("https://evil.example/soundbites/" + BITE,
                    "https://blerp.com.attacker.tld/soundbites/" + BITE,
                    "https://attacker.tld/?u=https://blerp.com/soundbites/" + BITE):
            self.write([{"item_id": "x", "kind": "single", "url": bad}])
            self.assertEqual(qs.load_queue(), [], bad)

    def test_a_bite_id_that_is_not_an_objectid_is_rejected(self):
        """It becomes a filename and a cache path."""
        for bad in ("../../evil", r"..\..\evil", "C:/Windows/System32",
                    BITE + "x", "not hex at all!!!!!!!!!!!"):
            self.write([{"item_id": "x", "kind": "single", "url": URL,
                         "bite_id": bad}])
            self.assertEqual(qs.load_queue(), [], bad)

    def test_a_blank_bite_id_is_fine(self):
        """A row that hasn't been resolved yet genuinely has no id."""
        self.write([{"item_id": "x", "kind": "single", "url": URL, "bite_id": ""}])
        self.assertEqual(len(qs.load_queue()), 1)

    def test_a_username_shaped_like_a_path_is_rejected(self):
        """It reaches sanitize() and becomes an output folder."""
        for bad in ("../../etc", r"..\..\win", "a/b", "C:name", ""):
            self.write([{"item_id": "x", "kind": "profile", "username": bad}])
            self.assertEqual(qs.load_queue(), [], bad)

    def test_an_unknown_kind_is_rejected(self):
        self.write([{"item_id": "x", "kind": "torrent", "url": URL}])
        self.assertEqual(qs.load_queue(), [])

    def test_an_unknown_status_becomes_queued(self):
        self.write([{"item_id": "x", "kind": "single", "url": URL,
                     "status": "exfiltrating"}])
        self.assertEqual(qs.load_queue()[0].status, qs.QUEUED)

    def test_control_characters_are_stripped_from_the_title(self):
        """The title is drawn in the list; a newline would break the row."""
        self.write([{"item_id": "x", "kind": "single", "url": URL,
                     "title": "line\r\none\x00two"}])
        self.assertEqual(qs.load_queue()[0].title, "lineonetwo")

    def test_a_runaway_title_is_truncated(self):
        self.write([{"item_id": "x", "kind": "single", "url": URL,
                     "title": "z" * 100_000}])
        self.assertLessEqual(len(qs.load_queue()[0].title), 1000)

    def test_the_list_is_capped(self):
        self.write([{"item_id": f"{i:032x}", "kind": "single", "url": URL}
                    for i in range(qs.MAX_QUEUE_ITEMS + 50)])
        self.assertLessEqual(len(qs.load_queue()), qs.MAX_QUEUE_ITEMS)


class TestInterruptedRowsAreRetried(_TempQueue):
    def test_a_row_caught_mid_flight_goes_back_in_the_line(self):
        """This is how an unfinished link survives a restart. process_bite
        writes to a .part and only renames on success, so there is nothing to
        resume from within a row - it has to start again."""
        for status in (qs.DOWNLOADING, qs.RESOLVING):
            self.write([{"item_id": "x", "kind": "single", "url": URL,
                         "status": status}])
            self.assertEqual(qs.load_queue()[0].status, qs.QUEUED, status)

    def test_finished_rows_keep_their_status(self):
        for status in (qs.DONE, qs.FAILED, qs.SKIPPED, qs.STOPPED, qs.QUEUED):
            self.write([{"item_id": "x", "kind": "single", "url": URL,
                         "status": status}])
            self.assertEqual(qs.load_queue()[0].status, status)


class TestPrune(unittest.TestCase):
    def test_unfinished_rows_are_never_dropped(self):
        old = single(status=qs.QUEUED, added_at=time.time() - 400 * 86400)
        self.assertEqual(qs.prune([old]), [old])

    def test_stale_finished_rows_are_dropped(self):
        stale = single(item_id="s" * 32, status=qs.DONE,
                       added_at=time.time() - (qs.MAX_FINISHED_AGE_DAYS + 1) * 86400)
        fresh = single(item_id="f" * 32, status=qs.DONE, added_at=time.time())
        self.assertEqual(qs.prune([stale, fresh]), [fresh])

    def test_finished_rows_are_capped_keeping_the_newest(self):
        now = time.time()
        items = [single(item_id=f"{i:032x}", status=qs.DONE, added_at=now - i)
                 for i in range(qs.MAX_FINISHED_ITEMS + 25)]
        kept = qs.prune(items)
        self.assertEqual(len(kept), qs.MAX_FINISHED_ITEMS)
        self.assertIn(items[0], kept)      # newest
        self.assertNotIn(items[-1], kept)  # oldest

    def test_order_is_preserved(self):
        now = time.time()
        items = [single(item_id=f"{i:032x}", status=s, added_at=now)
                 for i, s in enumerate((qs.DONE, qs.QUEUED, qs.DONE, qs.FAILED))]
        self.assertEqual([i.item_id for i in qs.prune(items)],
                         [i.item_id for i in items])

    def test_a_finished_row_with_no_timestamp_is_kept(self):
        """Age is unknown, not infinite; dropping it would silently delete rows
        written by a version that didn't stamp them."""
        item = single(status=qs.DONE, added_at=0.0)
        self.assertEqual(qs.prune([item]), [item])


class TestSchemaGuard(unittest.TestCase):
    def test_the_record_shape_is_pinned(self):
        """save_queue writes every field reflectively but _clean_item reads them
        by name, so a field added to QueueItem is silently dropped on load until
        it is handled there - and the schema version has to move with it."""
        import dataclasses
        self.assertEqual(
            {f.name for f in dataclasses.fields(qs.QueueItem)},
            {"item_id", "kind", "url", "username", "bite_id", "title", "status",
             "error", "out_path", "added_at", "done_count", "total_count",
             "limit", "overwrite"},
            "QueueItem changed: update _clean_item and bump SCHEMA_VERSION")


if __name__ == "__main__":
    unittest.main()

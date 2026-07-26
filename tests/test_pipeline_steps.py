"""Tests for the per-bite progress callback.

The point of these is the contract, not the conversion: process_bite has to be
able to report where it is without changing a byte of what the CLI prints, and a
caller's broken callback must not be able to take a bulk run down.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import pipeline  # noqa: E402
from blerp_downloader.scraping import BiteMedia  # noqa: E402

MEDIA = BiteMedia(
    bite_id="0123456789abcdef01234567",
    title="Test Blerp",
    audio_url="https://cdn.blerp.com/a.mp3",
    image_url="https://cdn.blerp.com/i.webp",
    audio_duration_s=2.0,
)


class _StubbedConvert(unittest.TestCase):
    """Replaces everything _convert reaches out to, so only the flow is tested."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out.mp4"
        patches = (
            mock.patch.object(pipeline, "http_get", return_value=b"x"),
            mock.patch.object(pipeline, "extract_frames",
                              return_value=([Path("f0.png")], [100])),
            mock.patch.object(pipeline, "build_animation_video", return_value=1.0),
            mock.patch.object(pipeline, "probe_duration", return_value=2.0),
            mock.patch.object(pipeline, "mux"),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)


class TestOnStep(_StubbedConvert):
    def test_fires_once_per_stage_in_order(self):
        seen = []
        pipeline.process_bite(MEDIA, self.out, on_step=lambda i, n, label: seen.append((i, n, label)))
        self.assertEqual([i for i, _, _ in seen], [0, 1, 2, 3])
        self.assertEqual({n for _, n, _ in seen}, {len(pipeline.STEP_LABELS)})
        self.assertEqual([label for _, _, label in seen], list(pipeline.STEP_LABELS))

    def test_a_raising_callback_does_not_abort_the_download(self):
        """It runs on the download thread; one bad caller must cost nothing."""
        def boom(index, total, label):
            raise RuntimeError("callback is broken")

        pipeline.process_bite(MEDIA, self.out, on_step=boom)   # must not raise
        self.assertTrue(pipeline.mux.called)

    def test_omitting_it_is_free(self):
        pipeline.process_bite(MEDIA, self.out)                 # must not raise
        self.assertTrue(pipeline.mux.called)


class TestVerboseIsUnchanged(_StubbedConvert):
    def test_the_cli_output_is_byte_for_byte_the_same(self):
        """The CLI is the only verbose=True caller and its step lines are a
        documented part of its output; adding a callback must not disturb them."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            pipeline.process_bite(MEDIA, self.out, verbose=True)
        without = buf.getvalue()

        buf = io.StringIO()
        with redirect_stdout(buf):
            pipeline.process_bite(MEDIA, self.out, verbose=True, on_step=lambda *a: None)
        self.assertEqual(buf.getvalue(), without)

        for tag in ("[2/5]", "[3/5]", "[4/5]", "[5/5]"):
            self.assertIn(tag, without)

    def test_silent_without_verbose(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            pipeline.process_bite(MEDIA, self.out, on_step=lambda *a: None)
        self.assertEqual(buf.getvalue(), "")


class TestStepLabels(unittest.TestCase):
    def test_labels_match_the_stages_the_log_announces(self):
        """The labels and the [n/5] lines describe the same four stages; if one
        list grows the other has to, or the progress and the log disagree."""
        self.assertEqual(len(pipeline.STEP_LABELS), 4)
        self.assertTrue(all(pipeline.STEP_LABELS))


if __name__ == "__main__":
    unittest.main()

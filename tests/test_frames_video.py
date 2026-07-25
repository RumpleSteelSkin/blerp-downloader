"""Tests for the WebP frame parser, the sync policy and filename sanitising.

parse_anmf_durations walks bytes downloaded from the internet with manual
offsets, so the malformed-input cases below are the point of this file: it is
currently memory-safe only because Python slicing clamps, and a refactor that
checked the *declared* chunk size instead of the real payload length would
reintroduce an IndexError on every corrupt image with nothing to notice.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader.frames import parse_anmf_durations  # noqa: E402
from blerp_downloader.pipeline import MAX_TITLE_CHARS, sanitize  # noqa: E402
from blerp_downloader.video import _concat_quote, resolve_sync  # noqa: E402


def _chunk(fourcc: bytes, payload: bytes, declared: int | None = None) -> bytes:
    size = len(payload) if declared is None else declared
    out = fourcc + struct.pack("<I", size) + payload
    if len(payload) % 2:          # RIFF chunks are padded to an even boundary
        out += b"\0"
    return out


def _anmf(duration_ms: int) -> bytes:
    # frame_duration is a 24-bit little-endian value at bytes 12..14.
    body = bytearray(16)
    body[12] = duration_ms & 0xFF
    body[13] = (duration_ms >> 8) & 0xFF
    body[14] = (duration_ms >> 16) & 0xFF
    return _chunk(b"ANMF", bytes(body))


def _webp(*chunks: bytes) -> bytes:
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


class TestParseAnmfDurations(unittest.TestCase):
    def test_reads_frame_durations(self):
        self.assertEqual(parse_anmf_durations(_webp(_anmf(80), _anmf(120), _anmf(40))),
                         [80, 120, 40])

    def test_24_bit_values(self):
        self.assertEqual(parse_anmf_durations(_webp(_anmf(0x123456))), [0x123456])

    def test_other_chunks_are_skipped(self):
        data = _webp(_chunk(b"VP8X", b"\0" * 10), _anmf(50), _chunk(b"ICCP", b"x" * 5))
        self.assertEqual(parse_anmf_durations(data), [50])

    def test_not_a_webp(self):
        for data in (b"", b"RIFF", b"RIFF\0\0\0\0AVI ", b"\x89PNG\r\n\x1a\n", b"GIF89a"):
            self.assertEqual(parse_anmf_durations(data), [], data[:8])

    def test_a_lying_chunk_size_does_not_crash(self):
        """The declared size claims far more than is present."""
        data = _webp(_chunk(b"ANMF", b"\0" * 16, declared=0xFFFFFFF0))
        self.assertIsInstance(parse_anmf_durations(data), list)

    def test_truncated_anmf_payload_is_ignored(self):
        """Fewer than 16 bytes present: the duration field isn't there to read."""
        data = _webp(_chunk(b"ANMF", b"\0" * 4))
        self.assertEqual(parse_anmf_durations(data), [])

    def test_zero_size_chunks_terminate(self):
        data = _webp(_chunk(b"ANMF", b""), _chunk(b"ANMF", b""))
        self.assertIsInstance(parse_anmf_durations(data), list)

    def test_truncated_mid_header(self):
        self.assertIsInstance(parse_anmf_durations(_webp(b"ANMF\x10")), list)

    def test_random_bytes_never_raise(self):
        import random
        rnd = random.Random(1234)      # fixed seed: a failure is reproducible
        for _ in range(200):
            blob = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 80)))
            parse_anmf_durations(b"RIFF" + struct.pack("<I", len(blob) + 4) + b"WEBP" + blob)


class TestResolveSync(unittest.TestCase):
    """Audio is the content and the animation decorates it, so the output is
    always the audio's length."""

    def test_target_is_always_the_audio_length(self):
        self.assertAlmostEqual(resolve_sync(2.0, 7.0).target_duration, 7.0)
        self.assertAlmostEqual(resolve_sync(9.0, 3.0).target_duration, 3.0)

    def test_short_animation_loops(self):
        self.assertTrue(resolve_sync(2.0, 10.0).loop_video)

    def test_long_animation_is_cut_not_looped(self):
        self.assertFalse(resolve_sync(10.0, 2.0).loop_video)

    def test_near_equal_durations_do_not_loop(self):
        """The documented reason the tolerance exists: without it a sliver of a
        second pass is appended when the two are effectively the same length."""
        self.assertFalse(resolve_sync(5.96, 5.97).loop_video)
        self.assertFalse(resolve_sync(5.99, 6.0).loop_video)

    def test_just_beyond_the_tolerance_loops(self):
        self.assertTrue(resolve_sync(5.0, 5.2).loop_video)

    def test_audio_is_never_padded(self):
        for pair in ((1.0, 9.0), (9.0, 1.0), (3.0, 3.0)):
            self.assertFalse(resolve_sync(*pair).pad_audio_with_silence, pair)


class TestConcatQuote(unittest.TestCase):
    def test_plain_path(self):
        self.assertEqual(_concat_quote(Path("C:/tmp/frame.png")), "'C:/tmp/frame.png'")

    def test_apostrophe_in_the_path_is_escaped(self):
        """A Windows user named O'Brien otherwise ends the quoted token early
        and every download fails."""
        out = _concat_quote(Path(r"C:/Users/O'Brien/f.png"))
        self.assertEqual(out, r"'C:/Users/O'\''Brien/f.png'")
        self.assertTrue(out.startswith("'") and out.endswith("'"))


class TestSanitize(unittest.TestCase):
    def test_illegal_characters_become_underscores(self):
        self.assertEqual(sanitize('a<b>c:d"e/f\\g|h?i*j'), "a_b_c_d_e_f_g_h_i_j")

    def test_control_characters_are_removed(self):
        self.assertEqual(sanitize("line1\nline2"), "line1 line2")
        self.assertEqual(sanitize("tab\there"), "tab here")
        self.assertNotIn("\x07", sanitize("\x07bell"))

    def test_windows_device_names_are_defused(self):
        """NUL.mp4 is the null device: ffmpeg writes to it, exits 0, and the run
        reports success having produced no file."""
        for name in ("CON", "NUL", "com1", "LPT9", "aux.wav", "prn"):
            self.assertNotEqual(sanitize(name).split(".")[0].lower(),
                                name.split(".")[0].lower(), name)

    def test_ordinary_names_are_left_alone(self):
        for name in ("normal title", "Consolation Prize", "AUXILIARY", "com10"):
            self.assertEqual(sanitize(name), name, name)

    def test_trailing_dots_and_spaces_are_dropped(self):
        self.assertEqual(sanitize("trail."), "trail")
        self.assertEqual(sanitize("trail  "), "trail")
        self.assertEqual(sanitize("..."), "blerp")

    def test_length_is_capped_leaving_room_for_the_id_suffix(self):
        out = sanitize("x" * 500)
        self.assertLessEqual(len(out), MAX_TITLE_CHARS)
        # bulk mode appends "_<24 hex>.mp4"
        self.assertLess(len(out) + 29, 255)

    def test_empty_input_gets_a_fallback(self):
        for raw in ("", "   ", None, "///"):
            self.assertTrue(sanitize(raw), repr(raw))


if __name__ == "__main__":
    unittest.main()

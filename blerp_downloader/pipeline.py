"""Shared CLI+GUI core: filename sanitizing and the per-blerp download+convert pipeline."""

from __future__ import annotations

import contextlib
import re
import tempfile
from pathlib import Path

from .ffmpeg_utils import probe_duration
from .frames import extract_frames
from .network import http_get
from .scraping import BiteMedia
from .video import build_animation_video, mux, resolve_sync


# Reserved on Windows with or without an extension: "NUL.mp4" is still the null
# device, which swallows ffmpeg's output while letting it exit 0.
_RESERVED = {"con", "prn", "aux", "nul",
             *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}

# Leaves room for the "_<24-hex ObjectId>.mp4" that bulk mode appends, inside the
# 255-character limit on a single path component.
MAX_TITLE_CHARS = 200


def sanitize(name: str) -> str:
    """Turns a blerp title into a filename component that Windows will accept."""
    name = re.sub(r'[\\/:*?"<>|]+', "_", name or "")
    # Control characters are rejected by the filesystem and would otherwise reach
    # ffmpeg as a literal newline or tab inside the output path.
    name = re.sub(r"[\x00-\x1f\x7f]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    # A trailing dot or space is silently dropped by Windows, which would make
    # the name we check for existence differ from the one on disk.
    name = name.rstrip(". ").strip()
    if not name:
        return "blerp"
    if len(name) > MAX_TITLE_CHARS:
        name = name[:MAX_TITLE_CHARS].rstrip(". ").strip() or "blerp"
    if name.split(".")[0].lower() in _RESERVED:
        name = "_" + name
    return name


# Stopping after this many consecutive failures. Past that the cause is the
# environment (offline, profile wiped, disk full), and every remaining bite would
# burn its own network timeout proving the same thing.
FAILURE_STREAK_LIMIT = 10


def bulk_out_path(out_dir: Path, media: BiteMedia) -> Path:
    """Where a bite lands in bulk mode.

    The blerp ID is in the name so it is both unique and identical across runs,
    which is what lets "already exists" stand in for "already downloaded".
    Shared by both front-ends so the two can never disagree about which file a
    bite corresponds to - a mismatch would silently re-download everything.
    """
    return out_dir / f"{sanitize(media.title)}_{media.bite_id}.mp4"


def process_bite(media: BiteMedia, out_path: Path, *, verbose: bool = False) -> None:
    """
    Downloads and converts one BiteMedia (audio+image URLs already resolved) to MP4.
    Shared by both single and bulk mode; propagates network/ffmpeg errors upward.
    """
    def log(msg: str) -> None:
        if verbose:
            print(msg)

    # blerpdl_, not blerp_: the cache cleaner matches on this prefix, and the
    # shorter one isn't specific enough to this app to delete on sight.
    with tempfile.TemporaryDirectory(prefix="blerpdl_") as td:
        tmp = Path(td)
        # Held open for the life of the download. Windows won't let another
        # process unlink an open file, so this is what tells the cache cleaner
        # that this directory belongs to a run still in progress.
        with _scratch_lock(tmp):
            _convert(media, out_path, tmp, log)


@contextlib.contextmanager
def _scratch_lock(tmp: Path):
    """Marks a scratch directory as in use. Never fails the download."""
    handle = None
    try:
        handle = (tmp / ".lock").open("w")
    except OSError:
        pass
    try:
        yield
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


def _convert(media: BiteMedia, out_path: Path, tmp: Path, log) -> None:
    """Download, split, encode, mux - all inside the scratch directory `tmp`."""
    webp_path, mp3_path = tmp / "image.webp", tmp / "audio.mp3"

    log("[2/5] Downloading media...")
    webp_path.write_bytes(http_get(media.image_url))
    mp3_path.write_bytes(http_get(media.audio_url))

    log("[3/5] Extracting WebP frames...")
    frames, durations = extract_frames(webp_path, tmp / "frames")
    log(f"      {len(frames)} frames, ~{sum(durations)/1000:.2f}s animation")

    log("[4/5] Building animation video...")
    anim = tmp / "anim.mp4"
    video_dur = build_animation_video(frames, durations, anim)

    # Audio length: measure the real file first (ground truth), fall back to metadata.
    audio_dur = probe_duration(mp3_path) or media.audio_duration_s or video_dur
    plan = resolve_sync(video_dur, audio_dur)
    log(f"      Plan: target={plan.target_duration:.2f}s "
        f"loop_video={plan.loop_video} pad_audio={plan.pad_audio_with_silence}")

    log("[5/5] Combining audio + video...")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mux(anim, mp3_path, plan, out_path)

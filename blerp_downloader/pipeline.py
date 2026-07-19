"""Shared CLI+GUI core: filename sanitizing and the per-blerp download+convert pipeline."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .ffmpeg_utils import probe_duration
from .frames import extract_frames
from .network import http_get
from .scraping import BiteMedia
from .video import build_animation_video, mux, resolve_sync


def sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "blerp"


def process_bite(media: BiteMedia, out_path: Path, *, verbose: bool = False) -> None:
    """
    Downloads and converts one BiteMedia (audio+image URLs already resolved) to MP4.
    Shared by both single and bulk mode; propagates network/ffmpeg errors upward.
    """
    def log(msg: str) -> None:
        if verbose:
            print(msg)

    with tempfile.TemporaryDirectory(prefix="blerp_") as td:
        tmp = Path(td)
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

"""FFmpeg video assembly: silent animation build, audio/video sync policy, and mux."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_utils import NO_WINDOW_FLAGS, ffmpeg_path


def build_animation_video(frames: list[Path], durations_ms: list[int], out_path: Path) -> float:
    """
    Converts PNG frames into a silent h264 MP4 with true per-frame durations (concat
    demuxer). Returns the produced video's duration in seconds.
    """
    list_file = out_path.with_suffix(".concat.txt")
    lines = []
    for fp, dur in zip(frames, durations_ms):
        p = fp.resolve().as_posix()  # ffmpeg concat wants forward slashes + quotes
        lines.append(f"file '{p}'")
        lines.append(f"duration {dur / 1000.0:.4f}")
    # The concat demuxer ignores the last entry's duration, so the last frame is repeated.
    lines.append(f"file '{frames[-1].resolve().as_posix()}'")
    list_file.write_text("\n".join(lines), encoding="utf-8")

    cmd = [
        ffmpeg_path(), "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-vsync", "vfr",                 # preserves variable frame durations
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, creationflags=NO_WINDOW_FLAGS)
    list_file.unlink(missing_ok=True)
    return sum(durations_ms) / 1000.0


@dataclass
class SyncPlan:
    """mux() builds its FFmpeg arguments from this plan."""
    target_duration: float          # final video length in seconds
    loop_video: bool                # loop the animation from the start if it's shorter
    pad_audio_with_silence: bool    # pad audio with silence if it's shorter


def resolve_sync(video_duration: float, audio_duration: float) -> SyncPlan:
    """
    Policy: "audio is king" - the final video length always equals the audio length.

    - Audio is a blerp's primary content and the GIF just decorates it, so audio is never cut.
    - If the GIF is meaningfully shorter, it's looped from the start until the audio ends.
    - If the GIF is longer, it's cut once the audio ends (-t).
    - Since the target is already the audio length, audio is never padded with silence.

    TOLERANCE: a small threshold avoids adding a pointless loop artifact when durations
    are nearly equal (e.g. 5.96s vs 5.97s).
    """
    TOLERANCE = 0.05  # seconds
    return SyncPlan(
        target_duration=audio_duration,
        loop_video=(video_duration + TOLERANCE < audio_duration),
        pad_audio_with_silence=False,
    )


def mux(anim_video: Path, audio_path: Path, plan: SyncPlan, out_path: Path) -> None:
    """Combines the silent animation video + mp3 into the final MP4 per the SyncPlan."""
    cmd = [ffmpeg_path(), "-y", "-loglevel", "error"]
    if plan.loop_video:
        cmd += ["-stream_loop", "-1"]          # loop indefinitely (cut later with -t)
    cmd += ["-i", str(anim_video), "-i", str(audio_path)]
    cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-af", "apad" if plan.pad_audio_with_silence else "anull"]
    cmd += ["-t", f"{plan.target_duration:.4f}"]   # cut to the final length
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, creationflags=NO_WINDOW_FLAGS)

"""FFmpeg video assembly: silent animation build, audio/video sync policy, and mux."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import BlerpError
from .ffmpeg_utils import ffmpeg_path, hidden_process_kwargs

# Encoding a long animation is slow but not unbounded; without a limit a wedged
# ffmpeg parks the worker thread forever and Stop can't help.
FFMPEG_TIMEOUT = 600


def _concat_quote(path: Path) -> str:
    """Renders a path for the concat demuxer's `file` directive.

    The value is single-quoted, and a literal quote is escaped the way the
    demuxer expects ('\\''). Without this, a Windows user whose name contains an
    apostrophe - C:\\Users\\O'Brien\\... - ends the token early and every single
    download fails.
    """
    return "'" + path.resolve().as_posix().replace("'", r"'\''") + "'"


def run_ffmpeg(cmd: list[str], *, what: str) -> None:
    """Runs ffmpeg, turning a failure into a message that names the cause.

    stderr was previously discarded, so every media failure surfaced as a
    30-element argv dump with no diagnostic content.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=FFMPEG_TIMEOUT, **hidden_process_kwargs())
    except FileNotFoundError:
        raise BlerpError("FFmpeg could not be run. Check that it is installed and on PATH.")
    except subprocess.TimeoutExpired:
        raise BlerpError(f"FFmpeg timed out after {FFMPEG_TIMEOUT}s while {what}.")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = " | ".join(detail[-3:]) if detail else "no output"
        raise BlerpError(f"FFmpeg failed while {what}: {tail}")


def build_animation_video(frames: list[Path], durations_ms: list[int], out_path: Path) -> float:
    """
    Converts PNG frames into a silent h264 MP4 with true per-frame durations (concat
    demuxer). Returns the produced video's duration in seconds.
    """
    if not frames:
        raise BlerpError("The image has no frames to build a video from.")

    list_file = out_path.with_suffix(".concat.txt")
    lines = []
    for fp, dur in zip(frames, durations_ms):
        lines.append(f"file {_concat_quote(fp)}")
        lines.append(f"duration {dur / 1000.0:.4f}")
    # The concat demuxer ignores the last entry's duration, so the last frame is repeated.
    lines.append(f"file {_concat_quote(frames[-1])}")
    list_file.write_text("\n".join(lines), encoding="utf-8")

    cmd = [
        ffmpeg_path(), "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-vsync", "vfr",                 # preserves variable frame durations
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        run_ffmpeg(cmd, what="building the animation")
    finally:
        list_file.unlink(missing_ok=True)
    # Includes the repeated final frame, which is really played: reporting the
    # bare sum would under-report the file we just produced and skew resolve_sync.
    return (sum(durations_ms) + (durations_ms[-1] if durations_ms else 0)) / 1000.0


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
    """Combines the silent animation video + mp3 into the final MP4 per the SyncPlan.

    Encoded to a sibling .part and moved into place only on success. ffmpeg
    creates its output immediately and fills it as it goes, so writing straight
    to out_path leaves a truncated, unplayable MP4 behind whenever a run is
    interrupted - and since resume is a plain "does the file exist?" check, the
    next run would report it as already downloaded and never repair it.

    Staged beside the destination rather than in the temp directory so the final
    move is a same-volume rename: os.replace can't move across volumes on
    Windows, and %TEMP% is often on a different drive from the output folder.
    """
    tmp = out_path.parent / (out_path.name + ".part")

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
        # ffmpeg picks the container from the extension, so keep .mp4 before .part
        "-f", "mp4",
        str(tmp),
    ]
    try:
        run_ffmpeg(cmd, what="combining audio and video")
        if not tmp.exists() or tmp.stat().st_size == 0:
            # NUL and other reserved device names swallow the output and let
            # ffmpeg exit 0, which would otherwise be reported as success.
            raise BlerpError("FFmpeg reported success but produced no output file.")
        tmp.replace(out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

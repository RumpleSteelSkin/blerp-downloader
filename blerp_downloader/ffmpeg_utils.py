"""FFmpeg/ffprobe detection, user guidance, and shared subprocess flags."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .settings import load_settings

# Suppresses the console window ffmpeg/ffprobe would otherwise flash open for every
# call when running from the windowed (GUI) exe.
NO_WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

FFMPEG_DOWNLOAD_URL = "https://ffmpeg.org/download.html"
WINGET_FFMPEG = "winget install Gyan.FFmpeg"
FFMPEG_HELP = (
    "FFmpeg not found - this app needs FFmpeg to combine the video.\n\n"
    "Easiest install on Windows (run in Command Prompt/PowerShell):\n"
    f"    {WINGET_FFMPEG}\n\n"
    f"Alternative: download it from {FFMPEG_DOWNLOAD_URL} and add\n"
    "ffmpeg.exe and ffprobe.exe to PATH.\n\n"
    "Restart the app after installing."
)


def _resolve_binary(name: str) -> str:
    """Resolves an ffmpeg/ffprobe-family binary: the configured ffmpeg_dir
    override if set and the exe actually exists there, otherwise the bare
    command name (today's PATH-reliant behavior)."""
    override = load_settings().ffmpeg_dir
    if not override:
        return name
    exe_name = f"{name}.exe" if os.name == "nt" else name
    candidate = Path(override) / exe_name
    return str(candidate) if candidate.is_file() else name


def ffmpeg_path() -> str:
    return _resolve_binary("ffmpeg")


def ffprobe_path() -> str:
    return _resolve_binary("ffprobe")


def has_ffmpeg() -> bool:
    """Whether ffmpeg and ffprobe are both resolvable (override folder or PATH)."""
    return shutil.which(ffmpeg_path()) is not None and shutil.which(ffprobe_path()) is not None


def probe_duration(media_path: Path) -> float | None:
    """Measures a media file's true duration (s) with ffprobe; returns None on failure."""
    try:
        out = subprocess.run(
            [ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)],
            capture_output=True, text=True, check=True, creationflags=NO_WINDOW_FLAGS,
        ).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):
        return None

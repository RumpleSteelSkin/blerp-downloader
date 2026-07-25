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


def hidden_process_kwargs() -> dict:
    """subprocess keyword args that keep any console window from appearing.

    CREATE_NO_WINDOW covers the process we start ourselves. STARTUPINFO's
    SW_HIDE is additionally inherited by any process that one starts in turn,
    which matters whenever ffmpeg is reached through a forwarder rather than
    directly.

    Always call this instead of passing creationflags directly, so no call site
    can silently reintroduce a visible window.
    """
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": NO_WINDOW_FLAGS, "startupinfo": si}


def _dereference_shim(path: Path) -> Path:
    """Follows a Chocolatey shim to the real executable it launches.

    Chocolatey puts a small forwarder in chocolatey\\bin and keeps the actual
    program under chocolatey\\lib. Running the forwarder means an extra process
    that starts the real one itself, and the window behaviour of that
    grandchild is up to the forwarder rather than our creation flags - which is
    how a console can still flash despite CREATE_NO_WINDOW. Calling the real
    executable directly removes that layer (and one process per call).
    """
    try:
        if path.parent.name.lower() != "bin":
            return path
        if not any(p.lower() == "chocolatey" for p in path.parts):
            return path
        shim_size = path.stat().st_size
        for candidate in (path.parents[1] / "lib").glob(f"*/tools/**/{path.name}"):
            # The real binary is far larger than the forwarder; this guards
            # against matching another shim or a stub.
            if candidate.is_file() and candidate.stat().st_size > shim_size * 4:
                return candidate
    except (OSError, IndexError):
        pass
    return path


_resolved: dict[tuple[str, str], str] = {}


def _resolve_binary(name: str) -> str:
    """Resolves an ffmpeg/ffprobe-family binary: the configured ffmpeg_dir
    override if set, otherwise whatever is on PATH, with Chocolatey shims
    followed to the real executable.

    Successful lookups are cached (they involve a directory walk and run for
    every ffmpeg call), but failures are not: ffmpeg may be installed while the
    app is open, via the GUI's winget offer.
    """
    override = load_settings().ffmpeg_dir
    key = (name, override)
    if key in _resolved:
        return _resolved[key]

    if override:
        exe_name = f"{name}.exe" if os.name == "nt" else name
        candidate = Path(override) / exe_name
        if candidate.is_file():
            _resolved[key] = str(candidate)
            return _resolved[key]

    found = shutil.which(name)
    if not found:
        return name  # uncached, so a later install is picked up
    _resolved[key] = str(_dereference_shim(Path(found)))
    return _resolved[key]


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
            capture_output=True, text=True, check=True, **hidden_process_kwargs(),
        ).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):
        return None

"""Cleaning up after the app: downloaded installers, abandoned scratch space,
and leftover part-files.

Everything here is best-effort and refuses to touch anything that might belong
to a download still in progress - including one in a second copy of the app.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import jobs, updater

# Scratch directories created by process_bite. The first is the current prefix;
# the second is what builds before 1.0.6 used, which was not specific enough to
# this app to be safe to match on its own - swept only via the lock/age checks
# below, and dropped once those builds are gone.
TEMP_PREFIXES = ("blerpdl_", "blerp_")

# The name process_bite keeps open for the life of a download.
LOCK_NAME = ".lock"

# For scratch left by a build with no lock file. Must comfortably exceed
# video.FFMPEG_TIMEOUT (600s): during the mux the scratch directory sits
# untouched while ffmpeg writes to the output folder, so a shorter window would
# delete a live run's frames.
STALE_TEMP_SECONDS = 3600

# Exactly the suffix mux stages to. Never widen this to "*.mp4*": one character
# turns tidying up into deleting the user's downloads.
PART_GLOB = "*.mp4.part"


@dataclass
class CleanupResult:
    freed_bytes: int = 0
    removed: int = 0
    in_use: int = 0                       # skipped because something has them open
    details: list[str] = field(default_factory=list)

    @property
    def freed_mb(self) -> float:
        return self.freed_bytes / 1_048_576


def _size_of(path: Path) -> int:
    try:
        if path.is_dir():
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        return path.stat().st_size
    except OSError:
        return 0


def clear_updates(result: CleanupResult) -> None:
    """Removes downloaded installers, whatever their age.

    cleanup_old_downloads only touches ones older than a week and only runs when
    the GUI starts; this is the explicit "reclaim it now" path.
    """
    try:
        entries = list(updater.updates_dir().glob("*"))
    except OSError:
        return
    for p in entries:
        if p.suffix.lower() not in (".exe", ".part"):
            continue
        size = _size_of(p)
        try:
            p.unlink()
        except OSError:
            # An installer being downloaded right now is held open.
            result.in_use += 1
            continue
        result.freed_bytes += size
        result.removed += 1
    if result.removed:
        result.details.append(f"{result.removed} downloaded installer(s)")


def _temp_is_free(d: Path) -> bool:
    """Whether nothing is using this scratch directory.

    Windows refuses to unlink a file another process holds open, so deleting the
    lock is itself the liveness test - the same trick the updater relies on.
    Note that os.kill(pid, 0) is NOT usable as a probe here: on Windows CPython
    maps it to TerminateProcess, so it would kill the very process it was asking
    about. Nor can rmtree be attempted and caught, because it deletes a live
    run's frames on the way to failing on the open file.
    """
    lock = d / LOCK_NAME
    if lock.exists():
        if sys.platform != "win32":
            # Unlinking an open file succeeds here, so the probe proves nothing.
            return _older_than(d, STALE_TEMP_SECONDS)
        try:
            lock.unlink()
            return True
        except OSError:
            return False
    # Written by a build that predates the lock, so fall back on age.
    return _older_than(d, STALE_TEMP_SECONDS)


def _older_than(path: Path, seconds: int) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) > seconds
    except OSError:
        return False


def clear_temp(result: CleanupResult) -> None:
    """Removes scratch directories left behind by runs that were killed."""
    root = Path(tempfile.gettempdir())
    removed = 0
    try:
        candidates = [d for d in root.iterdir()
                      if d.is_dir() and d.name.startswith(TEMP_PREFIXES)]
    except OSError:
        return
    for d in candidates:
        if not _temp_is_free(d):
            result.in_use += 1
            continue
        size = _size_of(d)
        try:
            shutil.rmtree(d, ignore_errors=False)
        except OSError:
            result.in_use += 1
            continue
        result.freed_bytes += size
        result.removed += 1
        removed += 1
    if removed:
        result.details.append(f"{removed} leftover temp folder(s)")


def output_dirs(*extra: str) -> list[Path]:
    """Folders the app can prove it wrote to.

    Never inferred: with no output folder set the destination is relative to the
    working directory, which for a shortcut launch is wherever the installer
    pointed it - not something to go deleting in.
    """
    from .settings import load_settings

    found: list[Path] = []
    seen: set[str] = set()
    job = jobs.load_job()
    for raw in (*extra, load_settings().output_dir, job.out_dir if job else ""):
        if not raw:
            continue
        try:
            p = Path(raw).resolve()
        except OSError:
            continue
        key = os.path.normcase(str(p))
        if key not in seen and p.is_dir():
            seen.add(key)
            found.append(p)
    return found


def clear_part_files(result: CleanupResult, dirs: list[Path]) -> None:
    """Removes half-written downloads from the user's own output folders.

    Opt-in, because these are harmless - the next attempt overwrites them - and
    this is the one place the app deletes inside a directory the user chose.
    A part-file belonging to a live run is held open by ffmpeg, so unlink fails
    and it is skipped.
    """
    removed = 0
    for d in dirs:
        try:
            parts = list(d.glob(PART_GLOB))   # never rglob: no recursion
        except OSError:
            continue
        for p in parts:
            size = _size_of(p)
            try:
                p.unlink()
            except OSError:
                result.in_use += 1
                continue
            result.freed_bytes += size
            result.removed += 1
            removed += 1
    if removed:
        result.details.append(f"{removed} unfinished download file(s)")


def clear_cache(*, include_outputs: bool = False, output_dirs_: list[Path] | None = None,
                forget_job: bool = True) -> CleanupResult:
    """Clears everything the app cached. Never raises."""
    result = CleanupResult()
    clear_updates(result)
    clear_temp(result)
    if include_outputs:
        clear_part_files(result, output_dirs_ if output_dirs_ is not None else output_dirs())
    if forget_job:
        job = jobs.load_job()
        jobs.clear_job()
        if job:
            result.details.append(f"the unfinished download for {job.username}")
    return result

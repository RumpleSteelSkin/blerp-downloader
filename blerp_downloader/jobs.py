"""Saved state for an interrupted bulk download.

Only the *scan* is recorded, never per-bite progress. Walking a profile takes
minutes (the API returns 12 items a page) while working out what is already
downloaded is free: mux writes to a .part and only renames on success, so a file
existing means it is complete. Progress is therefore read back off the
filesystem, which means this file is written once and never touched again by the
worker thread - and the GUI's close path, which does not join that thread, has
nothing to race with.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import BlerpError
from .network import require_web_url
from .scraping import OBJECTID_RE, BiteMedia

APP_DIR_NAME = "BlerpDownloader"

# Bumped whenever the on-disk shape changes; an unrecognised version is ignored
# rather than guessed at.
SCHEMA_VERSION = 2

# Versions this build can still read. 1 differs only by not carrying `selected`,
# which defaults to "all of them" - so refusing it would throw away a scan the
# user is part way through for no gain.
READABLE_VERSIONS = (1, 2)

# A listing is a snapshot. Past this, re-scan rather than work from something
# that predates whatever the user is trying to do now.
MAX_AGE_DAYS = 30

_MAX_JOB_BYTES = 20 * 1024 * 1024   # a 3000-bite listing is roughly 1 MB
_JOB_GLOB = "*.json"


def state_dir() -> Path:
    """Where per-machine state lives - the same root as downloaded installers."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_DIR_NAME
    return Path.home() / ".cache" / "blerp-downloader"


def _jobs_dir() -> Path:
    return state_dir() / "jobs"


def _job_key(username: str) -> str:
    """A filename for one profile's saved listing.

    Hashed rather than sanitised: a blerp username is arbitrary text, and this
    becomes a path. Case-folded because the app matches profiles that way, so
    "SomeUser" and "someuser" must land on one file rather than two.
    """
    name = (username or "").strip().lower()
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]


def _job_path(username: str = "") -> Path:
    return _jobs_dir() / f"{_job_key(username)}.json"


def _legacy_job_path() -> Path:
    """Where the single saved listing lived before they were keyed by profile."""
    return state_dir() / "job.json"


def _migrate_legacy_job() -> None:
    """Moves a pre-1.1 job.json under its profile's name.

    One file meant only one profile could ever be resumed; the download list can
    hold several. Done on read rather than at startup so it costs nothing until
    something actually looks for a listing.
    """
    legacy = _legacy_job_path()
    if not legacy.exists():
        return

    username = ""
    try:
        data = json.loads(legacy.read_bytes()[:_MAX_JOB_BYTES].decode("utf-8-sig"))
        if isinstance(data, dict):
            username = str(data.get("username") or "")
    except (OSError, ValueError):
        pass   # unreadable; it still has to go, or it is re-parsed on every read

    try:
        if username:
            target = _job_path(username)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                legacy.replace(target)
                return
        legacy.unlink(missing_ok=True)   # unusable, or already superseded
    except OSError:
        pass


@dataclass
class Job:
    """A profile scan, with the options it was started under."""
    username: str
    bites: list           # full scan, before any --limit slice
    dropped: int = 0
    limit: int | None = None
    overwrite: bool = False
    out_dir: str = ""
    selected: list = dataclasses.field(default_factory=list)  # chosen bite ids
    scan_complete: bool = True
    created_at: float = 0.0
    app_version: str = ""

    @property
    def age_days(self) -> float:
        return (time.time() - self.created_at) / 86400 if self.created_at else 0.0

    def matches(self, username: str) -> bool:
        """Whether this job is the one for `username`.

        Keyed on the username alone: the listing is a property of the profile,
        not of where the files are going, so changing the output folder must not
        throw the scan away. Case-folded because list_user_bites resolves a
        canonical name and then discards it, leaving whatever the user typed.
        """
        return self.username.strip().lower() == (username or "").strip().lower()

    def is_usable(self) -> bool:
        if not self.bites or self.age_days > MAX_AGE_DAYS:
            return False
        # An overwrite run ignores what is on disk, so there is nothing to work
        # out how far it got from: resuming would restart at the first bite and
        # the stop/resume cycle would never end.
        return not self.overwrite


def _clean_bite(raw: object) -> BiteMedia | None:
    """One record from the file, or None if it isn't usable.

    Fields are read individually rather than splatted: an extra or missing key
    would raise, and one malformed record should cost one bite, not the job.
    The URLs are re-validated because this file lives in a directory any process
    running as the user can write, and they are handed straight to http_get.
    """
    if not isinstance(raw, dict):
        return None
    try:
        bite_id = str(raw["bite_id"])
        audio_url = require_web_url(str(raw["audio_url"]))
        image_url = require_web_url(str(raw["image_url"]))
        if not bite_id:
            return None
        return BiteMedia(
            bite_id=bite_id,
            title=str(raw.get("title") or bite_id),
            audio_url=audio_url,
            image_url=image_url,
            audio_duration_s=float(raw.get("audio_duration_s") or 0.0),
        )
    except (KeyError, TypeError, ValueError, BlerpError):
        return None


def load_job(username: str = "") -> Job | None:
    """The saved job for a profile, or None.

    With no username, the most recently saved one - which is what the CLI wants
    when it is about to check whether the profile it was given has a listing.

    Never raises: a job that can't be read just means the profile gets scanned
    again.
    """
    _migrate_legacy_job()
    path = _job_path(username) if username else _most_recent_job()
    return _read_job(path) if path is not None else None


def _read_job(path: Path) -> Job | None:
    try:
        raw = path.read_bytes()[:_MAX_JOB_BYTES]
        data = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") not in READABLE_VERSIONS:
        return None

    bites = [b for b in (_clean_bite(x) for x in (data.get("bites") or [])) if b]
    if not bites:
        return None
    try:
        return Job(
            username=str(data.get("username") or ""),
            bites=bites,
            dropped=int(data.get("dropped") or 0),
            limit=(int(data["limit"]) if data.get("limit") is not None else None),
            overwrite=bool(data.get("overwrite")),
            out_dir=str(data.get("out_dir") or ""),
            selected=[str(x) for x in (data.get("selected") or [])
                      if OBJECTID_RE.fullmatch(str(x))],
            scan_complete=bool(data.get("scan_complete", True)),
            created_at=float(data.get("created_at") or 0.0),
            app_version=str(data.get("app_version") or ""),
        )
    except (TypeError, ValueError):
        return None


def _most_recent_job() -> Path | None:
    """The newest saved listing, or None if there are none."""
    try:
        paths = list(_jobs_dir().glob(_JOB_GLOB))
    except OSError:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime, default=None) if paths else None


def save_job(job: Job) -> None:
    """Writes the job atomically. Best-effort: failing to record a resume point
    must never take down the download it belongs to."""
    path = _job_path(job.username)
    tmp = path.with_suffix(path.suffix + ".part")
    payload = {
        "version": SCHEMA_VERSION,
        "username": job.username,
        "bites": [dataclasses.asdict(b) for b in job.bites],
        "dropped": job.dropped,
        "limit": job.limit,
        "overwrite": job.overwrite,
        "out_dir": job.out_dir,
        "selected": list(job.selected),
        "scan_complete": job.scan_complete,
        "created_at": job.created_at or time.time(),
        "app_version": job.app_version,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Same directory as the destination so the rename is same-volume.
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def clear_job(username: str = "") -> None:
    """Forgets a profile's saved listing, or every one of them. Best-effort."""
    try:
        if username:
            _job_path(username).unlink(missing_ok=True)
            return
        _legacy_job_path().unlink(missing_ok=True)
        for path in _jobs_dir().glob(_JOB_GLOB):
            path.unlink(missing_ok=True)
    except OSError:
        pass


def listing_paths() -> list:
    """The files holding saved profile listings. Empty if there are none."""
    try:
        return list(_jobs_dir().glob(_JOB_GLOB))
    except OSError:
        return []


def saved_jobs() -> list:
    """Every saved listing, newest first. Used to report what clearing costs."""
    _migrate_legacy_job()
    jobs = []
    try:
        paths = sorted(_jobs_dir().glob(_JOB_GLOB),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return jobs
    for path in paths:
        job = _read_job(path)
        if job is not None:
            jobs.append(job)
    return jobs

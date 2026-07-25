"""Self-update via GitHub Releases: version check, installer download, handoff.

Only meaningful for the packaged (PyInstaller) build - a running .exe cannot
overwrite itself on Windows, so the update is applied by downloading the Inno
Setup installer and letting it replace the files and relaunch the app.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import UpdateError

GITHUB_OWNER = "RumpleSteelSkin"
GITHUB_REPO = "blerp-downloader"
API_LATEST_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"

# Only an Inno Setup installer is ever launched. A bare BlerpDownloader.exe asset
# must never match, or it would be executed as if it were an installer.
INSTALLER_RE = re.compile(r"^BlerpDownloader-Setup-.+\.exe$", re.IGNORECASE)

APP_DIR_NAME = "BlerpDownloader"
_API_TIMEOUT = 15.0
_DOWNLOAD_TIMEOUT = 60.0
_CHUNK = 262144  # 256 KiB
_MAX_API_BYTES = 2 * 1024 * 1024
_MAX_TAG_LEN = 64
_STALE_DOWNLOAD_DAYS = 7


class UpdateState(str, Enum):
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    NO_RELEASES = "no_releases"
    AHEAD = "ahead"                # running a dev build newer than the latest release
    RATE_LIMITED = "rate_limited"
    UNCOMPARABLE = "uncomparable"  # tag exists but isn't a parseable version
    ERROR = "error"


@dataclass(frozen=True)
class UpdateInfo:
    version: str          # "1.2.0"
    tag: str              # "v1.2.0"
    asset_name: str
    asset_url: str
    asset_size: int
    html_url: str
    notes: str


@dataclass(frozen=True)
class UpdateStatus:
    state: UpdateState
    current: str
    latest: str | None = None
    info: UpdateInfo | None = None
    message: str = ""
    retry_after: str | None = None   # human-readable clock time for RATE_LIMITED


def is_frozen() -> bool:
    """True when running from a packaged build. `sys.frozen` is the documented
    freezer marker; `sys._MEIPASS` (used by resource_path) is the onefile
    extraction *path*, which answers a different question."""
    return bool(getattr(sys, "frozen", False))


def updates_dir() -> Path:
    """Downloaded installers live under LOCALAPPDATA, not TEMP: 'no executables
    in Temp' policies are common in managed environments and would silently
    break the handoff."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_DIR_NAME / "updates"
    return Path.home() / ".cache" / "blerp-downloader" / "updates"


# --------------------------------------------------------------------------- #
#  Version parsing
# --------------------------------------------------------------------------- #
def parse_version(text: str) -> tuple[int, ...] | None:
    """
    'v1.2.0' / '1.2' / '1.2.0.3' -> a comparable tuple; None if unparseable.

    Numeric parts are padded to 4 so '1.2' == '1.2.0.0'. The trailing element is
    a release flag (0 = pre-release, 1 = final) so '1.2.0-rc.1' < '1.2.0'.

    Parsed with plain string ops rather than a regex: the numeric-and-suffix
    grammar needs nested quantifiers to express, which backtrack badly.
    """
    if not text or len(text) > _MAX_TAG_LEN:
        return None
    s = text.strip()
    if s[:1].lower() == "v":
        s = s[1:]

    # A pre-release/build suffix starts at the first '-' or '+'.
    cut = min((i for i in (s.find("-"), s.find("+")) if i != -1), default=-1)
    prerelease = cut != -1
    if cut != -1:
        s = s[:cut]

    nums: list[int] = []
    for part in s.split("."):
        if part.isdecimal():        # not isdigit(): '²'.isdigit() is True but int() rejects it
            nums.append(int(part))
        else:
            prerelease = True       # trailing non-numeric component, e.g. '1.2.0.rc1'
            break
    if not nums:
        return None

    nums = nums[:4]
    nums += [0] * (4 - len(nums))
    return tuple(nums) + (0 if prerelease else 1,)


def is_newer(candidate: str, current: str) -> bool:
    c, n = parse_version(candidate), parse_version(current)
    return bool(c and n and c > n)


def _version_from_tag(tag: str) -> str:
    return tag[1:] if tag[:1].lower() == "v" else tag


# --------------------------------------------------------------------------- #
#  GitHub API
# --------------------------------------------------------------------------- #
def _user_agent(current_version: str) -> str:
    # GitHub rejects requests without a User-Agent and asks callers to identify
    # themselves; network.py's browser UA exists for Blerp's CDN, not for this.
    return f"BlerpDownloader/{current_version} (+{RELEASES_PAGE_URL})"


def _api_get_json(url: str, current_version: str, timeout: float) -> dict:
    req = Request(url, headers={
        "User-Agent": _user_agent(current_version),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_API_BYTES)
    return json.loads(raw)


def _rate_limit_reset(headers) -> str | None:
    try:
        return datetime.fromtimestamp(int(headers["X-RateLimit-Reset"])).strftime("%H:%M")
    except (KeyError, TypeError, ValueError, OSError):
        return None


def pick_installer_asset(assets: list[dict], version: str) -> dict | None:
    """Exact filename first, then the installer pattern, then give up.

    Only fully-uploaded assets are eligible: GitHub creates the asset record
    before the upload finishes, and downloading one of those yields a truncated
    file or a 404.
    """
    usable = [a for a in assets if a.get("state") == "uploaded" and a.get("name")]
    exact = f"BlerpDownloader-Setup-{version}.exe".lower()
    for a in usable:
        if a["name"].lower() == exact:
            return a
    for a in usable:
        if INSTALLER_RE.match(a["name"]):
            return a
    return None


def _classify_http_error(e: HTTPError, current_version: str) -> UpdateStatus:
    if e.code == 404:
        return UpdateStatus(
            UpdateState.NO_RELEASES, current_version,
            message="No release has been published yet. You already have the newest build.")
    if e.code in (403, 429) and str(e.headers.get("X-RateLimit-Remaining", "")) == "0":
        when = _rate_limit_reset(e.headers)
        return UpdateStatus(
            UpdateState.RATE_LIMITED, current_version, retry_after=when,
            message=("GitHub's rate limit was reached (60 checks per hour per network). "
                     + (f"Try again after {when}." if when else "Try again later.")))
    return UpdateStatus(UpdateState.ERROR, current_version,
                        message=f"GitHub returned HTTP {e.code}.")


def _fetch_latest(url: str, current_version: str,
                  timeout: float) -> tuple[dict | None, UpdateStatus | None]:
    """Returns (data, None) on success or (None, failure_status)."""
    try:
        return _api_get_json(url, current_version, timeout), None
    except HTTPError as e:
        return None, _classify_http_error(e, current_version)
    except URLError as e:
        return None, UpdateStatus(
            UpdateState.ERROR, current_version,
            message=f"Could not reach GitHub: {e.reason}. Check your internet connection.")
    except ValueError as e:  # includes json.JSONDecodeError
        return None, UpdateStatus(UpdateState.ERROR, current_version,
                                  message=f"Could not read GitHub's response: {e}")
    except OSError as e:  # socket timeouts and friends
        return None, UpdateStatus(UpdateState.ERROR, current_version,
                                  message=f"Could not reach GitHub: {e}")


def check_for_update(current_version: str, *, timeout: float = _API_TIMEOUT,
                     api_url: str | None = None) -> UpdateStatus:
    """Queries GitHub for the latest release and classifies the result.

    api_url (or the BLERP_UPDATE_API_URL env var) overrides the endpoint for
    testing against a local fixture server.
    """
    url = api_url or os.environ.get("BLERP_UPDATE_API_URL") or API_LATEST_URL

    data, failure = _fetch_latest(url, current_version, timeout)
    if failure is not None:
        return failure

    if not isinstance(data, dict):
        return UpdateStatus(UpdateState.ERROR, current_version,
                            message="Unexpected response from GitHub.")

    tag = (data.get("tag_name") or "").strip()
    latest = _version_from_tag(tag)
    if parse_version(tag) is None:
        return UpdateStatus(
            UpdateState.UNCOMPARABLE, current_version, latest=latest or None,
            message=(f"Could not read the latest version number ({tag or 'missing tag'}). "
                     "Open the Releases page to check manually."))

    if not is_newer(tag, current_version):
        if is_newer(current_version, tag):
            return UpdateStatus(
                UpdateState.AHEAD, current_version, latest=latest,
                message=(f"You are running {current_version}, which is newer than the latest "
                         f"published release ({latest}). Nothing to update."))
        return UpdateStatus(UpdateState.UP_TO_DATE, current_version, latest=latest,
                            message=f"You are running the latest version ({current_version}).")

    asset = pick_installer_asset(data.get("assets") or [], latest)
    if not asset:
        return UpdateStatus(
            UpdateState.ERROR, current_version, latest=latest,
            message=(f"Release {latest} has no Windows installer attached yet. "
                     "It may still be uploading - try again in a minute."))

    info = UpdateInfo(
        version=latest,
        tag=tag,
        asset_name=asset.get("name") or f"BlerpDownloader-Setup-{latest}.exe",
        asset_url=asset.get("browser_download_url") or "",
        asset_size=int(asset.get("size") or 0),
        html_url=data.get("html_url") or RELEASES_PAGE_URL,
        notes=(data.get("body") or "").strip()[:600],
    )
    if not info.asset_url:
        return UpdateStatus(UpdateState.ERROR, current_version, latest=latest,
                            message="The release asset has no download URL.")
    return UpdateStatus(UpdateState.AVAILABLE, current_version, latest=latest, info=info,
                        message=f"Version {latest} is available (you have {current_version}).")


# --------------------------------------------------------------------------- #
#  Download + handoff
# --------------------------------------------------------------------------- #
def _stream_to(req: Request, part: Path, expected_size: int,
               on_progress, cancel: threading.Event | None) -> int:
    """Streams the response body into `part` and returns the byte count."""
    try:
        with urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length") or expected_size or 0)
            got = 0
            with part.open("wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        # The caller deletes `part` once `with` has closed it:
                        # Windows refuses to unlink a file that is still open.
                        raise UpdateError("Update download cancelled.")
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        return got
                    f.write(chunk)
                    got += len(chunk)
                    if on_progress:
                        on_progress(got, total)
    except HTTPError as e:
        raise UpdateError(f"Download failed: HTTP {e.code}")
    except URLError as e:
        raise UpdateError(f"Download failed: {e.reason}")
    except OSError as e:
        raise UpdateError(f"Download failed: {e}")


def download_installer(info: UpdateInfo, *, current_version: str = "",
                       on_progress=None, cancel: threading.Event | None = None) -> Path:
    """Streams the installer to disk and returns its path.

    Written to a .part file and renamed only after the size is verified, so a
    truncated or cancelled download can never be launched.
    """
    dest_dir = updates_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / info.asset_name
    part = final.with_suffix(final.suffix + ".part")

    req = Request(info.asset_url, headers={
        "User-Agent": _user_agent(current_version or info.version),
        "Accept": "application/octet-stream",
    })
    try:
        got = _stream_to(req, part, info.asset_size, on_progress, cancel)
        if info.asset_size and got != info.asset_size:
            raise UpdateError("The downloaded installer is incomplete. Please try again.")
    except Exception:
        part.unlink(missing_ok=True)
        raise

    part.replace(final)
    return final


def launch_installer(path: Path) -> None:
    """Starts the installer detached and returns immediately; the caller then exits.

    /CLOSEAPPLICATIONS lets Restart Manager close this app cleanly if it is still
    holding the exe, and /NORESTARTAPPLICATIONS stops it from also relaunching
    (installer.iss has a /UPDATED=1 [Run] entry that does that deterministically).
    """
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    subprocess.Popen(
        [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NOCANCEL", "/NORESTART",
         "/CLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", "/UPDATED=1", "/LOG"],
        creationflags=flags, close_fds=True, cwd=str(path.parent),
    )


def cleanup_old_downloads(max_age_days: int = _STALE_DOWNLOAD_DAYS) -> None:
    """Best-effort removal of stale installers; never raises."""
    try:
        cutoff = datetime.now().timestamp() - max_age_days * 86400
        for p in updates_dir().glob("*"):
            if p.suffix.lower() in (".exe", ".part") and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        pass

"""Blerp Downloader - downloads a Blerp soundbite's animated image (WebP) and its
audio (MP3), then combines them with FFmpeg into an MP4."""

from __future__ import annotations

__author__ = "RumpleSteelSkin"
__version__ = "1.0.4"
APP_NAME = "Blerp -> MP4 Downloader"
SIGNATURE = f"By {__author__}"

from .errors import BlerpError, UpdateError
from .ffmpeg_utils import (FFMPEG_DOWNLOAD_URL, FFMPEG_HELP, has_ffmpeg,
                           hidden_process_kwargs)
from .listing import list_user_bites, parse_username
from .pipeline import process_bite, sanitize
from .scraping import OBJECTID_RE, fetch_bite_media, is_blerp_url
from .settings import Settings, load_settings, save_settings
from .updater import (RELEASES_PAGE_URL, UpdateInfo, UpdateState, UpdateStatus,
                      check_for_update, cleanup_old_downloads, download_installer,
                      is_frozen, launch_installer)

__all__ = [
    "__author__", "__version__", "APP_NAME", "SIGNATURE",
    "BlerpError", "UpdateError", "has_ffmpeg", "hidden_process_kwargs",
    "FFMPEG_HELP", "FFMPEG_DOWNLOAD_URL",
    "parse_username", "OBJECTID_RE", "fetch_bite_media", "is_blerp_url",
    "list_user_bites", "process_bite", "sanitize",
    "Settings", "load_settings", "save_settings",
    "RELEASES_PAGE_URL", "UpdateInfo", "UpdateState", "UpdateStatus",
    "check_for_update", "cleanup_old_downloads", "download_installer",
    "is_frozen", "launch_installer",
]

"""Blerp Downloader - downloads a Blerp soundbite's animated image (WebP) and its
audio (MP3), then combines them with FFmpeg into an MP4."""

from __future__ import annotations

__author__ = "RumpleSteelSkin"
__version__ = "1.0.0"
APP_NAME = "Blerp -> MP4 Downloader"
SIGNATURE = f"By {__author__}"

from .errors import BlerpError
from .ffmpeg_utils import FFMPEG_DOWNLOAD_URL, FFMPEG_HELP, has_ffmpeg
from .listing import list_user_bites, parse_username
from .pipeline import process_bite, sanitize
from .scraping import OBJECTID_RE, fetch_bite_media
from .settings import Settings, load_settings, save_settings

__all__ = [
    "__author__", "__version__", "APP_NAME", "SIGNATURE",
    "BlerpError", "has_ffmpeg", "FFMPEG_HELP", "FFMPEG_DOWNLOAD_URL",
    "parse_username", "OBJECTID_RE", "fetch_bite_media", "list_user_bites",
    "process_bite", "sanitize",
    "Settings", "load_settings", "save_settings",
]

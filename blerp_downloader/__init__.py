"""Blerp Downloader - downloads a Blerp soundbite's animated image (WebP) and its
audio (MP3), then combines them with FFmpeg into an MP4."""

from __future__ import annotations

__author__ = "RumpleSteelSkin"
__version__ = "1.1.0"
APP_NAME = "Blerp -> MP4 Downloader"
SIGNATURE = f"By {__author__}"

from .errors import BlerpError, UpdateError
from .ffmpeg_utils import (FFMPEG_DOWNLOAD_URL, FFMPEG_HELP, has_ffmpeg,
                           hidden_process_kwargs)
from .jobs import Job, clear_job, load_job, save_job
from .listing import list_user_bites, parse_username
from .jobs import saved_jobs
from .maintenance import cache_usage, clear_cache, human_size, output_dirs
from .pipeline import (FAILURE_STREAK_LIMIT, STEP_LABELS, bulk_out_path,
                       process_bite, sanitize)
from .queue_store import (QueueItem, clear_queue, load_queue, prune,
                          save_queue)
from .scraping import BiteMedia, OBJECTID_RE, fetch_bite_media, is_blerp_url
from .thumbs import cached_ico, cached_png
from .settings import Settings, load_settings, reset_settings, save_settings
from .updater import (RELEASES_PAGE_URL, UpdateInfo, UpdateState, UpdateStatus,
                      check_for_update, cleanup_old_downloads, download_installer,
                      is_frozen, launch_installer)

__all__ = [
    "__author__", "__version__", "APP_NAME", "SIGNATURE",
    "BlerpError", "UpdateError", "has_ffmpeg", "hidden_process_kwargs",
    "FFMPEG_HELP", "FFMPEG_DOWNLOAD_URL",
    "parse_username", "OBJECTID_RE", "fetch_bite_media", "is_blerp_url",
    "BiteMedia",
    "list_user_bites", "process_bite", "sanitize", "bulk_out_path",
    "FAILURE_STREAK_LIMIT", "STEP_LABELS",
    "Job", "load_job", "save_job", "clear_job",
    "QueueItem", "load_queue", "save_queue", "clear_queue", "prune",
    "clear_cache", "output_dirs", "saved_jobs", "cache_usage", "human_size",
    "cached_ico", "cached_png",
    "Settings", "load_settings", "save_settings", "reset_settings",
    "RELEASES_PAGE_URL", "UpdateInfo", "UpdateState", "UpdateStatus",
    "check_for_update", "cleanup_old_downloads", "download_installer",
    "is_frozen", "launch_installer",
]

"""Persistent app settings (INI-backed): a flat key-value bag shared by the
CLI and GUI, small enough that a human-editable text file beats a database.
"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "BlerpDownloader"


@dataclass
class Settings:
    output_dir: str = ""
    overwrite: bool = False
    bulk_limit: int | None = None
    bulk_delay: float = 0.3
    ffmpeg_dir: str = ""              # empty = use PATH (today's behavior)
    window_width: int = 580
    window_height: int = 460
    clipboard_watch: bool = False     # off by default: opt-in, not silently enabled
    clipboard_mode: str = "ask"       # "ask" or "auto"; only relevant if clipboard_watch


def _settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_DIR_NAME / "settings.ini"
    return Path.home() / ".config" / "blerp-downloader" / "settings.ini"


def _get_bool(section: configparser.SectionProxy, key: str, default: bool) -> bool:
    try:
        return section.getboolean(key, fallback=default)
    except ValueError:
        return default


def _get_float(section: configparser.SectionProxy, key: str, default: float) -> float:
    try:
        return section.getfloat(key, fallback=default)
    except ValueError:
        return default


def _get_int(section: configparser.SectionProxy, key: str, default: int) -> int:
    try:
        return section.getint(key, fallback=default)
    except ValueError:
        return default


def _get_optional_int(section: configparser.SectionProxy, key: str) -> int | None:
    text = section.get(key, "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def load_settings() -> Settings:
    """Loads settings from disk. A missing file, missing section/keys, or
    values that fail to parse all fall back to defaults per-field - a bad
    settings file must never crash the app."""
    settings = Settings()
    path = _settings_path()
    if not path.exists():
        return settings

    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return settings
    if not parser.has_section("general"):
        return settings

    g = parser["general"]
    settings.output_dir = g.get("output_dir", settings.output_dir)
    settings.ffmpeg_dir = g.get("ffmpeg_dir", settings.ffmpeg_dir)
    settings.clipboard_mode = g.get("clipboard_mode", settings.clipboard_mode)
    settings.overwrite = _get_bool(g, "overwrite", settings.overwrite)
    settings.clipboard_watch = _get_bool(g, "clipboard_watch", settings.clipboard_watch)
    settings.bulk_delay = _get_float(g, "bulk_delay", settings.bulk_delay)
    settings.bulk_limit = _get_optional_int(g, "bulk_limit")
    settings.window_width = _get_int(g, "window_width", settings.window_width)
    settings.window_height = _get_int(g, "window_height", settings.window_height)
    return settings


def save_settings(settings: Settings) -> None:
    """Overwrites the settings file with the given values."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    parser["general"] = {
        "output_dir": settings.output_dir,
        "overwrite": str(settings.overwrite),
        "bulk_limit": "" if settings.bulk_limit is None else str(settings.bulk_limit),
        "bulk_delay": str(settings.bulk_delay),
        "ffmpeg_dir": settings.ffmpeg_dir,
        "window_width": str(settings.window_width),
        "window_height": str(settings.window_height),
        "clipboard_watch": str(settings.clipboard_watch),
        "clipboard_mode": settings.clipboard_mode,
    }
    with path.open("w", encoding="utf-8") as f:
        parser.write(f)

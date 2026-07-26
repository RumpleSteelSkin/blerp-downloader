"""Persistent app settings (INI-backed): a flat key-value bag shared by the
CLI and GUI, small enough that a human-editable text file beats a database.
"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path

APP_DIR_NAME = "BlerpDownloader"

# Smallest window the layout fits in.
MIN_WINDOW_WIDTH = 620
MIN_WINDOW_HEIGHT = 640

# The starting size. Taller than the minimum so the download list has room to
# show several rows without the user having to resize on first run.
DEFAULT_WINDOW_WIDTH = MIN_WINDOW_WIDTH
DEFAULT_WINDOW_HEIGHT = 720

THEMES = ("auto", "dark", "light")
CLIPBOARD_MODES = ("ask", "auto")


@dataclass
class Settings:
    output_dir: str = ""
    overwrite: bool = False
    bulk_limit: int | None = None
    bulk_delay: float = 0.3
    ffmpeg_dir: str = ""              # empty = use PATH (today's behavior)
    window_width: int = DEFAULT_WINDOW_WIDTH
    window_height: int = DEFAULT_WINDOW_HEIGHT
    clipboard_watch: bool = False     # off by default: opt-in, not silently enabled
    clipboard_mode: str = "ask"       # "ask" or "auto"; only relevant if clipboard_watch
    theme: str = "auto"               # "auto" follows the Windows light/dark setting
    log_expanded: bool = False        # the collapsible details pane under the list
    close_to_tray: bool = True        # X hides to the notification area instead of quitting
    tray_enabled: bool = True         # escape hatch if the notification icon misbehaves
    notify_balloons: bool = True      # Windows notifications from the tray icon
    notify_card: bool = True          # the in-app card shown on a clipboard catch
    thumbnails: bool = True           # fetch and cache blerp images for the list
    pick_blerps: bool = True          # ask which of a profile to take, after scanning


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


def _get_choice(section: configparser.SectionProxy, key: str,
                allowed: tuple, default: str) -> str:
    """One of `allowed`, case-insensitively, or the default.

    Anything unrecognised falls back rather than being carried through: these
    values steer behaviour, and a typo in a hand-edited file should not put the
    app into a state no code branch handles.
    """
    value = section.get(key, default).strip().lower()
    return value if value in allowed else default


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
        # utf-8-sig, not utf-8: the file is meant to be hand-editable, and many
        # Windows editors write a BOM. Under plain utf-8 that BOM lands in the
        # section header and the whole file is silently discarded.
        parser.read(path, encoding="utf-8-sig")
    except (configparser.Error, UnicodeDecodeError):
        return settings
    if not parser.has_section("general"):
        return settings

    g = parser["general"]
    settings.output_dir = g.get("output_dir", settings.output_dir)
    settings.ffmpeg_dir = g.get("ffmpeg_dir", settings.ffmpeg_dir)
    settings.overwrite = _get_bool(g, "overwrite", settings.overwrite)
    settings.clipboard_watch = _get_bool(g, "clipboard_watch", settings.clipboard_watch)
    settings.bulk_delay = _get_float(g, "bulk_delay", settings.bulk_delay)
    settings.bulk_limit = _get_optional_int(g, "bulk_limit")
    settings.window_width = _get_int(g, "window_width", settings.window_width)
    settings.window_height = _get_int(g, "window_height", settings.window_height)
    settings.log_expanded = _get_bool(g, "log_expanded", settings.log_expanded)
    settings.close_to_tray = _get_bool(g, "close_to_tray", settings.close_to_tray)
    settings.tray_enabled = _get_bool(g, "tray_enabled", settings.tray_enabled)
    settings.notify_balloons = _get_bool(g, "notify_balloons", settings.notify_balloons)
    settings.notify_card = _get_bool(g, "notify_card", settings.notify_card)
    settings.thumbnails = _get_bool(g, "thumbnails", settings.thumbnails)
    settings.pick_blerps = _get_bool(g, "pick_blerps", settings.pick_blerps)
    settings.theme = _get_choice(g, "theme", THEMES, settings.theme)
    settings.clipboard_mode = _get_choice(
        g, "clipboard_mode", CLIPBOARD_MODES, settings.clipboard_mode)
    return settings


def reset_settings() -> Settings:
    """Restores every setting to its default and returns them.

    Callers with a UI must also push these back into their widgets: anything
    that rebuilds Settings from widget state on exit would otherwise write the
    old values straight back over this.
    """
    defaults = Settings()
    save_settings(defaults)
    return defaults


def save_settings(settings: Settings) -> None:
    """Overwrites the settings file with the given values.

    Written to a temporary file and renamed over the original, so an interrupted
    write can't leave a truncated file - which loads as "no [general] section"
    and silently resets every setting.
    """
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    # Generated from the dataclass rather than hand-listed: a field left out of a
    # hand-written dict still loads (as its default), so forgetting one silently
    # discards that setting on every save and no test would fail.
    parser["general"] = {
        f.name: "" if (v := getattr(settings, f.name)) is None else str(v)
        for f in fields(settings)
    }
    tmp = path.with_suffix(path.suffix + ".part")   # same directory: same volume
    try:
        with tmp.open("w", encoding="utf-8") as f:
            parser.write(f)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise

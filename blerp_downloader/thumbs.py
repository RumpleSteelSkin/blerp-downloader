"""Cached blerp images for the download list.

A blerp has no small preview: its only image is the full animated WebP the
pipeline turns into video, several megabytes of it. So a thumbnail is expensive
to obtain once and free forever after, which is what this module is for.

Two ways in. The cheap one is store_from_webp, called by the pipeline with the
file it has already downloaded - every blerp the user downloads gets an image at
no extra cost. The other is fetch, which does go to the network, and the GUI
runs exactly one of those at a time and never while a download is in progress.

PNG rather than anything Pillow-specific: Tk 8.6 decodes PNG natively, so the
list needs no ImageTk and the packaged build needs no extra DLL.
"""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

from .errors import BlerpError
from .jobs import state_dir
from .network import http_get
from .scraping import OBJECTID_RE

# Fits inside theme.LIST_ROW_HEIGHT with room for padding. tests/test_thumbs.py
# pins that relationship.
THUMB_PX = 40

# Two sizes because a Windows notification asks for the 32px icon at 100% DPI
# and the 64px one at 200%.
ICO_SIZES = ((32, 32), (64, 64))

# The image URL is already a 320x320 derivative, but an animated blerp carries
# every frame: measured against a real profile, static ones run 11-19 KB and
# animated ones 1-3.2 MB. 8 MB leaves generous headroom over that and still
# stops anything pathological. http_get raises rather than truncating past its
# limit, which is the behaviour wanted here.
#
# There is no cheaper route: the CDN ignores every resize parameter tried
# (?w=, ?width=, ?size=, ?tr=w-, ?format=), and while it does honour Range,
# libwebp refuses to decode a truncated animation - so the first frame cannot
# be had without the whole file.
THUMB_MAX_BYTES = 8 * 1024 * 1024

MAX_CACHED = 3000


def thumbs_dir() -> Path:
    return state_dir() / "thumbs"


def _safe_name(bite_id: str) -> str | None:
    """The id, only if it really is one.

    It becomes a path, and the download list it comes from is a file anything
    running as the user can write - "../.." is the case this exists to stop.
    """
    bite_id = (bite_id or "").strip()
    return bite_id if OBJECTID_RE.fullmatch(bite_id) else None


def cached_png(bite_id: str) -> Path | None:
    name = _safe_name(bite_id)
    if not name:
        return None
    path = thumbs_dir() / f"{name}.png"
    return path if path.exists() else None


def cached_ico(bite_id: str) -> Path | None:
    name = _safe_name(bite_id)
    if not name:
        return None
    path = thumbs_dir() / f"{name}.ico"
    return path if path.exists() else None


def _write_atomic(path: Path, write) -> None:
    """Writes via a .part in the same directory, so the rename is same-volume."""
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write(tmp)
        tmp.replace(path)
    except (OSError, ValueError):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _shrink(image):
    """Frame 0 of an animation, squared off at THUMB_PX."""
    image.seek(0)
    frame = image.convert("RGBA")
    frame.thumbnail((THUMB_PX * 2, THUMB_PX * 2))
    return frame


def store(bite_id: str, data: bytes) -> Path | None:
    """Caches a thumbnail from raw image bytes. Returns the PNG, or None.

    Never raises: a missing thumbnail is a cosmetic problem, and this is called
    from the middle of a download that must not fail because of one.
    """
    name = _safe_name(bite_id)
    if not name:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None

    png = thumbs_dir() / f"{name}.png"
    try:
        with Image.open(io.BytesIO(data)) as image:
            frame = _shrink(image)
            small = frame.copy()
            small.thumbnail((THUMB_PX, THUMB_PX))
            _write_atomic(png, lambda p: small.save(p, format="PNG"))
            # The .ico is what a Windows notification can show; Pillow's writer
            # is far less fiddly than building an HICON from raw pixels.
            _write_atomic(thumbs_dir() / f"{name}.ico",
                          lambda p: frame.save(p, format="ICO", sizes=ICO_SIZES))
    except Exception:
        return None
    return png if png.exists() else None


def store_from_webp(bite_id: str, webp_path: Path) -> Path | None:
    """The free path: the pipeline already has this file on disk."""
    if cached_png(bite_id):
        return cached_png(bite_id)
    try:
        return store(bite_id, Path(webp_path).read_bytes())
    except OSError:
        return None


def fetch(bite_id: str, image_url: str) -> Path | None:
    """The paid path: downloads the full animation to get one small frame.

    Only worth calling for a row the user is looking at, one at a time, and not
    while a download is competing for the connection.
    """
    existing = cached_png(bite_id)
    if existing:
        return existing
    if not _safe_name(bite_id):
        return None
    try:
        data = http_get(image_url, limit=THUMB_MAX_BYTES)
    except (BlerpError, OSError):
        return None
    return store(bite_id, data)


def sweep(max_files: int = MAX_CACHED) -> int:
    """Drops the least recently used thumbnails. Returns how many went."""
    try:
        files = sorted(thumbs_dir().glob("*.*"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return 0
    excess = len(files) - max_files
    removed = 0
    for path in files[:max(0, excess)]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def touch(bite_id: str) -> None:
    """Marks a thumbnail as recently used, so sweep keeps it."""
    path = cached_png(bite_id)
    if path is None:
        return
    try:
        now = time.time()
        os.utime(path, (now, now))
    except OSError:
        pass

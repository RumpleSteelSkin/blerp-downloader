"""The saved download list.

What the user has lined up, kept across restarts so closing the window - or
losing power - doesn't lose the links they collected. This is *user data*, not
cache: clearing the cache leaves it alone, and only the Clear list button
removes rows.

Deliberately separate from jobs.py. That file holds the expensive artifact (a
scanned profile listing) and is written once per scan by the worker thread; this
one is small, changes constantly, and is written only by the main thread. Mixing
them would put a per-keystroke write on the same file as a multi-megabyte one.

Threading contract: the list belongs to the main (GUI) thread. Workers get a
snapshot of what they need and report back through the GUI's queue. Nothing here
takes a lock because nothing here is ever called from two threads.
"""

from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass
from pathlib import Path

from .jobs import state_dir
from .network import require_web_url
from .scraping import OBJECTID_RE, is_blerp_url

# Bumped whenever the record shape changes. A file from another version is
# discarded whole rather than half-read: a partly-understood download list is
# worse than an empty one, because the user cannot see what was dropped.
SCHEMA_VERSION = 1

# A clipboard watcher left running can add rows unattended, so the list needs a
# ceiling even though nothing in the UI can reach it by hand.
MAX_QUEUE_ITEMS = 500

# Finished rows are kept so the user can see what happened, but not forever.
MAX_FINISHED_ITEMS = 200
MAX_FINISHED_AGE_DAYS = 14

_MAX_QUEUE_BYTES = 4 * 1024 * 1024
_MAX_TEXT = 300
_MAX_USERNAME = 64

KINDS = ("single", "profile")

QUEUED = "queued"
RESOLVING = "resolving"
DOWNLOADING = "downloading"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"
STOPPED = "stopped"

STATUSES = (QUEUED, RESOLVING, DOWNLOADING, DONE, FAILED, SKIPPED, STOPPED)

# Reached the end one way or another; these are the rows "Clear finished" takes.
FINISHED = (DONE, FAILED, SKIPPED)

# Rows Start should pick up. STOPPED belongs here: it means the user pressed
# Stop, which is an interruption rather than an outcome - leaving it out is what
# made a stopped profile impossible to restart.
PENDING = (QUEUED, STOPPED)

# A row in one of these was mid-flight when the process ended. It cannot be
# resumed from where it was - process_bite writes to a .part and only renames on
# success - so it goes back in the line to be retried. This is also what makes
# an unfinished link survive a restart instead of being shown as forever busy.
_INTERRUPTED = (RESOLVING, DOWNLOADING)


@dataclass
class QueueItem:
    """One row of the download list."""

    item_id: str = ""             # uuid4 hex; also the row's id in the tree
    kind: str = "single"
    url: str = ""                 # what the user pasted
    username: str = ""            # profile rows only
    bite_id: str = ""             # single rows, once resolved
    title: str = ""               # display name; blank until resolved
    status: str = QUEUED
    error: str = ""
    out_path: str = ""
    added_at: float = 0.0
    done_count: int = 0           # profile rows: blerps finished
    total_count: int = 0          # profile rows: blerps in the listing
    limit: int | None = None
    overwrite: bool = False

    @property
    def label(self) -> str:
        """What to show in the list before anything better is known."""
        if self.kind == "profile":
            return self.username or self.url or "profile"
        return self.title or self.url or self.bite_id or "blerp"

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED

    @property
    def is_pending(self) -> bool:
        return self.status in PENDING

    @property
    def age_days(self) -> float:
        return (time.time() - self.added_at) / 86400 if self.added_at else 0.0


def queue_path() -> Path:
    return _queue_path()


def _queue_path() -> Path:
    return state_dir() / "queue.json"


def _clean_text(raw: object, limit: int = _MAX_TEXT) -> str:
    """A display string with control characters and runaway length removed."""
    text = str(raw or "")
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    return text.strip()[:limit]


def _clean_username(raw: object) -> str:
    """A profile name safe to put in a request and in a folder name."""
    name = _clean_text(raw, _MAX_USERNAME)
    # Separators would escape the output folder once sanitize() builds a path
    # from this, and a name is never legitimately shaped like a path.
    if any(sep in name for sep in ("/", "\\", "..", ":")):
        return ""
    return name


def _source_is_usable(kind: str, url: str, username: str) -> bool:
    """Whether a record names something we are willing to go and fetch.

    Stricter than the checks jobs.py applies to a scanned listing, because a row
    here is processed unattended once the user presses Start - nothing asks them
    to confirm it a second time - and the file lives in a directory anything
    running as the user can write.
    """
    if kind == "profile":
        return bool(username)   # the url is only ever displayed for these
    try:
        require_web_url(url)
    except Exception:
        return False
    return is_blerp_url(url)


def _clean_status(raw: object) -> str:
    status = str(raw or QUEUED)
    if status not in STATUSES or status in _INTERRUPTED:
        # Unknown, or mid-flight when the process ended. Either way the row goes
        # back in the line rather than being shown as permanently busy.
        return QUEUED
    return status


def _clean_item(raw: object) -> QueueItem | None:
    """One record from the file, or None if it isn't usable.

    Read key by key rather than splatted: an extra or missing key would raise,
    and one bad record should cost one row rather than the whole list.
    """
    if not isinstance(raw, dict):
        return None

    kind = str(raw.get("kind") or "single")
    if kind not in KINDS:
        return None

    url = _clean_text(raw.get("url"))
    username = _clean_username(raw.get("username"))
    if not _source_is_usable(kind, url, username):
        return None

    bite_id = _clean_text(raw.get("bite_id"), 64)
    # It ends up in a filename and in a cache path, so it has to be exactly the
    # 24-hex id it claims to be - "../.." is the thing this stops.
    if bite_id and not OBJECTID_RE.fullmatch(bite_id):
        return None

    status = _clean_status(raw.get("status"))
    try:
        limit = int(raw["limit"]) if raw.get("limit") is not None else None
        item = QueueItem(
            item_id=_clean_text(raw.get("item_id"), 64),
            kind=kind,
            url=url,
            username=username,
            bite_id=bite_id,
            title=_clean_text(raw.get("title")),
            status=status,
            error=_clean_text(raw.get("error")),
            out_path=_clean_text(raw.get("out_path"), 1024),
            added_at=float(raw.get("added_at") or 0.0),
            done_count=max(0, int(raw.get("done_count") or 0)),
            total_count=max(0, int(raw.get("total_count") or 0)),
            limit=limit,
            overwrite=bool(raw.get("overwrite")),
        )
    except (TypeError, ValueError):
        return None
    return item if item.item_id else None


def prune(items: list) -> list:
    """Drops finished rows that have outstayed their welcome.

    Without this the list only ever grows - a clipboard watcher plus rows that
    stay until removed by hand is a combination that ends in thousands of Done
    entries nobody can search through. Unfinished rows are never dropped.
    """
    kept, finished = [], []
    for item in items:
        if item.is_finished:
            finished.append(item)
        else:
            kept.append(item)

    fresh = [f for f in finished
             if not f.added_at or f.age_days <= MAX_FINISHED_AGE_DAYS]
    if len(fresh) > MAX_FINISHED_ITEMS:
        fresh.sort(key=lambda f: f.added_at)
        fresh = fresh[-MAX_FINISHED_ITEMS:]

    keep_ids = {id(f) for f in fresh}
    # Rebuilt in the original order: the list is something the user reads, and
    # reordering it on load would be its own kind of data loss.
    return [i for i in items if not i.is_finished or id(i) in keep_ids][:MAX_QUEUE_ITEMS]


def load_queue() -> list:
    """The saved download list, or an empty one.

    Never raises: a corrupt or unreadable file must not stop the app opening,
    and there is nowhere to report it at that point in startup.
    """
    try:
        raw = _queue_path().read_bytes()[:_MAX_QUEUE_BYTES]
        data = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        return []

    records = data.get("items")
    if not isinstance(records, list):
        return []
    items = [item for item in (_clean_item(r) for r in records[:MAX_QUEUE_ITEMS])
             if item]
    return prune(items)


def save_queue(items) -> None:
    """Writes the list atomically. Best-effort: a failure must not break the UI.

    Main thread only - see the module docstring.
    """
    path = _queue_path()
    tmp = path.with_suffix(path.suffix + ".part")
    payload = {
        "version": SCHEMA_VERSION,
        "saved_at": time.time(),
        "items": [dataclasses.asdict(i) for i in list(items)[:MAX_QUEUE_ITEMS]],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Same directory, so the replace is on one volume and therefore atomic.
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def clear_queue() -> None:
    try:
        _queue_path().unlink(missing_ok=True)
    except OSError:
        pass

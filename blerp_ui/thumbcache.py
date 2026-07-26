"""Blerp images for the list, as Tk images.

Two halves. Turning a cached PNG into a Tk image is cheap and happens on the
main thread; going to the network for one is not, and happens on a small pool
of background threads that stands down to a single fetcher whenever a download
is running - the connection belongs to the download, not to decoration.

The pool exists because these are not small files. Blerp's image URL is already
a 320x320 derivative, but an animated blerp carries every frame, so a screenful
runs to tens of megabytes and one at a time is visibly slow. There is no cheaper
source: the CDN ignores every resize parameter and every Accept header tried,
and while it honours Range, libwebp will not decode a truncated animation.

tk.PhotoImage(data=<base64 PNG>) rather than PIL.ImageTk: Tk 8.6 decodes PNG
itself, so the packaged build needs no _imagingtk DLL.
"""

from __future__ import annotations

import base64
import queue
import threading
import tkinter as tk
from collections import OrderedDict

from blerp_downloader import thumbs

# Each live 40x40 image costs about 9 KB inside Tk, so a big profile expanded in
# full would be tens of megabytes. Rows past this fall back to the placeholder.
MAX_LIVE_IMAGES = 512

# A bound on how many images one expansion may go and fetch. Past this the user
# is scrolling through thousands of rows and would not look at them anyway.
MAX_FETCHES_PER_REQUEST = 60

# How many images to pull at once. Measured over a screenful of a real profile:
# one at a time took 13.2s, four took 6.9s, and six and eight were no better -
# past four it is bandwidth, not latency, and the extra sockets buy nothing.
# While a download is running the pool drops to one, because the connection
# belongs to the thing the user actually asked for.
MAX_FETCHERS_IDLE = 4
MAX_FETCHERS_BUSY = 1


class ThumbCache:
    """Tk images for blerps, with a background fetcher.

    Owned by the main thread. The worker only ever touches the filesystem and
    the network, and reports back through the app's queue.
    """

    def __init__(self, root: tk.Misc, on_ready, *, enabled=None, busy=None) -> None:
        self.root = root
        self._on_ready = on_ready          # called (bite_id) on the worker thread
        self._enabled = enabled or (lambda: True)
        self._busy = busy or (lambda: False)

        self._images: OrderedDict = OrderedDict()   # bite_id -> PhotoImage (LRU)
        self._evicted = set()
        self._failed = set()               # this session only; never written to disk
        self._wanted: queue.LifoQueue = queue.LifoQueue()
        self._queued = set()
        self._stop = threading.Event()
        self._workers: list = []
        # Guards _queued and _workers, both of which several fetchers touch.
        self._lock = threading.Lock()
        self.placeholder = self._make_placeholder()

    # ------------------------------------------------------------------ #
    #  Main thread
    # ------------------------------------------------------------------ #
    def _make_placeholder(self):
        """One transparent image shared by every unresolved row."""
        try:
            return tk.PhotoImage(master=self.root, width=thumbs.THUMB_PX,
                                 height=thumbs.THUMB_PX)
        except tk.TclError:
            return None

    def image_for(self, bite_id: str):
        """The row's image if it is cached, else the placeholder.

        Never goes to the network - call want() for that.
        """
        if not bite_id:
            return self.placeholder
        existing = self._images.get(bite_id)
        if existing is not None:
            self._images.move_to_end(bite_id)
            return existing

        path = thumbs.cached_png(bite_id)
        if path is None:
            return self.placeholder
        try:
            data = base64.b64encode(path.read_bytes())
            image = tk.PhotoImage(master=self.root, data=data)
        except (OSError, tk.TclError):
            return self.placeholder

        self._images[bite_id] = image
        self._evict_if_needed()
        return image

    def _evict_if_needed(self) -> None:
        """Drops the least recently used images, oldest first.

        The caller has to repoint any row still showing one back at the
        placeholder *before* the reference goes: a Treeview row stores only the
        image's name, so dropping it out from under the row blanks the cell.
        """
        while len(self._images) > MAX_LIVE_IMAGES:
            bite_id, _ = self._images.popitem(last=False)
            self._evicted.add(bite_id)

    def take_evicted(self) -> set:
        """The ids whose images have just gone, so their rows can be reset."""
        gone, self._evicted = self._evicted, set()
        return gone

    def want(self, requests) -> None:
        """Asks for images that aren't cached yet. `requests` is (bite_id, url)."""
        if not self._enabled():
            return
        fresh = []
        with self._lock:
            for bite_id, url in requests:
                if len(fresh) >= MAX_FETCHES_PER_REQUEST:
                    break
                if (not bite_id or not url or bite_id in self._queued
                        or bite_id in self._failed or thumbs.cached_png(bite_id)):
                    continue
                self._queued.add(bite_id)
                fresh.append((bite_id, url))
        # Pushed back to front so the LIFO pops them front to back: within one
        # screenful the row at the top should fill in first.
        for pair in reversed(fresh):
            self._wanted.put(pair)
        if fresh:
            self._ensure_workers()

    def _ensure_workers(self) -> None:
        """Tops the pool up to whatever the current cap allows."""
        self._stop.clear()
        cap = MAX_FETCHERS_BUSY if self._busy() else MAX_FETCHERS_IDLE
        with self._lock:
            self._workers = [t for t in self._workers if t.is_alive()]
            missing = cap - len(self._workers)
            for _ in range(max(0, missing)):
                t = threading.Thread(target=self._run, daemon=True)
                self._workers.append(t)
                t.start()

    def close(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ #
    #  Worker thread - filesystem and network only
    # ------------------------------------------------------------------ #
    def _surplus(self) -> bool:
        """Whether this thread is more than a busy connection has room for."""
        with self._lock:
            alive = sum(1 for t in self._workers if t.is_alive())
        return alive > MAX_FETCHERS_BUSY

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                # Last in, first out: the rows the user just scrolled to matter
                # more than the ones they scrolled past.
                bite_id, url = self._wanted.get(timeout=0.5)
            except queue.Empty:
                return
            if self._stop.is_set():
                return
            if self._busy() and self._surplus():
                # A download owns the connection, and this thread is one more
                # than that leaves room for. Hand the work back and stand down.
                self._wanted.put((bite_id, url))
                return
            try:
                got = thumbs.fetch(bite_id, url)
            except Exception:
                got = None
            with self._lock:
                self._queued.discard(bite_id)
            if got is None:
                # In memory only: a marker on disk would make one bad afternoon
                # permanent.
                with self._lock:
                    self._failed.add(bite_id)
                continue
            try:
                self._on_ready(bite_id)
            except Exception:
                pass

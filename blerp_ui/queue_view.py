"""The download list.

A ttk.Treeview rather than a frame full of row widgets. That is not a style
preference: 2,847 rows insert in about 16 ms here against roughly seven seconds
of frozen UI and ~11,000 live widgets the other way, and the tree brings the
hierarchy the expandable profile rows need, plus per-row images, selection and
keyboard navigation for free.

Progress is drawn as text because a tree cell cannot hold a widget. The real
ttk.Progressbar stays on the main window as the total across the queue.
"""

from __future__ import annotations

from tkinter import ttk

from blerp_downloader import queue_store as qs

# U+2588 FULL BLOCK / U+2591 LIGHT SHADE. Both are in Segoe UI, and they read as
# a bar rather than as the ASCII noise "####----" would be.
_FULL, _EMPTY = "█", "░"
# Eight, not ten: a profile row also carries "1203/2847" beside the bar, and
# the column has to hold both without the count being clipped.
_BAR_CELLS = 8

STATUS_TEXT = {
    qs.QUEUED: "Waiting",
    qs.RESOLVING: "Reading page…",
    qs.DOWNLOADING: "Downloading",
    qs.DONE: "✓ Done",
    qs.FAILED: "✗ Failed",
    qs.SKIPPED: "Already saved",
    qs.STOPPED: "⏹ Stopped",
}


def bar(fraction: float) -> str:
    """A text progress bar, e.g. ████░░░░░░  40%."""
    fraction = min(1.0, max(0.0, fraction))
    filled = int(round(fraction * _BAR_CELLS))
    return f"{_FULL * filled}{_EMPTY * (_BAR_CELLS - filled)} {fraction * 100:3.0f}%"


def progress_text(item: qs.QueueItem, step: int = 0, steps: int = 0) -> str:
    """What belongs in a row's progress cell, given where the row has got to."""
    if item.status == qs.DONE:
        return bar(1.0)
    if item.status in (qs.FAILED, qs.SKIPPED):
        return ""
    if item.kind == "profile":
        if not item.total_count:
            return "" if item.status == qs.QUEUED else "scanning…"
        return (f"{bar(item.done_count / item.total_count)}  "
                f"{item.done_count}/{item.total_count}")
    if item.status == qs.QUEUED:
        return ""
    return bar(step / steps) if steps else bar(0.0)


class QueueView(ttk.Frame):
    """The list plus its scrollbar. Owns no state: the app passes rows in.

    Every method here runs on the main thread; the worker reports through the
    app's queue and the app calls in from _handle.
    """

    def __init__(self, parent, *, on_selection_change=None, on_activate=None,
                 rows: int = 4) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            self, columns=("status", "progress"), displaycolumns=("status", "progress"),
            selectmode="extended", height=rows)
        self.tree.heading("#0", text="Blerp", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.heading("progress", text="Progress", anchor="w")
        # Not stretch=False on #0: it is the one column that should absorb the
        # window's width, and the two fixed ones are sized to their content.
        self.tree.column("#0", width=260, minwidth=180, stretch=True, anchor="w")
        self.tree.column("status", width=110, minwidth=90, stretch=False, anchor="w")
        self.tree.column("progress", width=180, minwidth=160, stretch=False, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")

        bar_ = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        bar_.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=bar_.set)

        if on_selection_change:
            self.tree.bind("<<TreeviewSelect>>", lambda _e: on_selection_change())
        if on_activate:
            self.tree.bind("<Double-1>", lambda _e: on_activate())

    # ------------------------------------------------------------------ #
    #  Rows
    # ------------------------------------------------------------------ #
    def add(self, item: qs.QueueItem, image=None) -> None:
        if self.tree.exists(item.item_id):
            self.update_item(item)
            return
        self.tree.insert("", "end", iid=item.item_id, text=f" {item.label}",
                         image=image or "",
                         values=(STATUS_TEXT.get(item.status, item.status),
                                 progress_text(item)))

    def update_item(self, item: qs.QueueItem, *, step: int = 0, steps: int = 0,
                    detail: str = "") -> None:
        if not self.tree.exists(item.item_id):
            return
        status = detail or STATUS_TEXT.get(item.status, item.status)
        if item.status == qs.FAILED and item.error and not detail:
            status = f"✗ {item.error[:60]}"
        self.tree.item(item.item_id, text=f" {item.label}")
        self.tree.set(item.item_id, "status", status)
        self.tree.set(item.item_id, "progress", progress_text(item, step, steps))

    def set_image(self, item_id: str, image) -> None:
        if self.tree.exists(item_id):
            self.tree.item(item_id, image=image or "")

    def remove(self, item_id: str) -> None:
        if self.tree.exists(item_id):
            self.tree.delete(item_id)

    def clear(self) -> None:
        children = self.tree.get_children("")
        if children:
            self.tree.delete(*children)

    def selection(self) -> tuple:
        """Only top-level rows: a profile's children are not separate jobs."""
        return tuple(iid for iid in self.tree.selection()
                     if not self.tree.parent(iid))

    def see(self, item_id: str) -> None:
        if self.tree.exists(item_id):
            self.tree.see(item_id)

    # ------------------------------------------------------------------ #
    #  Profile children
    # ------------------------------------------------------------------ #
    def set_children(self, item_id: str, rows) -> None:
        """Fills a profile row in. `rows` is (child_id, label, status_text)."""
        if not self.tree.exists(item_id):
            return
        self.tree.delete(*self.tree.get_children(item_id))
        for child_id, label, status in rows:
            self.tree.insert(item_id, "end", iid=f"{item_id}:{child_id}",
                             text=f" {label}", values=(status, ""))

    def has_children(self, item_id: str) -> bool:
        return bool(self.tree.exists(item_id) and self.tree.get_children(item_id))

    def placeholder_child(self, item_id: str, text: str) -> None:
        """A single non-selectable row explaining why there is nothing to show."""
        if not self.tree.exists(item_id):
            return
        self.tree.delete(*self.tree.get_children(item_id))
        self.tree.insert(item_id, "end", iid=f"{item_id}:none", text=f" {text}",
                         values=("", ""))

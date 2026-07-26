"""Choosing which blerps of a profile to download.

Shown once a profile has been scanned, because scanning is the slow part and
committing to three thousand downloads sight-unseen is not a decision anyone
would make on purpose.

Rows carry the blerp's picture, fetched only for what is on screen: the
image is the full-size animation, so showing three hundred of them up front
would download the whole profile just to decide against it.

Checkboxes rather than the usual highlight selection: a highlight is lost the
moment you click elsewhere, which over thousands of rows means one slip undoes
several minutes of picking. Ticks survive scrolling, clicking and searching.

The worker thread is parked while this is open - see BlerpGUI._on_choose_bites.
"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk

from .widgets import MUTED

CHECKED, UNCHECKED = "☑", "☐"


class BitePicker(tk.Toplevel):
    """A tick-list of a profile's blerps. Call .result after it closes."""

    def __init__(self, parent, username: str, bites, *, already_have=(),
                 preselected=None, icon_path: str = "", images=None,
                 palette=None, on_copy=None) -> None:
        super().__init__(parent)
        self.bites = list(bites)
        self.already_have = set(already_have)
        self.result = None            # None means cancelled; a list means go
        self._checked: set = set()
        self._anchor: str | None = None
        self._images = images         # a ThumbCache, or None for no pictures
        self._by_id = {m.bite_id: m for m in self.bites}
        self._on_copy = on_copy
        self._pending_scroll = None

        self.title(f"Choose blerps — {username}")
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        try:
            self.iconbitmap(icon_path)
        except tk.TclError:
            pass

        self._build(username)
        self._build_menu(palette)
        self._fill(preselected)
        self._centre(parent)
        # Safe here, unlike on a settings window: this is a decision the run is
        # waiting on, and a grab restricts input without stopping the event loop.
        self.grab_set()
        self.tree.focus_set()
        self._request_visible()

    # ------------------------------------------------------------------ #
    #  Layout
    # ------------------------------------------------------------------ #
    def _build(self, username: str) -> None:
        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(2, weight=1)

        ttk.Label(frm, text=f"{len(self.bites)} blerps found for {username}",
                  style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frm, text="Tick the ones you want. Click a row to toggle it, "
                            "shift-click to reach across a range.",
                  style=MUTED).grid(row=1, column=0, sticky="w", pady=(2, 10))

        holder = ttk.Frame(frm)
        holder.grid(row=2, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(holder, columns=("state",), show="tree headings",
                                 displaycolumns=("state",), selectmode="none",
                                 style="Picker.Treeview", height=13)
        self.tree.heading("#0", text="Blerp", anchor="w")
        self.tree.heading("state", text="", anchor="w")
        self.tree.column("#0", width=420, minwidth=240, stretch=True, anchor="w")
        self.tree.column("state", width=110, minwidth=90, stretch=False, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")

        self.scrollbar = ttk.Scrollbar(holder, orient="vertical",
                                       command=self.tree.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=self._on_scroll)

        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Shift-Button-1>", self._on_shift_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<space>", lambda _e: self._toggle(self.tree.focus()))

        tools = ttk.Frame(frm)
        tools.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(tools, text="Select all", command=self._all).pack(side="left")
        ttk.Button(tools, text="Clear all", command=self._none).pack(side="left", padx=8)
        ttk.Button(tools, text="Invert", command=self._invert).pack(side="left")
        self.missing_btn = ttk.Button(tools, text="Only the missing ones",
                                      command=self._only_missing)
        self.missing_btn.pack(side="left", padx=8)
        self.count_lbl = ttk.Label(tools, text="", style=MUTED)
        self.count_lbl.pack(side="right")

        actions = ttk.Frame(frm)
        actions.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(actions, text="Cancel", command=self._cancel).pack(side="right")
        self.ok_btn = ttk.Button(actions, text="Download", style="Accent.TButton",
                                 command=self._accept)
        self.ok_btn.pack(side="right", padx=(0, 8))

    def _fill(self, preselected) -> None:
        rows = []
        for media in self.bites:
            have = media.bite_id in self.already_have
            rows.append((media.bite_id, media.title, have))
            if preselected is None:
                # Default to what is actually missing: ticking files that are
                # already on disk only to skip them wastes the user's decision.
                if not have:
                    self._checked.add(media.bite_id)
            elif media.bite_id in preselected:
                self._checked.add(media.bite_id)

        blank = self._images.placeholder if self._images else ""
        for bite_id, title, have in rows:
            mark = CHECKED if bite_id in self._checked else UNCHECKED
            self.tree.insert("", "end", iid=bite_id, text=f"{mark}  {title}",
                             image=blank or "",
                             values=("already saved" if have else "",))
        self._update_count()

    # ------------------------------------------------------------------ #
    #  Pictures
    # ------------------------------------------------------------------ #
    def _on_scroll(self, first, last) -> None:
        self.scrollbar.set(first, last)
        # Coalesced: a drag fires this continuously, and asking for images on
        # every pixel would queue the whole profile a few rows at a time.
        if self._pending_scroll is not None:
            try:
                self.after_cancel(self._pending_scroll)
            except tk.TclError:
                pass
        try:
            self._pending_scroll = self.after(150, self._request_visible)
        except tk.TclError:
            self._pending_scroll = None

    def _visible_ids(self) -> list:
        """The rows actually on screen, plus a little either side."""
        ids = self.tree.get_children("")
        if not ids:
            return []
        try:
            first, last = self.tree.yview()
        except tk.TclError:
            return []
        n = len(ids)
        lo = max(0, int(first * n) - 5)
        hi = min(n, int(last * n) + 5)
        return list(ids[lo:hi])

    def _request_visible(self) -> None:
        """Shows what is cached and asks for the rest - only for what is on
        screen, because a picture here costs a full-size animation download."""
        self._pending_scroll = None
        if self._images is None:
            return
        wanted = self._visible_ids()
        for bite_id in wanted:
            self.set_image(bite_id, self._images.image_for(bite_id))
        self._images.want((bid, self._by_id[bid].image_url)
                          for bid in wanted if bid in self._by_id)

    def set_image(self, bite_id: str, image) -> None:
        """Called here and by the app when a fetched image lands."""
        if self.tree.exists(bite_id):
            self.tree.item(bite_id, image=image or "")

    # ------------------------------------------------------------------ #
    #  Right-click
    # ------------------------------------------------------------------ #
    def _build_menu(self, palette) -> None:
        opts = {}
        if palette is not None:
            opts = dict(background=palette.surface, foreground=palette.text,
                        activebackground=palette.select_bg,
                        activeforeground=palette.select_fg)
        self.menu = tk.Menu(self, tearoff=0, borderwidth=0, relief="flat", **opts)
        self.menu.add_command(label="Open in browser", command=self._open_row)
        # The picture in the row is one still frame. This opens the source the
        # site itself uses, animation and all, without downloading anything.
        self.menu.add_command(label="Open picture", command=self._open_image)
        self.menu.add_separator()
        self.menu.add_command(label="Copy URL", command=self._copy_row)
        self.menu.add_command(label="Copy picture URL", command=self._copy_image)
        self._menu_row: str | None = None

    def _on_right_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return "break"
        # Deliberately does not toggle: a right-click is a question about the
        # row, not a decision about it.
        self._menu_row = row
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
        return "break"

    def _row_url(self) -> str:
        return (f"https://blerp.com/soundbites/{self._menu_row}"
                if self._menu_row else "")

    def _image_url(self) -> str:
        media = self._by_id.get(self._menu_row or "")
        return media.image_url if media else ""

    def _open_row(self) -> None:
        self._open(self._row_url())

    def _open_image(self) -> None:
        self._open(self._image_url())

    def _copy_row(self) -> None:
        self._copy(self._row_url())

    def _copy_image(self) -> None:
        self._copy(self._image_url())

    @staticmethod
    def _open(url: str) -> None:
        if url:
            webbrowser.open(url)

    def _copy(self, url: str) -> None:
        if not url:
            return
        self.clipboard_clear()
        self.clipboard_append(url)
        if self._on_copy:
            # So the clipboard watcher doesn't offer back what was just copied.
            self._on_copy(url)

    def _centre(self, parent) -> None:
        self.update_idletasks()
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        except tk.TclError:
            return
        x = max(0, min(x, self.winfo_screenwidth() - self.winfo_reqwidth()))
        y = max(0, min(y, self.winfo_screenheight() - self.winfo_reqheight()))
        self.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------ #
    #  Ticking
    # ------------------------------------------------------------------ #
    def _on_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            self._anchor = row
            self._toggle(row)
        return "break"          # no highlight to fight with the ticks

    def _on_shift_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return "break"
        if self._anchor is None:
            self._toggle(row)
            self._anchor = row
            return "break"
        ids = list(self.tree.get_children(""))
        try:
            lo, hi = sorted((ids.index(self._anchor), ids.index(row)))
        except ValueError:
            return "break"
        # The row that was shift-clicked decides which way the whole span goes,
        # so a range reads the same as clicking its last item.
        want = row not in self._checked
        for bite_id in ids[lo:hi + 1]:
            self._set(bite_id, want)
        self._update_count()
        return "break"

    def _toggle(self, bite_id: str) -> None:
        if bite_id:
            self._set(bite_id, bite_id not in self._checked)
            self._update_count()

    def _set(self, bite_id: str, on: bool) -> None:
        if not self.tree.exists(bite_id):
            return
        if on:
            self._checked.add(bite_id)
        else:
            self._checked.discard(bite_id)
        text = self.tree.item(bite_id, "text")
        self.tree.item(bite_id, text=(CHECKED if on else UNCHECKED) + text[1:])

    def _all(self) -> None:
        self._set_many(lambda m: True)

    def _none(self) -> None:
        self._set_many(lambda m: False)

    def _invert(self) -> None:
        self._set_many(lambda m: m.bite_id not in self._checked)

    def _only_missing(self) -> None:
        self._set_many(lambda m: m.bite_id not in self.already_have)

    def _set_many(self, want) -> None:
        for media in self.bites:
            self._set(media.bite_id, bool(want(media)))
        self._update_count()

    def _update_count(self) -> None:
        n = len(self._checked)
        self.count_lbl.configure(text=f"{n} of {len(self.bites)} ticked")
        self.ok_btn.configure(state="normal" if n else "disabled",
                              text=f"Download {n}" if n else "Download")

    # ------------------------------------------------------------------ #
    #  Closing
    # ------------------------------------------------------------------ #
    def _accept(self) -> None:
        if not self._checked:
            return
        # Kept in the order the profile was scanned in, not the order they were
        # ticked, so the run matches what the list showed.
        self.result = [m for m in self.bites if m.bite_id in self._checked]
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        # A pending scroll timer would fire against a destroyed window, which in
        # the packaged build has nowhere to report itself.
        if self._pending_scroll is not None:
            try:
                self.after_cancel(self._pending_scroll)
            except tk.TclError:
                pass
            self._pending_scroll = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

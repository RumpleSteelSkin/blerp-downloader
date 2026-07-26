"""The Options window.

Everything the user configures lives here rather than on the main window, which
is now the download list. The controls bind to the *same* Tk variables the app
owns, so editing one writes straight into the app's Settings object - this
window holds no state of its own and there is nothing to apply or cancel.

It expects the app to provide, by name: the `v_*` variables, `_pick_ffmpeg_dir`,
`_clear_cache`, `_reset_settings`, `_check_updates`, `_persist_settings`,
`_busy_widgets` and `_busy`.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from blerp_downloader import maintenance

from .widgets import hint, section

PAD = 14


class OptionsWindow(tk.Toplevel):
    """A non-modal settings window. One at a time; reopening raises the first."""

    def __init__(self, app) -> None:
        super().__init__(app.root)
        self.app = app
        self.title("Options")
        self.resizable(False, False)
        # transient, never grab_set: a grab blocks the main loop, which is also
        # what drives the clipboard watch and every list update, so the app
        # would look frozen for as long as this window is open.
        self.transient(app.root)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())

        try:
            self.iconbitmap(app.icon_path)
        except tk.TclError:
            pass

        self._buttons: list = []
        self._build()
        self._sync_busy()
        self._place_over_parent()

    # ------------------------------------------------------------------ #
    #  Layout
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)

        row = self._build_ffmpeg(frm, 0)
        row = self._build_downloads(frm, row)
        row = self._build_clipboard(frm, row)
        row = self._build_notifications(frm, row)
        row = self._build_appearance(frm, row)
        row = self._build_maintenance(frm, row)

        ttk.Button(frm, text="Close", command=self._close) \
            .grid(row=row, column=0, sticky="e", pady=(18, 0))

    def _build_ffmpeg(self, frm: ttk.Frame, row: int) -> int:
        row = section(frm, row, "FFMPEG")
        grid = ttk.Frame(frm)
        grid.grid(row=row, column=0, sticky="ew")
        grid.columnconfigure(0, weight=1)
        ttk.Entry(grid, textvariable=self.app.v_ffmpeg_dir, width=42) \
            .grid(row=0, column=0, sticky="ew")
        ttk.Button(grid, text="Browse…", command=self.app._pick_ffmpeg_dir) \
            .grid(row=0, column=1, padx=(8, 0))
        hint(grid, "Leave empty to use the ffmpeg on your PATH",
             row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        return row + 1

    def _build_downloads(self, frm: ttk.Frame, row: int) -> int:
        row = section(frm, row, "DOWNLOADS")
        box = ttk.Frame(frm)
        box.grid(row=row, column=0, sticky="ew")

        line = ttk.Frame(box)
        line.pack(fill="x")
        ttk.Label(line, text="Limit per profile").pack(side="left", padx=(0, 8))
        ttk.Entry(line, width=7, textvariable=self.app.v_limit,
                  validate="key", validatecommand=self.app._digits_only) \
            .pack(side="left", padx=(0, 6))
        ttk.Label(line, text="blank = all", style="Muted.TLabel").pack(side="left")

        delay = ttk.Frame(box)
        delay.pack(fill="x", pady=(8, 0))
        ttk.Label(delay, text="Pause between blerps").pack(side="left", padx=(0, 8))
        ttk.Spinbox(delay, from_=0.0, to=10.0, increment=0.1, width=6, format="%.1f",
                    textvariable=self.app.v_bulk_delay).pack(side="left", padx=(0, 6))
        ttk.Label(delay, text="seconds", style="Muted.TLabel").pack(side="left")

        ttk.Checkbutton(box, text="Ask which blerps to take after scanning a profile",
                        variable=self.app.v_pick_blerps).pack(anchor="w", pady=(10, 0))
        ttk.Checkbutton(box, text="Overwrite files that already exist",
                        variable=self.app.v_overwrite).pack(anchor="w", pady=(6, 0))
        hint(box, "Off means an already-downloaded blerp is skipped, which is what "
                  "lets a stopped run carry on.").pack(anchor="w", padx=(PAD + 8, 0))
        return row + 1

    def _build_clipboard(self, frm: ttk.Frame, row: int) -> int:
        row = section(frm, row, "CLIPBOARD")
        box = ttk.Frame(frm)
        box.grid(row=row, column=0, sticky="ew")
        ttk.Checkbutton(box, text="Watch the clipboard for Blerp links",
                        variable=self.app.v_watch_clipboard).pack(anchor="w")
        ttk.Checkbutton(box, text="Download them straight away, without asking",
                        variable=self.app.v_auto_download).pack(anchor="w",
                                                                padx=(18, 0))
        hint(box, "Off means a caught link is offered on a card and waits in the "
                  "list until you press Start.").pack(anchor="w", padx=(PAD + 18, 0))
        return row + 1

    def _build_notifications(self, frm: ttk.Frame, row: int) -> int:
        row = section(frm, row, "NOTIFICATIONS")
        box = ttk.Frame(frm)
        box.grid(row=row, column=0, sticky="ew")
        ttk.Checkbutton(box, text="Windows notification when a download starts",
                        variable=self.app.v_notify_balloons).pack(anchor="w")
        ttk.Checkbutton(box, text="Show a card when a link is caught from the clipboard",
                        variable=self.app.v_notify_card).pack(anchor="w")
        ttk.Checkbutton(box, text="Fetch blerp images for the list",
                        variable=self.app.v_thumbnails).pack(anchor="w")
        hint(box, "Images come from the full-size animation, so they are fetched in "
                  "the background and cached.").pack(anchor="w", padx=(PAD + 8, 0))
        return row + 1

    def _build_appearance(self, frm: ttk.Frame, row: int) -> int:
        row = section(frm, row, "APPEARANCE")
        box = ttk.Frame(frm)
        box.grid(row=row, column=0, sticky="ew")

        line = ttk.Frame(box)
        line.pack(fill="x")
        ttk.Label(line, text="Theme").pack(side="left", padx=(0, 8))
        for label, value in (("Follow Windows", "auto"), ("Dark", "dark"),
                             ("Light", "light")):
            ttk.Radiobutton(line, text=label, value=value,
                            variable=self.app.v_theme).pack(side="left", padx=(0, 12))

        ttk.Checkbutton(box, text="Closing the window keeps the app in the tray",
                        variable=self.app.v_close_to_tray).pack(anchor="w", pady=(10, 0))
        hint(box, "So an accidental close can't abandon a download in progress. "
                  "Quit from the tray icon's menu.").pack(anchor="w", padx=(PAD + 8, 0))
        return row + 1

    def _build_maintenance(self, frm: ttk.Frame, row: int) -> int:
        row = section(frm, row, "MAINTENANCE")
        box = ttk.Frame(frm)
        box.grid(row=row, column=0, sticky="ew")
        buttons = ttk.Frame(box)
        buttons.pack(fill="x")
        for text, command in (("Clear cache…", self.app._clear_cache),
                              ("Reset settings…", self.app._reset_settings),
                              ("Check for updates", self.app._check_updates)):
            btn = ttk.Button(buttons, text=text, command=command)
            btn.pack(side="left", padx=(0, 8))
            self._buttons.append(btn)
        # Without a number there is nothing to tell the user whether clearing is
        # worth doing, so it either never happens or happens pointlessly.
        self.usage_lbl = hint(box, "")
        self.usage_lbl.pack(anchor="w", pady=(8, 0))
        self.refresh_usage()
        # Registered with the app so a download starting while this window is
        # open disables them too - clearing the cache mid-run would delete the
        # saved listing of the run in progress.
        self.app._busy_widgets.extend(self._buttons)
        return row + 1

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #
    def _place_over_parent(self) -> None:
        """Centres on the main window, kept fully on screen."""
        self.update_idletasks()
        root = self.app.root
        try:
            x = root.winfo_rootx() + (root.winfo_width() - self.winfo_reqwidth()) // 2
            y = root.winfo_rooty() + (root.winfo_height() - self.winfo_reqheight()) // 3
        except tk.TclError:
            return
        x = max(0, min(x, self.winfo_screenwidth() - self.winfo_reqwidth()))
        y = max(0, min(y, self.winfo_screenheight() - self.winfo_reqheight()))
        self.geometry(f"+{x}+{y}")

    def refresh_usage(self) -> None:
        """Re-measures the cache. Called on open and after clearing it."""
        label = getattr(self, "usage_lbl", None)
        if label is None or not label.winfo_exists():
            return
        usage = maintenance.cache_usage()
        label.configure(text=("Nothing cached right now." if not usage.total_bytes
                              else f"Cached: {usage.summary()}"))

    def _sync_busy(self) -> None:
        state = "disabled" if self.app._busy else "normal"
        for btn in self._buttons:
            btn.configure(state=state)

    def _close(self) -> None:
        for btn in self._buttons:
            try:
                self.app._busy_widgets.remove(btn)
            except ValueError:
                pass
        self._buttons.clear()
        self.app._persist_settings()
        self.destroy()

#!/usr/bin/env python3
"""
blerp_gui.py
============
A simple Tkinter frontend for blerp_downloader (stdlib only, no extra
dependency). Supports both single-blerp and bulk (user/profile) downloads.

Architecture note:
    Downloads never run on the main (GUI) thread - the window would freeze.
    The work runs on a background thread that writes progress/log messages to
    a queue; the main thread drains that queue periodically via
    root.after(...) and updates the UI. The background thread never touches
    Tkinter widgets directly (Tkinter is not thread-safe).

Run:  python blerp_gui.py
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
import webbrowser
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

import blerp_downloader as core
from blerp_downloader import theme as theming
from blerp_downloader import tray
from blerp_downloader.settings import MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH, THEMES
from blerp_downloader import queue_store as qs
from blerp_ui import (BitePicker, LinkCard, OptionsWindow, QueueView,
                      ThumbCache, section)
from blerp_ui import card
from blerp_ui.widgets import MUTED

# The Windows theme can change while the app is open, and Tk never sees the
# WM_SETTINGCHANGE that announces it. _poll already ticks every 100ms, so the
# registry is re-read every this-many ticks instead of on a second timer.
_THEME_CHECK_TICKS = 20

# How often the download list may be written. Every add/remove/status change
# marks it dirty; without a floor a long bulk run would rewrite the file on
# every row it finishes.
_QUEUE_WRITE_INTERVAL = 2.0


def resource_path(rel: str) -> Path:
    """Resource path - works both run from source and inside a PyInstaller bundle."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / rel


def _as_float(text: str, fallback: float) -> float:
    """A Spinbox hands back whatever is in the box, including "" mid-edit."""
    try:
        return max(0.0, float(text))
    except (TypeError, ValueError):
        return fallback


def detect_mode(target: str) -> tuple[str, str]:
    """
    Classifies the single text box's content as ('single', url) or ('bulk', username)
    (mirrors the CLI's detection logic):
      • /u/<username> profile URL           -> bulk
      • soundbite URL (24-hex ObjectId)     -> single
      • plain username (not a URL)          -> bulk
    """
    target = target.strip()
    username = core.parse_username(target)          # /u/<username>
    if username:
        return "bulk", username
    if "/soundbites/" in target or core.OBJECTID_RE.search(target):
        return "single", target
    if target and "://" not in target and "/" not in target:
        return "bulk", target                       # plain username
    return "single", target


def looks_like_blerp_soundbite_url(text: str) -> str | None:
    """
    Strict single-soundbite match for clipboard watching only: the host must
    actually be blerp.com, unlike the paste box's looser detect_mode(), because
    this runs unattended against whatever happens to be on the clipboard.

    The host is compared after parsing rather than by searching the string:
    "blerp.com" appearing anywhere - in a path, a query parameter, or as
    "blerp.com.attacker.tld" - is not the same as the URL pointing at Blerp,
    and with auto-download enabled the difference is a fetch nobody approved.
    """
    text = (text or "").strip()
    if not core.is_blerp_url(text):
        return None
    if "/soundbites/" in text or core.OBJECTID_RE.search(text):
        return text
    return None


class BlerpGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel = threading.Event()
        self.settings = core.load_settings()
        self._closing = False   # set before destroy() so _poll stops rescheduling itself
        self._busy = False
        self._busy_widgets: list = []
        self._options: OptionsWindow | None = None
        self.icon_path = str(resource_path("assets/icon.ico"))

        # The download list. Main thread only - see queue_store's docstring.
        self.items: list = core.load_queue()
        self._by_id = {i.item_id: i for i in self.items}
        self._queue_dirty = False
        self._last_queue_write = 0.0
        self._active_id: str | None = None
        # bite_id -> the profile child rows currently showing its image
        self._children_showing: dict = {}
        self.tray = None
        self._card: LinkCard | None = None
        self._picker = None
        # True from just before the picker is built until it closes.
        self._picking = False
        self._hidden = False
        self._told_about_tray = False
        self._announced: set = set()   # rows already notified about this run
        # The worker parks on this while the picker is open.
        self._choice_ready = threading.Event()
        self._choice = None

        # Baseline the clipboard so whatever was already copied before the app
        # opened doesn't immediately trigger a prompt/auto-download.
        try:
            self._last_clipboard = self.root.clipboard_get()
        except tk.TclError:
            self._last_clipboard = ""

        root.title(core.APP_NAME)
        try:  # window icon (bundled or from source)
            root.iconbitmap(self.icon_path)
        except Exception:
            pass  # fine if there's no icon
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._mode = theming.resolve_mode(self.settings.theme)
        self._plain = theming.high_contrast_active()
        self.palette = (theming.palette_for(self._mode) if self._plain
                        else theming.apply_theme(root, self._mode))
        self._tick = 0

        self._build_vars()
        self._build()
        self._apply_geometry()
        self._thumbs = ThumbCache(
            root, self._on_thumb_ready,
            enabled=lambda: self.settings.thumbnails,
            # "Busy" means a download is actually using the connection. While
            # the picker is open the worker is alive but parked waiting for an
            # answer, so the connection is free. Keyed on an explicit flag
            # rather than on the picker existing: the flag can be set before
            # the window is built, and the window's first request for images
            # happens inside its own constructor.
            busy=lambda: bool(self.worker and self.worker.is_alive()
                              and not self._picking))
        self._restore_list()
        self._install_tray()
        try:
            core.cleanup_old_downloads()
        except Exception:
            pass  # stale-file cleanup must never block startup
        self.root.after(100, self._poll)

    # ------------------------------------------------------------------ #
    #  Settings-backed variables
    #
    #  self.settings is the single source of truth; every control edits it as
    #  the user types. Rebuilding Settings from the widgets instead - as this
    #  used to - only works while every control exists on one window, and it
    #  quietly writes stale values back over anything changed elsewhere.
    # ------------------------------------------------------------------ #
    def _build_vars(self) -> None:
        s = self.settings
        # Digits or empty. Validating on keystroke means "abc" can't be entered
        # at all, so there is no invalid state for anything downstream to check.
        self._digits_only = (self.root.register(
            lambda proposed: proposed == "" or proposed.isdigit()), "%P")

        self.v_out = self._tracked(tk.StringVar(value=s.output_dir),
                                   lambda v: self._assign("output_dir", v.strip()))
        self.v_ffmpeg_dir = self._tracked(tk.StringVar(value=s.ffmpeg_dir),
                                          lambda v: self._assign("ffmpeg_dir", v.strip()))
        self.v_limit = self._tracked(
            tk.StringVar(value="" if s.bulk_limit is None else str(s.bulk_limit)),
            lambda v: self._assign("bulk_limit", int(v) if v.strip() else None))
        self.v_overwrite = self._tracked(tk.BooleanVar(value=s.overwrite),
                                         lambda v: self._assign("overwrite", v))
        self.v_watch_clipboard = self._tracked(
            tk.BooleanVar(value=s.clipboard_watch),
            lambda v: self._assign("clipboard_watch", v))
        self.v_auto_download = self._tracked(
            tk.BooleanVar(value=s.clipboard_mode == "auto"),
            lambda v: self._assign("clipboard_mode", "auto" if v else "ask"))
        # Spinbox writes a string, and an empty box while the user retypes it
        # must not become a crash or a zero delay.
        self.v_bulk_delay = self._tracked(
            tk.StringVar(value=f"{s.bulk_delay:.1f}"),
            lambda v: self._assign("bulk_delay", _as_float(v, s.bulk_delay)))
        self.v_theme = self._tracked(tk.StringVar(value=s.theme), self._apply_theme_choice)
        self.v_close_to_tray = self._tracked(tk.BooleanVar(value=s.close_to_tray),
                                             lambda v: self._assign("close_to_tray", v))
        self.v_notify_balloons = self._tracked(
            tk.BooleanVar(value=s.notify_balloons),
            lambda v: self._assign("notify_balloons", v))
        self.v_notify_card = self._tracked(tk.BooleanVar(value=s.notify_card),
                                           lambda v: self._assign("notify_card", v))
        self.v_thumbnails = self._tracked(tk.BooleanVar(value=s.thumbnails),
                                          lambda v: self._assign("thumbnails", v))
        self.v_pick_blerps = self._tracked(tk.BooleanVar(value=s.pick_blerps),
                                           lambda v: self._assign("pick_blerps", v))

    def _tracked(self, var, apply):
        """A Tk variable that writes straight through to self.settings.

        trace_add rather than a <FocusOut> binding: a trace cannot miss an edit,
        and it doesn't depend on the widget losing focus before the app closes.
        """
        var.trace_add("write", lambda *_: apply(var.get()))
        return var

    def _assign(self, name: str, value) -> None:
        setattr(self.settings, name, value)

    def _apply_theme_choice(self, choice: str) -> None:
        """Theme is the one setting with an immediate visible effect."""
        self._assign("theme", choice if choice in THEMES else "auto")
        self._retheme(theming.resolve_mode(self.settings.theme))

    def _refresh_from_settings(self) -> None:
        """Pushes self.settings back into the controls (used after a reset)."""
        s = self.settings
        self.v_out.set(s.output_dir)
        self.v_ffmpeg_dir.set(s.ffmpeg_dir)
        self.v_limit.set("" if s.bulk_limit is None else str(s.bulk_limit))
        self.v_overwrite.set(s.overwrite)
        self.v_watch_clipboard.set(s.clipboard_watch)
        self.v_auto_download.set(s.clipboard_mode == "auto")
        self.v_bulk_delay.set(f"{s.bulk_delay:.1f}")
        self.v_theme.set(s.theme)
        self.v_close_to_tray.set(s.close_to_tray)
        self.v_notify_balloons.set(s.notify_balloons)
        self.v_notify_card.set(s.notify_card)
        self.v_thumbnails.set(s.thumbnails)
        self.v_pick_blerps.set(s.pick_blerps)

    def _apply_geometry(self) -> None:
        """Sizes the window from the built layout, so the minimum follows the
        actual content rather than a hardcoded guess."""
        # update_idletasks, never update(): update() re-enters the event loop,
        # and a queued close would run _on_close against half-built widgets.
        self.root.update_idletasks()
        min_w = min(self.root.winfo_reqwidth(), self.root.winfo_screenwidth() - 80)
        min_h = min(self.root.winfo_reqheight(), self.root.winfo_screenheight() - 80)
        min_w, min_h = max(min_w, MIN_WINDOW_WIDTH), max(min_h, MIN_WINDOW_HEIGHT)
        self.root.minsize(min_w, min_h)
        self.root.geometry(f"{max(self.settings.window_width, min_w)}x"
                           f"{max(self.settings.window_height, min_h)}")

    # ------------------------------------------------------------------ #
    #  UI construction
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        self._tune_fonts()
        frm = ttk.Frame(self.root, padding=16)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)

        row = self._build_header(frm, 0)
        row = self._build_source(frm, row)
        row = self._build_destination(frm, row)
        row = self._build_list(frm, row)
        row = self._build_actions(frm, row)
        self._build_log(frm, row)

    def _tune_fonts(self) -> None:
        """Segoe UI where available; Tk's default is a decade out of date."""
        try:
            families = set(font.families())
            if "Segoe UI" not in families:
                return
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                try:
                    font.nametofont(name).configure(family="Segoe UI", size=9)
                except tk.TclError:
                    pass
        except tk.TclError:
            pass

    def _build_header(self, frm: ttk.Frame, row: int) -> int:
        bar = ttk.Frame(frm)
        bar.grid(row=row, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text=core.APP_NAME, style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(bar, text=f"v{core.__version__}", style="Muted.TLabel") \
            .grid(row=0, column=2, sticky="e")
        return row + 1

    def _build_source(self, frm: ttk.Frame, row: int) -> int:
        row = section(frm, row, "ADD TO LIST")
        line = ttk.Frame(frm)
        line.grid(row=row, column=0, sticky="ew")
        line.columnconfigure(0, weight=1)
        self.target = ttk.Entry(line)
        self.target.grid(row=0, column=0, sticky="ew")
        self.target.bind("<Return>", lambda _e: self._add_target())
        ttk.Button(line, text="Add", command=self._add_target) \
            .grid(row=0, column=1, padx=(8, 0))
        ttk.Label(frm, text="A soundbite URL, or a username / profile URL for the whole profile",
                  style=MUTED).grid(row=row + 1, column=0, sticky="w", pady=(4, 0))
        return row + 2

    def _build_list(self, frm: ttk.Frame, row: int) -> int:
        row = section(frm, row, "DOWNLOAD LIST")
        self.view = QueueView(frm, on_selection_change=self._update_list_buttons,
                              on_activate=self._open_selected)
        self.view.grid(row=row, column=0, sticky="nsew")
        # The list is what absorbs the window's spare height; everything else
        # keeps its natural size.
        frm.rowconfigure(row, weight=1)
        self.view.tree.bind("<Delete>", lambda _e: self._remove_selected())
        self.view.tree.bind("<<TreeviewOpen>>", self._on_expand)
        self.view.tree.bind("<Button-3>", self._on_right_click)
        self._build_row_menu()

        tools = ttk.Frame(frm)
        tools.grid(row=row + 1, column=0, sticky="ew", pady=(8, 0))
        self.remove_btn = ttk.Button(tools, text="Remove", command=self._remove_selected,
                                     state="disabled")
        self.remove_btn.pack(side="left")
        self.clear_done_btn = ttk.Button(tools, text="Clear finished",
                                         command=self._clear_finished)
        self.clear_done_btn.pack(side="left", padx=8)
        self.clear_btn = ttk.Button(tools, text="Clear list…", command=self._clear_list)
        self.clear_btn.pack(side="left")
        self.count_lbl = ttk.Label(tools, text="", style=MUTED)
        self.count_lbl.pack(side="right")
        return row + 2

    def _build_destination(self, frm: ttk.Frame, row: int) -> int:
        row = section(frm, row, "DESTINATION")
        grid = ttk.Frame(frm)
        grid.grid(row=row, column=0, sticky="ew")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="Output").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.out = ttk.Entry(grid, textvariable=self.v_out)
        self.out.grid(row=0, column=1, sticky="ew")
        ttk.Button(grid, text="Choose…", command=self._pick_dir) \
            .grid(row=0, column=2, padx=(8, 0))

        ttk.Label(grid, text="Leave empty to save next to the app",
                  style="Muted.TLabel").grid(row=1, column=1, columnspan=2,
                                             sticky="w", pady=(4, 0))
        return row + 1

    def _build_actions(self, frm: ttk.Frame, row: int) -> int:
        ttk.Separator(frm, orient="horizontal").grid(row=row, column=0, sticky="ew", pady=(16, 12))

        # What the user came here to do.
        btns = ttk.Frame(frm)
        btns.grid(row=row + 1, column=0, sticky="ew")
        self.dl_btn = ttk.Button(btns, text="Start", command=self._start,
                                 style="Accent.TButton")
        self.dl_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        self.opt_btn = ttk.Button(btns, text="Options…", command=self._open_options)
        self.opt_btn.pack(side="right")

        self.prog = ttk.Progressbar(frm, mode="determinate")
        self.prog.grid(row=row + 2, column=0, sticky="ew", pady=(14, 6))
        self.status = ttk.Label(frm, text="Ready.", style="Status.TLabel")
        self.status.grid(row=row + 3, column=0, sticky="w")

        # Everything that must be unavailable while work is running. A list
        # rather than a tuple because the Options window adds its maintenance
        # buttons here for as long as it is open - clearing the cache mid-run
        # would delete the saved listing of the download in progress.
        self._busy_widgets = [self.dl_btn]
        return row + 4

    def _build_log(self, frm: ttk.Frame, row: int) -> None:
        # Collapsed by default. The list now says what is happening, so the log
        # is for the times that isn't enough - an ffmpeg error, a 404, the scan
        # counter - and it costs ~150px of window height when it is open.
        self._log_row = row + 1
        self.v_log_open = tk.BooleanVar(value=self.settings.log_expanded)
        toggle = ttk.Checkbutton(frm, text="Details", style="Toolbutton",
                                 variable=self.v_log_open, command=self._toggle_log)
        toggle.grid(row=row, column=0, sticky="w", pady=(10, 0))

        # tk.Text + ttk.Scrollbar rather than ScrolledText: that one is built
        # from classic widgets, so it carries a grey frame and a scrollbar that
        # ignores colours on Windows.
        self.log_wrap = ttk.Frame(frm)
        self.log_wrap.columnconfigure(0, weight=1)
        self.log_wrap.rowconfigure(0, weight=1)

        # width=1 deliberately: Text sizes itself in characters, and the default
        # 80 would demand a ~644px window on its own.
        self.log = tk.Text(self.log_wrap, height=8, width=1, state="disabled",
                           wrap="word", bd=0, relief="flat", highlightthickness=1,
                           padx=10, pady=8)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self.log_wrap, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        ttk.Label(frm, text=core.SIGNATURE, style=MUTED) \
            .grid(row=row + 2, column=0, sticky="e", pady=(8, 0))
        self._paint_log()
        self._toggle_log()

    def _toggle_log(self) -> None:
        """Shows or hides the details pane, remembering which for next time."""
        show = self.v_log_open.get()
        self.settings.log_expanded = show
        if show:
            self.log_wrap.grid(row=self._log_row, column=0, sticky="nsew",
                               pady=(6, 0))
        else:
            self.log_wrap.grid_remove()

    def _paint_log(self) -> None:
        """Colours the classic Text widget; ttk styles don't reach it."""
        p = self.palette
        try:
            self.log.configure(
                background=p.log_bg, foreground=p.text, insertbackground=p.text,
                selectbackground=p.select_bg, selectforeground=p.select_fg,
                highlightbackground=p.border, highlightcolor=p.border,
                font=("Consolas", 9),
            )
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool) -> None:
        """Flips the whole control set between idle and working.

        One place rather than the enable/disable pairs that were repeated at
        every call site - which is also what keeps the maintenance buttons from
        being clickable mid-run. The flag is kept because the Options window can
        open at any point and has to match the state already in effect.
        """
        self._busy = busy
        self._refresh_tray_menu()
        state = "disabled" if busy else "normal"
        for w in self._busy_widgets:
            try:
                w.configure(state=state)
            except tk.TclError:
                pass   # a window closed between registering and this call
        self.stop_btn.configure(state="normal" if busy else "disabled")

    def _open_options(self) -> None:
        if self._options is not None and self._options.winfo_exists():
            self._options.lift()
            self._options.focus_set()
            return
        self._options = OptionsWindow(self)

    # ------------------------------------------------------------------ #
    #  The download list (main thread owns it; workers only report back)
    # ------------------------------------------------------------------ #
    def _touch_queue(self, *, now: bool = False) -> None:
        """Marks the list as needing a write; _poll does the writing.

        Called on structural changes and status transitions, never on progress
        ticks - a bulk run would otherwise rewrite the file thousands of times
        to record percentages that aren't persisted anyway.
        """
        self._queue_dirty = True
        if now:
            self._flush_queue()

    def _flush_queue(self) -> None:
        core.save_queue(self.items)
        self._queue_dirty = False
        self._last_queue_write = time.monotonic()

    def _restore_list(self) -> None:
        """Draws the saved list at startup."""
        for item in self.items:
            self.view.add(item, image=self._thumbs.image_for(item.bite_id))
        self._drop_evicted()
        self._update_list_buttons()
        waiting = sum(1 for i in self.items if i.is_pending)
        if waiting:
            self._log(f"{waiting} item(s) still waiting from last time. "
                      "Press Start to carry on.")

    def _add_target(self, text: str | None = None, *, start: bool = False) -> None:
        """Turns whatever is in the box (or a caught link) into a row."""
        raw = (text if text is not None else self.target.get()).strip()
        if not raw:
            self._log("⚠ Paste a soundbite URL, or a username for a whole profile.")
            return
        if len(self.items) >= qs.MAX_QUEUE_ITEMS:
            self._log(f"⚠ The list is full ({qs.MAX_QUEUE_ITEMS}). "
                      "Remove some rows first.")
            return

        mode, value = detect_mode(raw)
        item = qs.QueueItem(
            item_id=uuid.uuid4().hex,
            kind="profile" if mode == "bulk" else "single",
            url=raw,
            username=value if mode == "bulk" else "",
            added_at=time.time(),
            limit=self.settings.bulk_limit,
            overwrite=self.settings.overwrite,
        )
        if self._duplicate_of(item):
            self._log(f"Already in the list: {item.label}")
            return

        self.items.append(item)
        self._by_id[item.item_id] = item
        self.view.add(item)
        self.view.see(item.item_id)
        if text is None:
            self.target.delete(0, "end")
        self._update_list_buttons()
        # Written immediately: a link pasted and then lost to a crash is
        # precisely the annoyance the saved list exists to prevent.
        self._touch_queue(now=True)
        self._request_thumb(item)
        # Auto-download means exactly that. Leaving the link sitting in the list
        # until Start is pressed would make the setting do nothing on its own.
        if start and not (self.worker and self.worker.is_alive()):
            self._start()

    def _duplicate_of(self, item) -> bool:
        """Whether an unfinished row already covers the same thing."""
        for other in self.items:
            if other.is_finished or other.kind != item.kind:
                continue
            if item.kind == "profile":
                if other.username.lower() == item.username.lower():
                    return True
            elif other.url == item.url:
                return True
        return False

    def _update_list_buttons(self) -> None:
        selected = self.view.selection()
        # The running row can't be removed: the worker is holding a snapshot of
        # it and cancelling mid-blerp isn't possible.
        removable = [i for i in selected if i != self._active_id]
        self.remove_btn.configure(
            state="normal" if removable else "disabled")
        if selected and not removable:
            self.status.configure(text="That one is downloading — press Stop first.")
        waiting = sum(1 for i in self.items if i.is_pending)
        done = sum(1 for i in self.items if i.is_finished)
        self.count_lbl.configure(
            text=f"{len(self.items)} in list · {waiting} waiting · {done} finished"
            if self.items else "List is empty")
        self._refresh_tray_menu()

    def _remove_selected(self) -> None:
        removed = 0
        for item_id in self.view.selection():
            if item_id == self._active_id:
                continue
            item = self._by_id.pop(item_id, None)
            if item is not None:
                self.items.remove(item)
                self.view.remove(item_id)
                removed += 1
        if removed:
            self._update_list_buttons()
            self._touch_queue(now=True)

    def _clear_finished(self) -> None:
        keep = [i for i in self.items if not i.is_finished]
        if len(keep) == len(self.items):
            return
        for item in self.items:
            if item.is_finished:
                self.view.remove(item.item_id)
                self._by_id.pop(item.item_id, None)
        self.items[:] = keep
        self._update_list_buttons()
        self._touch_queue(now=True)

    def _clear_list(self) -> None:
        if not self.items:
            return
        if not messagebox.askyesno(
                "Clear list",
                f"Remove all {len(self.items)} item(s) from the list?\n\n"
                "Files you have already downloaded are not touched."):
            return
        keep = [i for i in self.items if i.item_id == self._active_id]
        self.view.clear()
        self.items[:] = keep
        self._by_id = {i.item_id: i for i in keep}
        for item in keep:
            self.view.add(item)
        self._update_list_buttons()
        self._touch_queue(now=True)

    def _build_row_menu(self) -> None:
        """The right-click menu for a list row."""
        p = self.palette
        self.row_menu = tk.Menu(self.root, tearoff=0, background=p.surface,
                                foreground=p.text, activebackground=p.select_bg,
                                activeforeground=p.select_fg, borderwidth=0,
                                relief="flat")
        self.row_menu.add_command(label="Copy URL", command=self._copy_url)
        self.row_menu.add_command(label="Open in browser", command=self._open_url)
        # The row shows one still frame; this opens the source the site itself
        # uses, animation and all, without downloading anything.
        self.row_menu.add_command(label="Open picture", command=self._open_image)
        self.row_menu.add_separator()
        self.row_menu.add_command(label="Open containing folder",
                                  command=self._open_selected)
        self.row_menu.add_command(label="Download again", command=self._retry_selected)
        self.row_menu.add_separator()
        self.row_menu.add_command(label="Remove from list",
                                  command=self._remove_selected)

    def _on_right_click(self, event) -> None:
        row = self.view.tree.identify_row(event.y)
        if not row:
            return
        # Right-clicking outside the selection acts on what was clicked, which
        # is what every list on Windows does; inside it, the selection stands.
        if row not in self.view.tree.selection():
            self.view.tree.selection_set(row)
        self.view.tree.focus(row)
        try:
            self.row_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.row_menu.grab_release()

    def _selected_items(self) -> list:
        """The QueueItems behind the selection, parents of child rows included."""
        found, seen = [], set()
        for iid in self.view.tree.selection():
            parent = self.view.tree.parent(iid)
            item = self._by_id.get(parent or iid)
            if item is not None and item.item_id not in seen:
                seen.add(item.item_id)
                found.append(item)
        return found

    def _selected_url(self) -> str:
        """The blerp's own page URL, even for a row inside a profile."""
        for iid in self.view.tree.selection():
            if self.view.tree.parent(iid):
                # A child row's id is "<parent>:<bite id>", and a blerp's page
                # is reachable from the id alone.
                bite_id = iid.rsplit(":", 1)[-1]
                if core.OBJECTID_RE.fullmatch(bite_id):
                    return f"https://blerp.com/soundbites/{bite_id}"
            item = self._by_id.get(iid)
            if item is not None:
                if item.kind == "profile":
                    return f"https://blerp.com/u/{item.username}"
                return item.url
        return ""

    def _note_clipboard(self, text: str) -> None:
        """Marks text as ours, so copying it doesn't come straight back as a
        'Blerp link detected' offer."""
        self._last_clipboard = text

    def _copy_url(self) -> None:
        url = self._selected_url()
        if not url:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        # Otherwise what was just copied would trip the watcher and be offered
        # straight back to the user.
        self._note_clipboard(url)
        self.status.configure(text=f"Copied {url}")

    def _open_url(self) -> None:
        url = self._selected_url()
        if url:
            webbrowser.open(url)

    def _open_image(self) -> None:
        """The blerp's picture, from the saved listing that named it."""
        url = self._selected_image_url()
        if url:
            webbrowser.open(url)
        else:
            self.status.configure(
                text="No picture known for that row yet — scan the profile first.")

    def _selected_image_url(self) -> str:
        for iid in self.view.tree.selection():
            parent = self.view.tree.parent(iid)
            bite_id = iid.rsplit(":", 1)[-1] if parent else \
                (self._by_id.get(iid).bite_id if self._by_id.get(iid) else "")
            if not core.OBJECTID_RE.fullmatch(bite_id or ""):
                continue
            owner = self._by_id.get(parent or iid)
            job = core.load_job(owner.username) if owner and owner.username else None
            for media in (job.bites if job else ()):
                if media.bite_id == bite_id:
                    return media.image_url
        return ""

    def _retry_selected(self) -> None:
        """Puts finished rows back in the line."""
        changed = 0
        for item in self._selected_items():
            if item.item_id == self._active_id or item.status == qs.QUEUED:
                continue
            item.status, item.error = qs.QUEUED, ""
            self.view.update_item(item)
            changed += 1
        if changed:
            self._update_list_buttons()
            self._touch_queue(now=True)
            self.status.configure(text=f"{changed} back in the list — press Start.")

    def _on_expand(self, _event=None) -> None:
        """Fills a profile row in the first time it is opened.

        Built on demand rather than at load: the blerps come from the saved
        listing, which for a large profile is a megabyte of JSON nobody should
        pay to read unless they actually look inside the row.
        """
        item_id = self.view.tree.focus()
        item = self._by_id.get(item_id)
        if item is None or item.kind != "profile" or self.view.has_children(item_id):
            return

        job = core.load_job(item.username)
        if job is None or not job.matches(item.username):
            self.view.placeholder_child(
                item_id, "Nothing scanned yet — press Start to read this profile.")
            return

        out_dir = Path(job.out_dir) if job.out_dir else Path(core.sanitize(item.username))
        bites = job.bites[:job.limit] if job.limit else job.bites
        rows = [(m.bite_id, m.title,
                 "✓ Saved" if core.bulk_out_path(out_dir, m).exists() else "Waiting")
                for m in bites]
        self.view.set_children(item_id, rows)

        if self.settings.thumbnails:
            # These are the one place a fetch is worth it: the saved listing
            # already carries every image URL, so no page has to be read first.
            for m in bites:
                child_id = f"{item_id}:{m.bite_id}"
                self._children_showing.setdefault(m.bite_id, set()).add(child_id)
                self.view.set_image(child_id, self._thumbs.image_for(m.bite_id))
            self._drop_evicted()
            self._thumbs.want((m.bite_id, m.image_url) for m in bites)
        if not job.scan_complete:
            self._log(f"The scan of {item.username} was interrupted, so this may "
                      "not be the whole profile.")

    def _request_thumb(self, item) -> None:
        """Shows a row's image if one is cached.

        Never fetches: a single row's image URL is only known after its page has
        been read, and scraping a page purely to decorate a row the user may
        never start is not a trade worth making. The image turns up for free
        when the blerp is downloaded.
        """
        if not self.settings.thumbnails or not item.bite_id:
            return
        self.view.set_image(item.item_id, self._thumbs.image_for(item.bite_id))
        self._drop_evicted()

    def _announce(self, item) -> None:
        """Tells the user a blerp has started, whether or not the window is up.

        Once per row, never once per blerp inside a profile: a 3,000-blerp run
        would otherwise fire 3,000 notifications, which is how an app gets its
        notifications switched off for good. A profile gets one at the start and
        one summary at the end.
        """
        if not self.settings.notify_balloons or self.tray is None:
            return
        if item.item_id in self._announced:
            return
        self._announced.add(item.item_id)
        ico = core.cached_ico(item.bite_id) if item.bite_id else None
        self.tray.notify("Downloading", item.label,
                         ico_path=str(ico) if ico else "", key=item.bite_id)

    def _announce_finished(self, ok: int, failed: int) -> None:
        """One notification when the whole run is over."""
        if not self.settings.notify_balloons or self.tray is None or not ok + failed:
            return
        text = f"{ok} downloaded" + (f", {failed} failed" if failed else "")
        self.tray.notify("Blerp Downloader", text)

    def _on_thumb_ready(self, bite_id: str) -> None:
        """Worker thread: a fetched image landed. Hand it to the main thread."""
        self.q.put(("thumb", bite_id))

    def _apply_thumb(self, bite_id: str) -> None:
        """Main thread: put a newly cached image on every row that wants it."""
        image = self._thumbs.image_for(bite_id)
        if self._picker is not None:
            try:
                self._picker.set_image(bite_id, image)
            except tk.TclError:
                self._picker = None
        for item in self.items:
            if item.bite_id == bite_id:
                self.view.set_image(item.item_id, image)
        for child_id in self._children_showing.get(bite_id, ()):
            self.view.set_image(child_id, image)
        self._drop_evicted()

    def _drop_evicted(self) -> None:
        """Resets rows whose image has just been evicted from the cache.

        A Treeview row holds only the image's name, so it has to be pointed back
        at the placeholder before the last reference goes or the cell goes blank.
        """
        gone = self._thumbs.take_evicted()
        if not gone:
            return
        for item in self.items:
            if item.bite_id in gone:
                self.view.set_image(item.item_id, self._thumbs.placeholder)
        for bite_id in gone:
            for child_id in self._children_showing.get(bite_id, ()):
                self.view.set_image(child_id, self._thumbs.placeholder)

    def _open_selected(self) -> None:
        """Double-click: reveal a finished download."""
        for item_id in self.view.selection():
            item = self._by_id.get(item_id)
            if item and item.out_path:
                target = Path(item.out_path)
                folder = target.parent if target.suffix else target
                try:
                    os.startfile(str(folder))          # noqa: S606 - Windows shell
                except (OSError, AttributeError) as e:
                    self._log(f"⚠ Could not open {folder}: {e}")
            return

    def _stop(self) -> None:
        """Stop, with an honest account of when it will take effect.

        A download in flight can't be interrupted - the current blerp has to
        finish downloading and encoding first, which can take a while. Without
        this the button looked broken for minutes.
        """
        self.cancel.set()
        self.stop_btn.configure(state="disabled")
        self.status.configure(text="Stopping after the current blerp…")
        self._log("⏹ Stopping — the blerp in progress has to finish first. "
                  "Press Start afterwards to carry on where this left off.")

    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(title="Choose output folder")
        if d:
            self.v_out.set(d)

    def _pick_ffmpeg_dir(self) -> None:
        d = filedialog.askdirectory(title="Choose the folder containing ffmpeg.exe/ffprobe.exe")
        if d:
            self.v_ffmpeg_dir.set(d)

    # ------------------------------------------------------------------ #
    #  Settings persistence
    # ------------------------------------------------------------------ #
    def _capture_geometry(self) -> None:
        """Records the window size, unless the window can't report a real one.

        Keyed on the window actually being mapped rather than on the reported
        size looking plausible: a withdrawn window reports Tk's default 200x200,
        which passes any size threshold and would shrink the window every time
        the app saved while hidden in the tray.
        """
        try:
            if not self.root.winfo_ismapped():
                return
            w, h = self.root.winfo_width(), self.root.winfo_height()
        except tk.TclError:
            return   # torn down mid-save; the stored size is still the right one
        if w >= MIN_WINDOW_WIDTH and h >= MIN_WINDOW_HEIGHT:
            self.settings.window_width, self.settings.window_height = w, h

    def _persist_settings(self) -> None:
        self._capture_geometry()
        try:
            core.save_settings(self.settings)
        except OSError as e:
            # A full or read-only profile directory must not stop the app from
            # closing; there is nowhere useful to show this at teardown.
            self._log(f"⚠ Could not save settings: {e}")

    # ------------------------------------------------------------------ #
    #  Notification area
    # ------------------------------------------------------------------ #
    def _install_tray(self) -> None:
        if not self.settings.tray_enabled or not tray.available():
            return
        icon = tray.TrayIcon(self.icon_path, core.APP_NAME, self._on_tray_event)
        if icon.install():
            self.tray = icon
            self._refresh_tray_menu()

    def _refresh_tray_menu(self) -> None:
        if self.tray is None:
            return
        waiting = any(i.is_pending for i in self.items)
        self.tray.set_menu((
            (tray.MENU_OPEN, "Open Blerp Downloader", True),
            (0, "", True),
            (tray.MENU_START, "Start downloads", waiting and not self._busy),
            (tray.MENU_STOP, "Stop", self._busy),
            (0, "", True),
            (tray.MENU_QUIT, "Quit", True),
        ))

    def _on_tray_event(self, name: str, payload) -> None:
        """Window-procedure thread. Enqueue only - never touch Tk from here."""
        self.q.put(("tray", (name, payload)))

    def _handle_tray(self, name: str, payload) -> None:
        if name in (tray.ACTIVATE, tray.BALLOON_CLICK):
            self._restore_from_tray()
        elif name == tray.CLOSE:
            # Restart Manager during an update, or Windows shutting down. Both
            # want the process gone, and neither waits long.
            self._quit()
        elif name == tray.MENU:
            self._handle_tray_menu(payload)

    def _handle_tray_menu(self, item_id: int) -> None:
        if item_id == tray.MENU_OPEN:
            self._restore_from_tray()
        elif item_id == tray.MENU_START:
            self._restore_from_tray()
            self._start()
        elif item_id == tray.MENU_STOP:
            self._stop()
        elif item_id == tray.MENU_QUIT:
            if self._busy:
                self._restore_from_tray()
                if not messagebox.askyesno(
                        "Quit", "A download is still running.\n\nQuit anyway? The "
                                "blerp in progress is lost, but everything already "
                                "saved is kept, and the list picks up from there."):
                    return
            self._quit()

    def _hide_to_tray(self) -> None:
        """X with the tray icon up: stay running, keep downloading."""
        # Before withdrawing: a hidden window cannot report its size.
        self._persist_settings()
        self._flush_queue()
        self.root.withdraw()
        self._hidden = True
        if not self._told_about_tray and self.tray is not None:
            self._told_about_tray = True
            self.tray.notify(core.APP_NAME,
                             "Still running here. Right-click this icon to quit.")

    def _restore_from_tray(self) -> None:
        self._hidden = False
        try:
            self.root.deiconify()
            self.root.lift()
            # Windows refuses a plain focus request from a process that isn't
            # already in front, which is exactly the case when this is triggered
            # by a second launch. Flicking topmost on and straight back off is
            # the usual way through it without pinning the window over
            # everything else afterwards.
            self.root.attributes("-topmost", True)
            self.root.attributes("-topmost", False)
            self.root.focus_force()
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        """The window's X. Hides rather than quits while the tray icon is up.

        withdraw(), never destroy(): the window keeps its handle, so Restart
        Manager can still close the app during an in-app update instead of
        waiting out its file-lock timeout and reporting files in use.
        """
        if self.tray is not None and self.settings.close_to_tray:
            self._hide_to_tray()
            return
        self._quit()

    def _quit(self) -> None:
        """Really go. The only path that ends the process."""
        if self._closing:
            return
        # Catches preference changes (window size, a toggle, ...) even if the
        # user never started a download this session.
        self._persist_settings()
        self._flush_queue()
        # Not a join - that could block the UI for minutes. This just stops the
        # worker starting another ffmpeg as the interpreter tears down; an
        # orphaned one holds a .part open and breaks the next run.
        self.cancel.set()
        self._choice_ready.set()   # release a worker waiting on the picker
        self._thumbs.close()
        if self.tray is not None:
            # Before destroy(), or the icon is left behind in the notification
            # area with no process behind it.
            self.tray.remove()
            self.tray = None
        self._closing = True
        self.root.destroy()

    # ------------------------------------------------------------------ #
    #  FFmpeg guidance (if the one external dependency is missing)
    # ------------------------------------------------------------------ #
    def _offer_ffmpeg(self) -> None:
        """Guides the user when ffmpeg is missing: offers to install via winget if
        present, otherwise shows guidance and opens the download page."""
        if shutil.which("winget"):
            if messagebox.askyesno(
                "FFmpeg required",
                core.FFMPEG_HELP + "\n\nInstall FFmpeg now via winget?",
            ):
                self._install_ffmpeg()
            return
        messagebox.showwarning("FFmpeg required", core.FFMPEG_HELP)
        webbrowser.open(core.FFMPEG_DOWNLOAD_URL)

    def _install_ffmpeg(self) -> None:
        """Installs ffmpeg via winget in the background (the window doesn't freeze)."""
        if self.worker and self.worker.is_alive():
            return
        self._set_busy(True)
        self.stop_btn.configure(state="disabled")   # winget install can't be cancelled
        self.status.configure(text="Installing FFmpeg…")
        self.worker = threading.Thread(target=self._winget_ffmpeg, daemon=True)
        self.worker.start()

    def _winget_ffmpeg(self) -> None:
        self.q.put(("log", "Installing FFmpeg (winget) - this can take a few minutes…"))
        try:
            subprocess.run(
                ["winget", "install", "--id", "Gyan.FFmpeg", "-e",
                 "--accept-package-agreements", "--accept-source-agreements"],
                check=False, **core.hidden_process_kwargs(),
            )
            if core.has_ffmpeg():
                self.q.put(("done", "✓ FFmpeg installed. You can click Download now."))
            else:
                self.q.put(("done", "FFmpeg installed - restart the app for the change "
                                    "to take effect."))
        except Exception as e:
            self.q.put(("error", f"Failed to install FFmpeg: {e}  ·  {core.FFMPEG_DOWNLOAD_URL}"))
        finally:
            self.q.put(("finish", None))

    # ------------------------------------------------------------------ #
    #  Self-update (packaged build only)
    #
    #  Two-phase, because the background thread must never call messagebox:
    #  worker checks -> main thread asks -> worker downloads -> main thread
    #  confirms and hands off to the installer.
    # ------------------------------------------------------------------ #
    def _check_updates(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not core.is_frozen():
            # Never touch a source checkout: no download, no git, no file writes.
            if messagebox.askyesno(
                "Running from source",
                "You are running Blerp Downloader from source, so there is nothing "
                "to update automatically.\n\nUpdate with:\n    git pull\n\n"
                "The in-app updater only applies to the packaged Windows build.\n\n"
                "Open the Releases page?",
            ):
                webbrowser.open(core.RELEASES_PAGE_URL)
            return

        self._set_busy(True)
        self.stop_btn.configure(state="disabled")   # the check is quick
        self.status.configure(text="Checking for updates…")
        self.worker = threading.Thread(target=self._update_check_worker, daemon=True)
        self.worker.start()

    def _update_check_worker(self) -> None:
        try:
            self.q.put(("update_result", core.check_for_update(core.__version__)))
        except Exception as e:
            self.q.put(("error", f"Update check failed: {e}"))
        finally:
            self.q.put(("finish", None))

    def _on_update_result(self, st) -> None:
        """Main thread: react to a finished update check."""
        self._log(st.message)
        self.status.configure(text=st.message)

        if st.state == core.UpdateState.AVAILABLE and st.info:
            notes = f"\n\nWhat's new:\n{st.info.notes}" if st.info.notes else ""
            if messagebox.askyesno(
                "Update available",
                f"Version {st.info.version} is available (you have {st.current}).\n\n"
                f"Download it now? ({st.info.asset_size / 1_048_576:.1f} MB){notes}",
            ):
                self._start_update_download(st.info)
        elif st.state in (core.UpdateState.RATE_LIMITED, core.UpdateState.ERROR,
                          core.UpdateState.UNCOMPARABLE):
            # Never a dead end: always offer the manual route.
            if messagebox.askyesno("Update check", f"{st.message}\n\nOpen the Releases page?"):
                webbrowser.open(core.RELEASES_PAGE_URL)

    def _start_update_download(self, info) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.cancel.clear()
        self._set_busy(True)
        self.prog.configure(value=0)
        self.worker = threading.Thread(target=self._update_download_worker,
                                       args=(info,), daemon=True)
        self.worker.start()

    def _update_download_worker(self, info) -> None:
        total_mb = info.asset_size / 1_048_576 if info.asset_size else 0

        def on_progress(got: int, total: int) -> None:
            self.q.put(("progress", int(got * 100 / total) if total else 0))
            self.q.put(("status", f"Downloading update… "
                                  f"{got / 1_048_576:.1f} / {total_mb:.1f} MB"))

        self.q.put(("total", 100))
        self.q.put(("log", f"Downloading {info.asset_name}…"))
        try:
            path = core.download_installer(info, current_version=core.__version__,
                                           on_progress=on_progress, cancel=self.cancel)
            self.q.put(("update_downloaded", (info, path)))
        except core.BlerpError as e:
            self.q.put(("error", str(e)))
        except Exception as e:
            self.q.put(("error", f"Unexpected error while downloading the update: {e}"))
        finally:
            self.q.put(("finish", None))

    def _on_update_downloaded(self, info, path: Path) -> None:
        """Main thread: confirm, then hand off to the installer and exit."""
        self._log(f"✓ Downloaded → {path}")
        if not messagebox.askokcancel(
            "Ready to install",
            f"Blerp Downloader will now close and version {info.version} will be installed.\n\n"
            "The app reopens automatically when it is done.",
        ):
            self._log(f"Installer saved to {path} - you can run it later.")
            return

        # Save settings BEFORE launching so the INI write can't race process teardown.
        self._persist_settings()
        # An ffmpeg still running in our process tree fights Restart Manager and
        # can make the installer roll the whole update back.
        self.cancel.set()
        try:
            core.launch_installer(path)
        except Exception as e:
            # Antivirus can quarantine the file between download and launch.
            self._log(f"✗ Could not start the installer: {e}")
            messagebox.showerror(
                "Could not start the installer",
                f"{e}\n\nYour antivirus may have removed it. You can run it manually:\n{path}\n\n"
                f"Or download it from:\n{core.RELEASES_PAGE_URL}",
            )
            return
        self._quit()

    # ------------------------------------------------------------------ #
    #  Maintenance (main thread; disabled while a download is running)
    # ------------------------------------------------------------------ #
    def _clear_cache(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        usage = core.cache_usage()
        lines = [f"Cached right now: {usage.summary()}", "",
                 "This will delete:",
                 f"  • downloaded update installers ({core.human_size(usage.updates_bytes)})",
                 f"  • leftover temporary files ({core.human_size(usage.temp_bytes)})",
                 f"  • cached blerp images ({core.human_size(usage.thumbs_bytes)})"]
        for job in core.saved_jobs():
            # Never silent: losing this means the next bulk run re-scans the
            # whole profile.
            lines.append(f"  • the unfinished download for {job.username} "
                         f"({len(job.bites)} blerps)")
        lines.append("\nYour download list, your settings and your MP4s are not touched.")
        lines.append("\nAlso remove half-written .part files from your output folder?")

        include = messagebox.askyesnocancel("Clear cache", "\n".join(lines))
        if include is None:
            return
        result = core.clear_cache(include_outputs=bool(include),
                                 output_dirs_=core.output_dirs(self.settings.output_dir))
        summary = (f"Freed {result.freed_mb:.1f} MB"
                   + (" — " + ", ".join(result.details) if result.details else ""))
        if result.in_use:
            summary += f" ({result.in_use} item(s) in use, left alone)"
        self._log("🧹 " + summary)
        self.status.configure(text=summary)
        # The Options window is what shows the running total, and it is also
        # where this button lives - so it has to re-measure straight away.
        if self._options is not None and self._options.winfo_exists():
            self._options.refresh_usage()

    def _reset_settings(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not messagebox.askyesno(
            "Reset settings",
            "Restore every setting to its default?\n\n"
            "Output folder, FFmpeg folder, limit, overwrite and the clipboard "
            "options will be cleared. The window size and your downloaded files "
            "are left alone."):
            return

        defaults = core.reset_settings()
        # The window keeps the size the user chose - it isn't a preference they
        # set deliberately, and shrinking the window under them reads as a bug.
        keep_w, keep_h = self.settings.window_width, self.settings.window_height
        self.settings = core.Settings(**{**vars(defaults),
                                         "window_width": keep_w, "window_height": keep_h})
        self._refresh_from_settings()
        self._persist_settings()
        self._log("↺ Settings restored to defaults.")
        self.status.configure(text="Settings restored to defaults.")

    # ------------------------------------------------------------------ #
    #  Start / background work (never touches GUI widgets - queue only)
    # ------------------------------------------------------------------ #
    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        # An unsent paste is almost always meant to be part of this run.
        if self.target.get().strip():
            self._add_target()

        pending = [i for i in self.items if i.is_pending]
        if not pending:
            self._log("⚠ Nothing waiting. Add a URL or username to the list first.")
            return
        if not core.has_ffmpeg():        # ffmpeg is required to produce video
            self._offer_ffmpeg()
            return

        # Also saved on window close, so a plain preference change like a resize
        # is captured even without a run.
        self._persist_settings()

        # Everything the worker needs, decided here on the main thread: it must
        # never open a dialog, read a widget, or touch the list itself.
        jobs = [(item.item_id, self._snapshot(item)) for item in pending]

        self.cancel.clear()
        self._set_busy(True)
        self.prog.configure(maximum=len(jobs), value=0)
        self.worker = threading.Thread(target=self._run_queue, args=(jobs,),
                                       daemon=True)
        self.worker.start()

    def _snapshot(self, item) -> dict:
        """An immutable copy of one row, plus what only the main thread knows.

        The worker gets this rather than the row itself, so removing a row
        mid-run cannot pull the ground out from under it.
        """
        return {
            "kind": item.kind,
            "url": item.url,
            "username": item.username,
            "out_dir": self.settings.output_dir,
            "limit": self.settings.bulk_limit,
            "overwrite": self.settings.overwrite,
            "delay": self.settings.bulk_delay,
            "saved": self._resumable_job(item, self.settings.overwrite),
        }

    def _resumable_job(self, item, overwrite: bool):
        """The saved listing for a profile row, if it can be carried on."""
        if item.kind != "profile" or overwrite:
            # With overwrite on, what's already downloaded is deliberately
            # ignored, so there is nothing to work out how far a previous run
            # got - it would simply start again from the first blerp.
            return None
        job = core.load_job(item.username)
        if job and job.matches(item.username) and job.is_usable():
            return job
        return None

    # ------------------------------------------------------------------ #
    #  Background work (never touches GUI widgets or the list - queue only)
    # ------------------------------------------------------------------ #
    def _run_queue(self, jobs) -> None:
        """Walks the list, one row at a time, on one worker thread."""
        finished = 0
        try:
            for item_id, job in jobs:
                if self.cancel.is_set():
                    self.q.put(("item_status", (item_id, qs.QUEUED, {})))
                    continue
                self._run_one(item_id, job)
                finished += 1
                self.q.put(("progress", finished))
        except Exception as e:   # nothing here may take the app down
            self.q.put(("error", f"Unexpected error: {e}"))
        finally:
            self.q.put(("finish", None))

    def _run_one(self, item_id: str, job: dict) -> None:
        """One row, with every failure recorded against that row."""
        try:
            if job["kind"] == "profile":
                self._run_bulk(item_id, job)
            else:
                self._run_single(item_id, job)
        except core.BlerpError as e:
            self.q.put(("item_status", (item_id, qs.FAILED, {"error": str(e)})))
            self.q.put(("log", f"✗ {e}"))
        except Exception as e:
            self.q.put(("item_status", (item_id, qs.FAILED,
                                        {"error": f"Unexpected error: {e}"})))
            self.q.put(("log", f"✗ Unexpected error: {e}"))

    def _run_single(self, item_id: str, job: dict) -> None:
        url = job["url"]
        self.q.put(("item_status", (item_id, qs.RESOLVING, {})))
        self.q.put(("log", f"Scraping page: {url}"))
        media = core.fetch_bite_media(url)
        self.q.put(("item_status", (item_id, qs.DOWNLOADING,
                                    {"title": media.title, "bite_id": media.bite_id})))

        out_path = self._single_out(job["out_dir"], media.title)
        if out_path.exists() and not job["overwrite"]:
            self.q.put(("item_status", (item_id, qs.SKIPPED,
                                        {"out_path": str(out_path.resolve())})))
            self.q.put(("log", f"- already saved: {out_path.name}"))
            return

        def on_step(index, total, label):
            self.q.put(("item_progress", (item_id, index, total, label)))

        core.process_bite(media, out_path, on_step=on_step)
        self.q.put(("item_status", (item_id, qs.DONE,
                                    {"out_path": str(out_path.resolve())})))
        self.q.put(("log", f"✓ {out_path.resolve()}"))

    def _run_bulk(self, item_id: str, job: dict) -> None:
        username = job["username"]
        out_text, limit = job["out_dir"], job["limit"]
        overwrite = job["overwrite"]
        out_dir = Path(out_text) if out_text else Path(core.sanitize(username))
        out_dir.mkdir(parents=True, exist_ok=True)   # before any job is recorded

        self.q.put(("item_status", (item_id, qs.RESOLVING, {})))
        scanned = self._scan_profile(username, out_dir, limit, overwrite, job["saved"])
        if scanned is None:
            self.q.put(("item_status", (item_id, qs.QUEUED, {})))
            return
        bites, dropped = scanned

        bites = self._choose_bites(username, bites, out_dir, job)
        if bites is None:
            self.q.put(("log", "Cancelled — nothing was downloaded."))
            self.q.put(("item_status", (item_id, qs.STOPPED, {})))
            return

        if limit:
            bites = bites[:limit]
        total = len(bites)
        self.q.put(("item_status", (item_id, qs.DOWNLOADING,
                                    {"total_count": total, "done_count": 0})))
        self.q.put(("log", f"{total} blerps found → {out_dir}"))
        if dropped:
            # Otherwise the run reports a smaller profile than the website shows,
            # with nothing to explain the difference.
            self.q.put(("log", f"  ({dropped} skipped: no audio or image on the server)"))

        ok, skip, fail, completed = self._download_all(item_id, bites, out_dir, total,
                                                       overwrite, job["delay"])
        if completed:
            # Reached the end under its own steam. Keyed on that rather than
            # "nothing left", because a blerp that always fails writes no file
            # and would otherwise keep the job alive forever.
            core.clear_job(username)
        summary = f"{ok} downloaded, {skip} skipped, {fail} failed"
        self.q.put(("item_status", (item_id, qs.DONE if completed else qs.STOPPED,
                                    {"out_path": str(out_dir.resolve()),
                                     "error": "" if completed else summary})))
        self.q.put(("done", f"{username}: {summary} → {out_dir.resolve()}"))

    def _choose_bites(self, username: str, bites, out_dir: Path, job):
        """Worker thread: asks which blerps to take, and waits for the answer.

        Committing to three thousand downloads without seeing them is not a
        decision anyone makes on purpose, so the run parks here until the main
        thread has shown the list and the user has said. Returns None if they
        cancelled.

        A profile that was already picked over keeps that choice - the saved
        listing records it - so stopping and restarting doesn't ask again.
        """
        if not self.settings.pick_blerps:
            return bites
        saved = job.get("saved")
        if saved is not None and saved.selected:
            chosen = [m for m in bites if m.bite_id in set(saved.selected)]
            self.q.put(("log", f"Carrying on with the {len(chosen)} blerps you "
                               f"picked before."))
            return chosen or bites

        have = {m.bite_id for m in bites
                if core.bulk_out_path(out_dir, m).exists()}
        self._choice_ready.clear()
        self._choice = None
        self.q.put(("choose_bites", (username, bites, have)))
        # Interruptible: Stop and closing the window both release this rather
        # than leaving the thread parked for the life of the process.
        while not self._choice_ready.wait(0.2):
            if self.cancel.is_set() or self._closing:
                return None
        chosen = self._choice
        if chosen is None:
            return None

        picked = {m.bite_id for m in chosen}
        core.save_job(core.Job(
            username=username, bites=list(bites), dropped=0, limit=job["limit"],
            overwrite=job["overwrite"], out_dir=str(out_dir.resolve()),
            selected=sorted(picked), scan_complete=True, created_at=time.time(),
            app_version=core.__version__))
        return chosen

    def _on_choose_bites(self, username: str, bites, have) -> None:
        """Main thread: show the picker, then release the parked worker."""
        # Set before the window exists: the picker asks for its first screenful
        # of images from inside its own constructor, and at that moment the
        # worker is parked here rather than using the connection.
        self._picking = True
        try:
            self._picker = BitePicker(
                self.root, username, bites, already_have=have,
                icon_path=self.icon_path, palette=self.palette,
                images=self._thumbs if self.settings.thumbnails else None,
                on_copy=self._note_clipboard)
            self.root.wait_window(self._picker)
            self._choice = self._picker.result
        except tk.TclError:
            self._choice = None      # the window went away mid-question
        finally:
            self._picker = None
            self._picking = False
            self._choice_ready.set()

    def _scan_profile(self, username: str, out_dir: Path, limit: int | None,
                      overwrite: bool, saved) -> tuple[list, int] | None:
        """The profile listing, reusing a saved scan when one fits.

        Returns None if the user stopped during the scan.
        """
        if saved is not None:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(saved.created_at))
            self.q.put(("log", f"Carrying on where you left off — reusing the listing "
                               f"of {len(saved.bites)} blerps scanned {when}."))
            if not saved.scan_complete:
                self.q.put(("log", "  (that scan was interrupted, so it may not cover "
                                   "the whole profile)"))
            return saved.bites, saved.dropped

        self.q.put(("log", f"Scanning user: {username}…"))
        self.q.put(("status", "Scanning profile…"))

        # Scanning a large profile is many sequential requests; without this the
        # window shows one line and an empty bar for minutes and looks hung.
        def scanning(pages: int, found: int) -> None:
            self.q.put(("status", f"Scanning profile… {found} blerps so far "
                                  f"({pages} page{'s' if pages != 1 else ''})"))

        bites = core.list_user_bites(username, on_progress=scanning, cancel=self.cancel)
        dropped = getattr(bites, "dropped", 0)
        stopped = self.cancel.is_set()

        # Keep even a partial scan. Paging a large profile takes minutes, and
        # throwing that away because Stop was pressed is exactly what this
        # feature exists to prevent. Resuming the paging itself isn't possible -
        # there is no cursor, and the ordering shifts between requests.
        if bites:
            core.save_job(core.Job(
                username=username, bites=list(bites), dropped=dropped, limit=limit,
                overwrite=overwrite, out_dir=str(out_dir.resolve()),
                scan_complete=not stopped, created_at=time.time(),
                app_version=core.__version__))

        if stopped:
            kept = (f" The {len(bites)} blerps found so far were saved — press Download "
                    "to continue with them." if bites else "")
            self.q.put(("done", f"⏹ Stopped while scanning.{kept}"))
            return None
        return bites, dropped

    def _download_all(self, item_id: str, bites, out_dir: Path, total: int,
                      overwrite: bool, delay: float) -> tuple[int, int, int, bool]:
        """The download loop. Returns (ok, skipped, failed, ran_to_completion)."""
        ok = skip = fail = streak = 0
        for i, m in enumerate(bites, 1):
            if self.cancel.is_set():
                self.q.put(("log", "⏹ Stopped. Press Start to carry on from here."))
                return ok, skip, fail, False
            out_path = core.bulk_out_path(out_dir, m)
            self.q.put(("item_status", (item_id, qs.DOWNLOADING,
                                        {"done_count": i - 1, "total_count": total,
                                         "title": m.title})))
            self.q.put(("status", f"[{i}/{total}] {m.title[:45]}"))
            if out_path.exists() and not overwrite:
                skip += 1
                self.q.put(("log", f"[{i}/{total}] - skipped: {out_path.name}"))
                continue
            try:
                core.process_bite(m, out_path)
                ok += 1
                streak = 0
                self.q.put(("log", f"[{i}/{total}] ✓ {out_path.name}"))
            except Exception as e:
                fail += 1
                streak += 1
                self.q.put(("log", f"[{i}/{total}] ✗ ERROR: {e}"))
                if streak >= core.FAILURE_STREAK_LIMIT:
                    # Every remaining blerp would fail the same way, each burning
                    # its own network timeout.
                    self.q.put(("log",
                                f"⏹ {core.FAILURE_STREAK_LIMIT} in a row failed — stopping. "
                                "Check your connection, or clear the cache to re-scan "
                                "the profile."))
                    return ok, skip, fail, False
            time.sleep(delay)
        self.q.put(("item_status", (item_id, qs.DOWNLOADING,
                                    {"done_count": total, "total_count": total})))
        return ok, skip, fail, True

    def _single_out(self, out_text: str, title: str) -> Path:
        """Single-mode output: an explicit .mp4 path, a folder to put it in, or cwd if empty."""
        if out_text:
            p = Path(out_text)
            return p if p.suffix.lower() == ".mp4" else p / f"{core.sanitize(title)}.mp4"
        return Path(f"{core.sanitize(title)}.mp4")

    # ------------------------------------------------------------------ #
    #  Clipboard watch (main thread only - safe to touch Tkinter/dialogs here)
    # ------------------------------------------------------------------ #
    def _check_clipboard(self) -> None:
        if not self.settings.clipboard_watch:
            return
        # No longer skipped while a download runs: a caught link now joins the
        # list rather than starting immediately, so there is nothing to interrupt.
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return  # clipboard empty or holds non-text content (e.g. an image)
        if text == self._last_clipboard:
            return
        self._last_clipboard = text

        url = looks_like_blerp_soundbite_url(text)
        if not url:
            return
        if self.settings.clipboard_mode == "auto":
            self._log(f"📋 Clipboard: Blerp link detected, downloading.\n    {url}")
            self._add_target(url, start=True)
        else:
            self._offer_link(url)

    def _offer_link(self, url: str) -> None:
        """Asks about a caught link without blocking the main loop.

        A messagebox here would stall _poll, which is what drives the clipboard
        watch and every list update - the app would look hung until answered.
        """
        if not self.settings.notify_card or card.quiet_hours():
            # Falling back rather than dropping it: the user turned the watch on,
            # so silently ignoring a catch would look like the feature is broken.
            if messagebox.askyesno("Blerp link detected",
                                   f"Add this blerp to the list?\n\n{url}"):
                self._add_target(url)
            return
        if self._card is None or not self._card.winfo_exists():
            self._card = LinkCard(self, self.palette)
        bite_id = core.OBJECTID_RE.search(url)
        image = (self._thumbs.image_for(bite_id.group(0)) if bite_id else None)
        # One card, always: a newer catch replaces what is in it and restarts the
        # timer, rather than stacking windows in the corner.
        self._card.show(url, image=image)

    # ------------------------------------------------------------------ #
    #  Main thread: drain the queue, update the UI
    # ------------------------------------------------------------------ #
    def _log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _handle(self, kind: str, val) -> None:
        """Applies one queued message from a worker thread to the UI."""
        if kind == "log":
            self._log(val)
        elif kind == "total":
            self.prog.configure(maximum=max(val, 1))
        elif kind == "progress":
            self.prog.configure(value=val)
        elif kind == "status":
            self.status.configure(text=val)
        elif kind == "done":
            self._log(val)
            self.status.configure(text=val)
        elif kind == "error":
            self._log(f"✗ ERROR: {val}")
            self.status.configure(text="An error occurred.")
        elif kind == "finish":
            self._on_run_finished()
        elif kind == "item_status":
            self._on_item_status(*val)
        elif kind == "item_progress":
            self._on_item_progress(*val)
        elif kind == "thumb":
            self._apply_thumb(val)
        elif kind == "tray":
            self._handle_tray(*val)
        elif kind == "choose_bites":
            self._on_choose_bites(*val)
        elif kind == "update_result":
            self._on_update_result(val)
        elif kind == "update_downloaded":
            self._on_update_downloaded(*val)

    def _on_item_status(self, item_id: str, status: str, extra: dict) -> None:
        """Main thread: applies a worker's report to the row it belongs to."""
        item = self._by_id.get(item_id)
        if item is None:
            return   # the user removed the row while it was being worked on
        item.status = status
        for name, value in extra.items():
            setattr(item, name, value)
        if status == qs.DOWNLOADING and self._active_id != item_id:
            self._announce(item)
        self._active_id = item_id if status in (qs.RESOLVING, qs.DOWNLOADING) else None
        self.view.update_item(item)
        self.view.see(item_id)
        self._update_list_buttons()
        # A status change is worth persisting; the counts inside one are not.
        if "done_count" not in extra or status != qs.DOWNLOADING:
            self._touch_queue()
        # On every transition, not just the ones that start work: the image is
        # written part-way through the download, so asking only at the start
        # meant it never appeared until the next launch.
        self._request_thumb(item)

    def _on_item_progress(self, item_id: str, step: int, steps: int,
                          label: str) -> None:
        item = self._by_id.get(item_id)
        if item is None:
            return
        self.view.update_item(item, step=step, steps=steps, detail=label)
        if step == 1:
            # The media has just been downloaded, which is where the blerp's
            # image gets cached - so this is the first moment both the row and
            # the notification can actually show it.
            self._request_thumb(item)
            self._announce(item)

    def _on_run_finished(self) -> None:
        self._set_busy(False)
        self._announce_finished(
            sum(1 for i in self.items if i.status == qs.DONE),
            sum(1 for i in self.items if i.status == qs.FAILED))
        self._announced.clear()
        self._active_id = None
        self._update_list_buttons()
        self._flush_queue()

    def _retheme(self, mode: str) -> None:
        """Switches the whole window to `mode`. Safe to call with no change."""
        if self._plain or mode == self._mode:
            return
        self._mode = mode
        self.palette = theming.apply_theme(self.root, mode)
        self._paint_log()   # the classic Text isn't covered by ttk styles
        theming.set_titlebar_theme(self.root, mode == "dark")

    def _follow_system_theme(self) -> None:
        """Re-applies the theme if Windows switched between light and dark.

        Tk can't see WM_SETTINGCHANGE, so this piggybacks on the existing poll
        rather than adding a second timer. Only meaningful in "auto" mode.
        """
        if self._plain or self.settings.theme != "auto":
            return
        self._tick += 1
        if self._tick % _THEME_CHECK_TICKS:
            return
        self._retheme(theming.detect_windows_theme())

    def _poll(self) -> None:
        if self._closing:
            return
        self._follow_system_theme()
        self._check_clipboard()
        try:
            while not self._closing:   # _handle may hand off to the installer
                kind, val = self.q.get_nowait()
                self._handle(kind, val)
        except queue.Empty:
            pass
        # Rate-limited rather than written on every change: a bulk run reports a
        # status per blerp, and rewriting the file thousands of times would buy
        # nothing - the percentages inside a row are not persisted anyway.
        if (self._queue_dirty
                and time.monotonic() - self._last_queue_write >= _QUEUE_WRITE_INTERVAL):
            self._flush_queue()
        if not self._closing:
            self.root.after(100, self._poll)


def main() -> None:
    # Closing to the tray makes "click the shortcut again" ordinary rather than
    # rare, and two copies writing the download list would clobber each other
    # with no error at all. The handle is deliberately never released: it lives
    # as long as the process, which is exactly the lifetime being claimed.
    _handle, already_running = tray.single_instance_mutex()
    if already_running:
        tray.broadcast_show_window(core.APP_NAME)
        return

    root = tk.Tk()
    # Built while hidden, then shown: this both avoids a flash of the unstyled
    # default window and gives the toplevel its frame handle, which the title
    # bar call needs.
    root.withdraw()
    gui = BlerpGUI(root)
    root.update_idletasks()
    theming.set_titlebar_theme(root, gui._mode == "dark")
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()

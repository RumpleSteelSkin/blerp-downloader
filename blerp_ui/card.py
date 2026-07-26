"""The card that appears when a Blerp link is caught from the clipboard.

A borderless window of our own rather than a message box, for two reasons. It
can show the blerp's image at a size worth looking at, and - less obviously - a
messagebox blocks inside the main loop, which is the same loop that drives the
clipboard watch and every list update, so the app freezes until it is answered.

Exactly one card exists at a time. A newer catch replaces what is in this one
and restarts its timer; stacking windows in the corner is churn nobody asked
for, and the list is where the links actually accumulate now.
"""

from __future__ import annotations

import ctypes
import os
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk

WIDTH = 340
MARGIN = 12
DISMISS_MS = 12_000


def work_area(root) -> tuple[int, int, int, int]:
    """The usable rectangle of the monitor the app is on.

    Screen bounds would put the card underneath the taskbar, and screen bounds
    of the *primary* monitor would put it on the wrong screen entirely.
    """
    fallback = (0, 0, root.winfo_screenwidth(), root.winfo_screenheight() - 48)
    if os.name != "nt":
        return fallback
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        hwnd = int(root.wm_frame(), 16)
        monitor = user32.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)  # NEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            w = info.rcWork
            return w.left, w.top, w.right, w.bottom
    except Exception:
        pass
    try:
        rect = ctypes.wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0):        # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return fallback


def quiet_hours() -> bool:
    """Whether Windows says now is a bad moment to pop something up.

    A topmost window over a full-screen game or a presentation is how a feature
    gets switched off for good.
    """
    if os.name != "nt":
        return False
    try:
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(
                ctypes.byref(state)) != 0:
            return False
        # 2 = BUSY (full-screen app), 3 = RUNNING_D3D_FULL_SCREEN,
        # 4 = PRESENTATION_MODE, 5 = ACCEPTS_NOTIFICATIONS, 7 = QUIET_TIME
        return state.value in (2, 3, 4, 7)
    except (OSError, AttributeError):
        return False


class LinkCard(tk.Toplevel):
    """A small bottom-right prompt. Buttons only - never click-to-accept."""

    def __init__(self, parent, palette) -> None:
        super().__init__(parent.root)
        self.app = parent
        self.palette = palette
        self._timer = None
        self._url = ""

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(background=palette.border)

        frame = tk.Frame(self, background=palette.surface)
        frame.pack(fill="both", expand=True, padx=1, pady=1)

        head = tk.Frame(frame, background=palette.surface)
        head.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(head, text="Blerp link copied", background=palette.surface,
                 foreground=palette.muted, font=("Segoe UI", 8, "bold")).pack(side="left")
        close = tk.Label(head, text="✕", background=palette.surface,
                         foreground=palette.muted, cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.dismiss())

        body = tk.Frame(frame, background=palette.surface)
        body.pack(fill="x", padx=12, pady=(6, 0))
        self.thumb = tk.Label(body, background=palette.surface)
        self.thumb.pack(side="left", padx=(0, 10))
        self.title_lbl = tk.Label(body, background=palette.surface,
                                  foreground=palette.text, justify="left",
                                  wraplength=WIDTH - 130, anchor="w",
                                  font=("Segoe UI", 10))
        self.title_lbl.pack(side="left", fill="x", expand=True)

        buttons = tk.Frame(frame, background=palette.surface)
        buttons.pack(fill="x", padx=12, pady=10)
        ttk.Button(buttons, text="Ignore", command=self.dismiss).pack(side="right")
        ttk.Button(buttons, text="Add to list", style="Accent.TButton",
                   command=self._accept).pack(side="right", padx=(0, 8))

        # The timer pauses while the pointer is over the card, so reading it
        # doesn't race the countdown.
        self.bind("<Enter>", lambda _e: self._cancel_timer())
        self.bind("<Leave>", lambda _e: self._arm())

    # ------------------------------------------------------------------ #
    def show(self, url: str, *, title: str = "", image=None) -> None:
        self._url = url
        self.title_lbl.configure(text=title or _elide(url))
        self.thumb.configure(image=image or "")
        self.thumb.image = image        # Tk keeps only the name; hold the object
        self._place()
        self.deiconify()
        self.lift()
        self._arm()

    def _place(self) -> None:
        self.update_idletasks()
        left, top, right, bottom = work_area(self.app.root)
        w = max(WIDTH, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        x = max(left, right - w - MARGIN)
        y = max(top, bottom - h - MARGIN)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _arm(self) -> None:
        self._cancel_timer()
        try:
            self._timer = self.after(DISMISS_MS, self.dismiss)
        except tk.TclError:
            self._timer = None

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            try:
                self.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None

    def _accept(self) -> None:
        url = self._url
        self.dismiss()
        if url:
            self.app._add_target(url)

    def dismiss(self) -> None:
        self._cancel_timer()
        try:
            if self.winfo_exists():
                self.withdraw()
        except tk.TclError:
            pass

    def destroy(self) -> None:
        self._cancel_timer()
        super().destroy()


def _elide(text: str, limit: int = 60) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"

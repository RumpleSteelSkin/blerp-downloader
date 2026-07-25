"""Light/dark theming for the Tkinter GUI - standard library only.

ttk's stock Windows themes can't be recoloured, so this switches to `clam`
(the one built-in theme whose elements accept colour options) and restyles it.
Anything that ttk refuses to colour is noted at the call site below.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from tkinter import ttk

MODES = ("auto", "dark", "light")


@dataclass(frozen=True)
class Palette:
    bg: str            # window background
    surface: str       # cards / raised areas
    text: str
    muted: str         # section headings, hints, signature
    disabled_fg: str
    border: str
    accent: str        # primary button, selection, focus
    accent_hover: str
    accent_fg: str     # text on top of accent
    entry_bg: str
    select_bg: str
    select_fg: str
    log_bg: str


DARK = Palette(
    bg="#1b1b1f", surface="#26262c", text="#e8e8ec", muted="#9a9aa6",
    disabled_fg="#5c5c68", border="#3a3a44",
    accent="#5b7cfa", accent_hover="#6d8bff", accent_fg="#ffffff",
    entry_bg="#16161a", select_bg="#3d5bd6", select_fg="#ffffff",
    log_bg="#131317",
)

LIGHT = Palette(
    bg="#f5f5f7", surface="#ffffff", text="#1c1c22", muted="#6b6b76",
    disabled_fg="#adadb8", border="#d8d8e0",
    accent="#4a63d8", accent_hover="#3d55c4", accent_fg="#ffffff",
    entry_bg="#ffffff", select_bg="#4a63d8", select_fg="#ffffff",
    log_bg="#fbfbfd",
)


def detect_windows_theme() -> str:
    """'dark' or 'light' from the Windows personalisation setting.

    Defaults to light, matching what Windows itself does when the value is
    absent (Server Core, LTSC, older builds).
    """
    if os.name != "nt":
        return "light"
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        with key:
            # AppsUseLightTheme, not SystemUsesLightTheme: the latter drives
            # the taskbar and Start menu rather than application windows.
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if value else "dark"
    except (ImportError, OSError, ValueError):
        return "light"


def high_contrast_active() -> bool:
    """Whether Windows high contrast is on.

    When it is, the custom palette is skipped entirely: overriding the system
    colours is exactly what the user turned high contrast on to prevent.
    """
    if os.name != "nt":
        return False

    class HIGHCONTRAST(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT),
                    ("dwFlags", wintypes.DWORD),
                    ("lpszDefaultScheme", wintypes.LPWSTR)]

    try:
        hc = HIGHCONTRAST()
        hc.cbSize = ctypes.sizeof(HIGHCONTRAST)
        if not ctypes.windll.user32.SystemParametersInfoW(
                0x0042, ctypes.sizeof(HIGHCONTRAST), ctypes.byref(hc), 0):
            return False
        return bool(hc.dwFlags & 0x1)
    except (OSError, AttributeError):
        return False


def resolve_mode(setting: str) -> str:
    """Turns the stored setting ('auto'/'dark'/'light') into 'dark' or 'light'."""
    if setting == "dark":
        return "dark"
    if setting == "light":
        return "light"
    return detect_windows_theme()


def palette_for(mode: str) -> Palette:
    return DARK if mode == "dark" else LIGHT


def apply_theme(root, mode: str) -> Palette:
    """Restyles every ttk widget class for the given mode and returns the palette.

    Safe to call again when the theme changes at runtime.
    """
    p = palette_for(mode)
    style = ttk.Style(root)
    if style.theme_use() != "clam":
        style.theme_use("clam")   # must come first: configure() is per-theme

    root.configure(background=p.bg)

    # clam ships its own `map` entries on the root style; a plain configure()
    # leaves them in place and every widget then flashes clam's light grey on
    # hover and when disabled. Overriding the maps is what actually removes it.
    style.configure(".", background=p.bg, foreground=p.text,
                    fieldbackground=p.entry_bg, bordercolor=p.border,
                    lightcolor=p.bg, darkcolor=p.bg,
                    focuscolor=p.accent, troughcolor=p.surface,
                    selectbackground=p.select_bg, selectforeground=p.select_fg)
    style.map(".",
              background=[("disabled", p.bg), ("active", p.bg)],
              foreground=[("disabled", p.disabled_fg)],
              selectbackground=[("!focus", p.select_bg)],
              selectforeground=[("!focus", p.select_fg)])

    style.configure("TFrame", background=p.bg)
    style.configure("Card.TFrame", background=p.surface,
                    relief="solid", borderwidth=1, bordercolor=p.border)

    style.configure("TLabel", background=p.bg, foreground=p.text)
    style.configure("Header.TLabel", background=p.bg, foreground=p.text,
                    font=("Segoe UI Semibold", 14))
    style.configure("Section.TLabel", background=p.bg, foreground=p.muted,
                    font=("Segoe UI", 8, "bold"))
    style.configure("Muted.TLabel", background=p.bg, foreground=p.muted)
    style.configure("Status.TLabel", background=p.bg, foreground=p.muted)

    # Entry colours live on `fieldbackground`; `background` is not an option on
    # clam's field element. `insertcolor` is read from the style too, and
    # without it the caret stays black and vanishes on a dark field.
    style.configure("TEntry", fieldbackground=p.entry_bg, foreground=p.text,
                    insertcolor=p.text, bordercolor=p.border,
                    lightcolor=p.border, darkcolor=p.border,
                    padding=5)
    style.map("TEntry",
              bordercolor=[("focus", p.accent)],
              lightcolor=[("focus", p.accent)],
              darkcolor=[("focus", p.accent)],
              fieldbackground=[("disabled", p.bg)],
              foreground=[("disabled", p.disabled_fg)])

    style.configure("TButton", background=p.surface, foreground=p.text,
                    bordercolor=p.border, lightcolor=p.surface,
                    darkcolor=p.surface, focusthickness=1, focuscolor=p.accent,
                    relief="solid", borderwidth=1, padding=(14, 7))
    style.map("TButton",
              background=[("disabled", p.bg), ("pressed", p.border),
                          ("active", p.border)],
              foreground=[("disabled", p.disabled_fg)],
              bordercolor=[("focus", p.accent), ("active", p.accent)],
              lightcolor=[("pressed", p.border)],
              darkcolor=[("pressed", p.border)])

    # The three buttons are enabled/disabled constantly while work runs, so the
    # disabled state has to look obviously different or a busy Download button
    # is indistinguishable from an idle one.
    style.configure("Accent.TButton", background=p.accent, foreground=p.accent_fg,
                    bordercolor=p.accent, lightcolor=p.accent, darkcolor=p.accent)
    style.map("Accent.TButton",
              background=[("disabled", p.border), ("pressed", p.accent_hover),
                          ("active", p.accent_hover)],
              foreground=[("disabled", p.disabled_fg)],
              bordercolor=[("disabled", p.border), ("active", p.accent_hover)],
              lightcolor=[("disabled", p.border), ("active", p.accent_hover)],
              darkcolor=[("disabled", p.border), ("active", p.accent_hover)])

    # clam has no `selected` entry for the indicator, so a ticked box would keep
    # the unchecked background. The accent entry has to come first: ttk applies
    # the first spec that matches.
    style.configure("TCheckbutton", background=p.bg, foreground=p.text,
                    indicatorbackground=p.entry_bg, indicatorforeground=p.accent_fg,
                    bordercolor=p.border, upperbordercolor=p.border,
                    lowerbordercolor=p.border, focusthickness=1,
                    focuscolor=p.accent, padding=3)
    style.map("TCheckbutton",
              indicatorbackground=[("selected", "!disabled", p.accent),
                                   ("disabled", p.bg),
                                   ("pressed", p.border)],
              indicatorforeground=[("selected", p.accent_fg)],
              background=[("active", p.bg), ("disabled", p.bg)],
              foreground=[("disabled", p.disabled_fg)],
              upperbordercolor=[("selected", p.accent), ("focus", p.accent)],
              lowerbordercolor=[("selected", p.accent), ("focus", p.accent)])

    # `thickness` is silently ignored on clam; `arrowsize` is the real lever
    # (rendered height ends up arrowsize + 4).
    style.configure("TProgressbar", background=p.accent, troughcolor=p.surface,
                    bordercolor=p.border, lightcolor=p.accent,
                    darkcolor=p.accent, arrowsize=6)

    style.configure("TSeparator", background=p.border)

    style.configure("Vertical.TScrollbar", background=p.surface,
                    troughcolor=p.bg, bordercolor=p.bg,
                    arrowcolor=p.muted, relief="flat", borderwidth=0)
    style.map("Vertical.TScrollbar",
              background=[("active", p.border), ("disabled", p.bg)],
              arrowcolor=[("disabled", p.disabled_fg)])

    return p


def set_titlebar_theme(root, dark: bool) -> None:
    """Matches the window's title bar to the theme (Windows 10 1809+).

    Must run after the toplevel is realised. `winfo_id()` is the *content*
    child window and is rejected with E_HANDLE; the frame handle is the one
    DWM wants.
    """
    if os.name != "nt":
        return
    try:
        frame = root.wm_frame()
        hwnd = int(frame, 16)
        if hwnd == root.winfo_id():
            return  # wrapper not created yet; nothing to theme
    except (ValueError, AttributeError, Exception):
        return

    try:
        dwm = ctypes.windll.dwmapi
        # Explicit argtypes: without them the HWND is marshalled as 32-bit and
        # gets truncated for high handle values on x64.
        dwm.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long

        value = ctypes.c_int(1 if dark else 0)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE on current builds. Only fall back
        # to the pre-20H1 value if 20 is rejected: on Windows 11, 19 means
        # something else entirely and would report success.
        if dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20,
                                     ctypes.byref(value), ctypes.sizeof(value)) != 0:
            dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 19,
                                      ctypes.byref(value), ctypes.sizeof(value))

        # Windows 10 doesn't repaint an already-visible caption on its own.
        # SWP_FRAMECHANGED nudges it without stealing focus.
        ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
            0x0002 | 0x0001 | 0x0004 | 0x0020)  # NOMOVE|NOSIZE|NOZORDER|FRAMECHANGED
    except (OSError, AttributeError):
        pass

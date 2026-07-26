"""The notification-area icon, in ctypes.

No Tkinter here at all. The window this creates lives on the main thread, and
Tcl's Windows notifier dispatches to it while mainloop runs - which is why there
is no second message pump and therefore no handshake to get wrong at shutdown.
The classic tray bug, an icon left behind after the app exits, comes from
exactly that handshake.

The rule that makes it safe: the window procedure runs re-entrantly, from inside
Tcl's DispatchMessage, so it must never call into Tk. It only hands the event to
the callback, which is expected to do nothing but enqueue it - the same
discipline the download threads already follow.

Follows theme.py's conventions: ctypes.windll, structures declared where they
are used, argtypes only where marshalling would otherwise be wrong, hex
constants named in a comment, and every call swallowed so a cosmetic Win32
failure can never raise into the app.
"""

from __future__ import annotations

import atexit
import ctypes
import os
from ctypes import wintypes

WINDOW_CLASS = "BlerpDownloaderTrayWnd"
# Broadcast by a second launch and answered by the copy already running.
SHOW_WINDOW_MESSAGE = "BlerpDownloaderShowWindow"

# Events handed to the callback.
ACTIVATE = "activate"          # left click or double click
MENU = "menu"                  # a menu item was chosen; payload is its id
BALLOON_CLICK = "balloon"      # the notification itself was clicked
CLOSE = "close"                # Restart Manager or a shutdown wants us gone

# Menu item ids. Must be > 0: TrackPopupMenu returns 0 for "cancelled".
MENU_OPEN = 1
MENU_START = 2
MENU_STOP = 3
MENU_QUIT = 9

_WM_APP = 0x8000
_CALLBACK_MESSAGE = _WM_APP + 1

# Kept forever. RegisterClassW stores the trampoline as a raw function pointer,
# so if Python frees it the next dispatched message jumps into freed memory -
# the process dies with no traceback and nothing in the log.
_KEEPALIVE: list = []


def available() -> bool:
    return os.name == "nt"


class TrayIcon:
    """One notification-area icon. Create, install(), remove() when done."""

    def __init__(self, icon_path: str, tooltip: str, on_event) -> None:
        self.icon_path = icon_path
        self.tooltip = tooltip[:127]
        self.on_event = on_event
        self.hwnd = None
        self._show_request = 0
        self._hicon = None
        self._balloon_icons: dict = {}
        self._installed = False
        self._taskbar_created = 0
        self._wndproc = None
        self._menu_items: list = []

    # ------------------------------------------------------------------ #
    #  Setup
    # ------------------------------------------------------------------ #
    def install(self) -> bool:
        """Creates the window and adds the icon. False if it couldn't."""
        if not available() or self._installed:
            return self._installed
        try:
            self._register_class()
            self._create_window()
            self._hicon = _load_icon(self.icon_path, small=True)
            if self._add():
                self._installed = True
                atexit.register(self.remove)
        except (OSError, AttributeError, ValueError):
            self._installed = False
        return self._installed

    def _register_class(self) -> None:
        # Declared here rather than at module scope so importing this module
        # touches nothing - CI imports every module in the package.
        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT),
                        ("lpfnWndProc", ctypes.c_void_p),
                        ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON),
                        ("hCursor", ctypes.c_void_p),
                        ("hbrBackground", ctypes.c_void_p),
                        ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR)]

        # WPARAM/LPARAM from wintypes are already pointer-sized, so this
        # signature is right on x86 as well as x64.
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                       wintypes.UINT, wintypes.WPARAM,
                                       wintypes.LPARAM)
        self._wndproc = proc_type(self._handle)
        _KEEPALIVE.append(self._wndproc)

        # Explicit argtypes for the same reason theme.py sets them on
        # DwmSetWindowAttribute: without them ctypes marshals these as 32-bit
        # ints, and a real wParam/lParam overflows on x64. That raises inside
        # the callback, where there is no caller to catch it - every message
        # after the first is then dropped.
        user32 = ctypes.windll.user32
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]

        cls = WNDCLASSW()
        cls.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        cls.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        cls.lpszClassName = WINDOW_CLASS
        # 1410 = ERROR_CLASS_ALREADY_EXISTS, which a second app instance or a
        # reinstall of the icon reaches; the class is still usable.
        if not ctypes.windll.user32.RegisterClassW(ctypes.byref(cls)):
            if ctypes.windll.kernel32.GetLastError() != 1410:
                raise OSError("could not register the tray window class")

        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        # What a second copy of the app broadcasts instead of opening a window
        # of its own. Registered on both sides; the id is per-session and the
        # same string always resolves to the same number.
        self._show_request = user32.RegisterWindowMessageW(SHOW_WINDOW_MESSAGE)

    def _create_window(self) -> None:
        user32 = ctypes.windll.user32
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, ctypes.c_void_p, wintypes.HINSTANCE, ctypes.c_void_p]
        # A real top-level window, never shown - not an HWND_MESSAGE one. A
        # message-only window receives no broadcasts, which would silently lose
        # both TaskbarCreated and the session-end messages Restart Manager
        # relies on during an in-app update.
        self.hwnd = user32.CreateWindowExW(
            0x00000080,            # WS_EX_TOOLWINDOW: keeps it out of Alt-Tab
            WINDOW_CLASS, "Blerp Downloader",
            0x00000000,            # WS_OVERLAPPED
            0, 0, 0, 0, None, None,
            ctypes.windll.kernel32.GetModuleHandleW(None), None)
        if not self.hwnd:
            raise OSError("could not create the tray window")

    # ------------------------------------------------------------------ #
    #  The icon
    # ------------------------------------------------------------------ #
    def _data(self, flags: int):
        return _notify_data(self.hwnd, flags, callback=_CALLBACK_MESSAGE,
                            hicon=self._hicon, tip=self.tooltip)

    def _add(self) -> bool:
        # NIF_MESSAGE|NIF_ICON|NIF_TIP
        return bool(self._notify(0, self._data(0x01 | 0x02 | 0x04)))   # NIM_ADD

    def _notify(self, action: int, data) -> bool:
        try:
            shell32 = ctypes.windll.shell32
            shell32.Shell_NotifyIconW.restype = wintypes.BOOL
            return bool(shell32.Shell_NotifyIconW(action, ctypes.byref(data)))
        except (OSError, AttributeError):
            return False

    def set_tooltip(self, text: str) -> None:
        if not self._installed:
            return
        self.tooltip = (text or "")[:127]
        self._notify(1, self._data(0x04))          # NIM_MODIFY, NIF_TIP

    def notify(self, title: str, text: str, *, ico_path: str = "",
               key: str = "") -> bool:
        """Shows a Windows notification, with a blerp's own image if given.

        Redundant by design: Focus Assist, a per-app notification setting or the
        icon being in the overflow all suppress this while the call still
        reports success. The list row is the real status; this is a courtesy for
        when the window isn't on screen.
        """
        if not self._installed:
            return False
        # NIF_INFO
        data = self._data(0x10)
        data.szInfoTitle = (title or "")[:63]
        data.szInfo = (text or "")[:255]
        data.dwInfoFlags = 0x00000001              # NIIF_INFO
        if ico_path:
            hicon = _load_icon(ico_path, small=False)
            if hicon:
                self._retain_balloon_icon(key or ico_path, hicon)
                data.hBalloonIcon = hicon
                data.dwInfoFlags = 0x00000004 | 0x00000020   # NIIF_USER|LARGE_ICON
        return self._notify(1, data)               # NIM_MODIFY

    def _retain_balloon_icon(self, key: str, hicon) -> None:
        """Holds an icon until the notification that uses it is done with it.

        The shell reads it lazily and the balloon outlives the call that showed
        it, so destroying it straight away is a use-after-free across a process
        boundary.
        """
        old = self._balloon_icons.pop(key, None)
        if old:
            _destroy_icon(old)
        self._balloon_icons[key] = hicon
        while len(self._balloon_icons) > 8:
            _, stale = self._balloon_icons.popitem()
            _destroy_icon(stale)

    # ------------------------------------------------------------------ #
    #  Menu
    # ------------------------------------------------------------------ #
    def set_menu(self, items) -> None:
        """items is (id, label, enabled); an id of 0 means a separator."""
        self._menu_items = list(items)

    def _show_menu(self) -> None:
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            for item_id, label, enabled in self._menu_items:
                if not item_id:
                    user32.AppendMenuW(menu, 0x800, 0, None)      # MF_SEPARATOR
                    continue
                # MF_STRING, or MF_GRAYED|MF_DISABLED when unavailable
                flags = 0x0 if enabled else (0x1 | 0x2)
                user32.AppendMenuW(menu, flags, item_id, label)

            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            # Documented TrackPopupMenu dance: without the foreground call and
            # the WM_NULL afterwards the menu refuses to close when the user
            # clicks somewhere else.
            user32.SetForegroundWindow(self.hwnd)
            chosen = user32.TrackPopupMenu(
                menu, 0x0002 | 0x0100 | 0x0080,   # RIGHTBUTTON|RETURNCMD|NONOTIFY
                pt.x, pt.y, 0, self.hwnd, None)
            user32.PostMessageW(self.hwnd, 0x0000, 0, 0)          # WM_NULL
            if chosen:
                self._emit(MENU, int(chosen))
        finally:
            user32.DestroyMenu(menu)

    # ------------------------------------------------------------------ #
    #  Window procedure - main thread, re-entrant, must not touch Tk
    # ------------------------------------------------------------------ #
    def _handle(self, hwnd, msg, wparam, lparam):
        try:
            if msg == _CALLBACK_MESSAGE:
                self._on_icon_event(lparam & 0xFFFF)
            elif msg == self._taskbar_created and self._taskbar_created:
                # Explorer restarted and took every notification icon with it.
                self._add()
            elif msg == self._show_request and self._show_request:
                # Someone launched the app again. Come forward rather than
                # letting their click appear to do nothing.
                self._emit(ACTIVATE, "second-launch")
            elif msg == 0x0011:                    # WM_QUERYENDSESSION
                self._emit(CLOSE, "session")
                return 1                           # yes, we can be shut down
            elif msg in (0x0010, 0x0016):          # WM_CLOSE, WM_ENDSESSION
                self._emit(CLOSE, "close")
                return 0
        except Exception:
            pass   # a raise here would propagate into Tcl's dispatch loop
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_icon_event(self, event: int) -> None:
        if event in (0x0202, 0x0203):              # WM_LBUTTONUP, WM_LBUTTONDBLCLK
            self._emit(ACTIVATE, None)
        elif event in (0x0205, 0x007B):            # WM_RBUTTONUP, WM_CONTEXTMENU
            self._show_menu()
        elif event == 0x0405:                      # NIN_BALLOONUSERCLICK
            self._emit(BALLOON_CLICK, None)
        elif event in (0x0403, 0x0404):            # NIN_BALLOONHIDE, ...TIMEOUT
            self._release_balloon_icons()

    def _release_balloon_icons(self) -> None:
        for hicon in self._balloon_icons.values():
            _destroy_icon(hicon)
        self._balloon_icons.clear()

    def _emit(self, name: str, payload) -> None:
        try:
            self.on_event(name, payload)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Teardown
    # ------------------------------------------------------------------ #
    def remove(self) -> None:
        """Takes the icon away. Idempotent, and safe after Tk has gone."""
        if not self._installed:
            return
        self._installed = False
        try:
            self._notify(2, self._data(0))         # NIM_DELETE
            self._release_balloon_icons()
            if self._hicon:
                _destroy_icon(self._hicon)
                self._hicon = None
            if self.hwnd:
                ctypes.windll.user32.DestroyWindow(self.hwnd)
                self.hwnd = None
        except (OSError, AttributeError):
            pass


def _notify_data(hwnd, flags: int, *, callback: int = 0, hicon=None, tip: str = ""):
    """A NOTIFYICONDATAW sized for the current shell (976 bytes on x64)."""
    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT),
                    ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128),
                    ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256),
                    ("uVersion", wintypes.UINT),
                    ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD),
                    ("guidItem", ctypes.c_byte * 16),
                    ("hBalloonIcon", wintypes.HICON)]

    data = NOTIFYICONDATAW()
    data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    data.hWnd = hwnd
    data.uID = 1
    data.uFlags = flags
    data.uCallbackMessage = callback
    if hicon:
        data.hIcon = hicon
    data.szTip = tip[:127]
    return data


def _load_icon(path: str, *, small: bool):
    """An HICON from an .ico file, at the size the shell is asking for."""
    if not path or not available():
        return None
    try:
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        # SM_CXSMICON / SM_CXICON, so the right frame is chosen out of the
        # multi-size .ico rather than a 256px one being squashed down.
        metric = 49 if small else 11
        size = user32.GetSystemMetrics(metric)
        return user32.LoadImageW(None, str(path), 1,   # IMAGE_ICON
                                 size, size,
                                 0x00000010 | 0x00008000)  # LOADFROMFILE|SHARED
    except (OSError, AttributeError, ValueError):
        return None


def _destroy_icon(hicon) -> None:
    try:
        ctypes.windll.user32.DestroyIcon(hicon)
    except (OSError, AttributeError):
        pass


def single_instance_mutex(name: str = "Local\\BlerpDownloaderSingleInstance"):
    """Claims the app's single-instance mutex.

    Returns (handle, already_running). Closing to the tray makes "click the
    shortcut again" ordinary rather than rare, and two copies writing the
    download list would clobber each other with no error at all.

    Local\\, not Global\\: the app installs per-user.
    """
    if not available():
        return None, False
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, name)
        already = kernel32.GetLastError() == 183      # ERROR_ALREADY_EXISTS
        return handle, bool(already)
    except (OSError, AttributeError):
        return None, False


def broadcast_show_window(window_title: str = "") -> None:
    """Asks the copy that is already running to come forward.

    Without this a second launch looks like a click that did nothing at all,
    which reads as the app being broken rather than as it already being open.
    """
    if not available():
        return
    try:
        user32 = ctypes.windll.user32
        # Windows normally refuses to let a process that isn't in front raise
        # another one's window. This is the sanctioned way to hand that right
        # over, and it has to happen before the message goes out.
        user32.AllowSetForegroundWindow(-1)               # ASFW_ANY

        msg = user32.RegisterWindowMessageW(SHOW_WINDOW_MESSAGE)
        if msg:
            # SendNotifyMessage, not SendMessage: a hung first instance must not
            # keep the second one alive on screen waiting for a reply.
            user32.SendNotifyMessageW(0xFFFF, msg, 0, 0)   # HWND_BROADCAST

        # Fallback for when the tray icon is switched off and so there is no
        # window of ours listening for that message.
        if window_title:
            user32.FindWindowW.restype = wintypes.HWND
            hwnd = user32.FindWindowW(None, window_title)
            if hwnd:
                user32.ShowWindow(hwnd, 9)                # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
    except (OSError, AttributeError):
        pass

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

import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

import blerp_downloader as core
from blerp_downloader import theme as theming
from blerp_downloader.settings import MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH

# The Windows theme can change while the app is open, and Tk never sees the
# WM_SETTINGCHANGE that announces it. _poll already ticks every 100ms, so the
# registry is re-read every this-many ticks instead of on a second timer.
_THEME_CHECK_TICKS = 20


def resource_path(rel: str) -> Path:
    """Resource path - works both run from source and inside a PyInstaller bundle."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / rel


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
    Strict single-soundbite match for clipboard watching only: requires the
    blerp.com domain, unlike the paste-box's looser detect_mode()/OBJECTID_RE,
    since this runs unattended against arbitrary clipboard content (a bare
    24-hex match alone would false-positive on an unrelated git SHA, UUID
    fragment, or any other 24-hex string the user happens to copy).
    """
    text = (text or "").strip()
    if "blerp.com" not in text:
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

        # Baseline the clipboard so whatever was already copied before the app
        # opened doesn't immediately trigger a prompt/auto-download.
        try:
            self._last_clipboard = self.root.clipboard_get()
        except tk.TclError:
            self._last_clipboard = ""

        root.title(core.APP_NAME)
        try:  # window icon (bundled or from source)
            root.iconbitmap(str(resource_path("assets/icon.ico")))
        except Exception:
            pass  # fine if there's no icon
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._mode = theming.resolve_mode(self.settings.theme)
        self._plain = theming.high_contrast_active()
        self.palette = (theming.palette_for(self._mode) if self._plain
                        else theming.apply_theme(root, self._mode))
        self._tick = 0

        self._build()
        self._apply_geometry()
        try:
            core.cleanup_old_downloads()
        except Exception:
            pass  # stale-file cleanup must never block startup
        self.root.after(100, self._poll)

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
        row = self._build_options(frm, row)
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

    def _section(self, parent: ttk.Frame, row: int, text: str) -> int:
        """A section heading plus its rule. Not ttk.LabelFrame: that draws a
        raised 3D box and its label doesn't inherit the frame background."""
        pad = (0 if row == 0 else 14, 6)
        head = ttk.Frame(parent)
        head.grid(row=row, column=0, sticky="ew", pady=pad)
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text=text, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Separator(head, orient="horizontal").grid(row=0, column=1, sticky="ew", padx=(10, 0))
        return row + 1

    def _build_header(self, frm: ttk.Frame, row: int) -> int:
        bar = ttk.Frame(frm)
        bar.grid(row=row, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text=core.APP_NAME, style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(bar, text=f"v{core.__version__}", style="Muted.TLabel") \
            .grid(row=0, column=2, sticky="e")
        return row + 1

    def _build_source(self, frm: ttk.Frame, row: int) -> int:
        row = self._section(frm, row, "SOURCE")
        self.target = ttk.Entry(frm)
        self.target.grid(row=row, column=0, sticky="ew")
        ttk.Label(frm, text="A soundbite URL, or a username / profile URL for the whole profile",
                  style="Muted.TLabel").grid(row=row + 1, column=0, sticky="w", pady=(4, 0))
        return row + 2

    def _build_destination(self, frm: ttk.Frame, row: int) -> int:
        row = self._section(frm, row, "DESTINATION")
        grid = ttk.Frame(frm)
        grid.grid(row=row, column=0, sticky="ew")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="Output").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.out = ttk.Entry(grid)
        self.out.insert(0, self.settings.output_dir)
        self.out.grid(row=0, column=1, sticky="ew")
        ttk.Button(grid, text="Choose…", command=self._pick_dir) \
            .grid(row=0, column=2, padx=(8, 0))

        ttk.Label(grid, text="FFmpeg").grid(row=1, column=0, sticky="w",
                                            padx=(0, 10), pady=(8, 0))
        self.ffmpeg_dir = ttk.Entry(grid)
        self.ffmpeg_dir.insert(0, self.settings.ffmpeg_dir)
        self.ffmpeg_dir.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        ttk.Button(grid, text="Browse…", command=self._pick_ffmpeg_dir) \
            .grid(row=1, column=2, padx=(8, 0), pady=(8, 0))
        ttk.Label(grid, text="Leave both empty to save next to the app and use FFmpeg from PATH",
                  style="Muted.TLabel").grid(row=2, column=1, columnspan=2,
                                             sticky="w", pady=(4, 0))
        return row + 1

    def _build_options(self, frm: ttk.Frame, row: int) -> int:
        row = self._section(frm, row, "OPTIONS")
        box = ttk.Frame(frm)
        box.grid(row=row, column=0, sticky="ew")

        line = ttk.Frame(box)
        line.pack(fill="x")
        ttk.Label(line, text="Limit (bulk)").pack(side="left", padx=(0, 8))
        self.limit = ttk.Entry(line, width=6)
        if self.settings.bulk_limit is not None:
            self.limit.insert(0, str(self.settings.bulk_limit))
        self.limit.pack(side="left", padx=(0, 18))
        self.overwrite = tk.BooleanVar(value=self.settings.overwrite)
        ttk.Checkbutton(line, text="Overwrite existing files",
                        variable=self.overwrite).pack(side="left")

        self.watch_clipboard = tk.BooleanVar(value=self.settings.clipboard_watch)
        ttk.Checkbutton(box, text="Watch clipboard for Blerp links",
                        variable=self.watch_clipboard).pack(anchor="w", pady=(8, 0))
        self.auto_download = tk.BooleanVar(value=(self.settings.clipboard_mode == "auto"))
        ttk.Checkbutton(box, text="Auto-download (skip confirmation)",
                        variable=self.auto_download).pack(anchor="w", padx=(18, 0))
        return row + 1

    def _build_actions(self, frm: ttk.Frame, row: int) -> int:
        ttk.Separator(frm, orient="horizontal").grid(row=row, column=0, sticky="ew", pady=(16, 12))

        btns = ttk.Frame(frm)
        btns.grid(row=row + 1, column=0, sticky="ew")
        self.dl_btn = ttk.Button(btns, text="Download", command=self._start,
                                 style="Accent.TButton")
        self.dl_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.cancel.set,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        self.upd_btn = ttk.Button(btns, text="Check for Updates", command=self._check_updates)
        self.upd_btn.pack(side="right")

        self.prog = ttk.Progressbar(frm, mode="determinate")
        self.prog.grid(row=row + 2, column=0, sticky="ew", pady=(14, 6))
        self.status = ttk.Label(frm, text="Ready.", style="Status.TLabel")
        self.status.grid(row=row + 3, column=0, sticky="w")
        return row + 4

    def _build_log(self, frm: ttk.Frame, row: int) -> None:
        # tk.Text + ttk.Scrollbar rather than ScrolledText: that one is built
        # from classic widgets, so it carries a grey frame and a scrollbar that
        # ignores colours on Windows.
        wrap = ttk.Frame(frm)
        wrap.grid(row=row, column=0, sticky="nsew", pady=(10, 0))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        frm.rowconfigure(row, weight=1)

        # width=1 deliberately: Text sizes itself in characters, and the default
        # 80 would demand a ~644px window on its own.
        self.log = tk.Text(wrap, height=10, width=1, state="disabled", wrap="word",
                           bd=0, relief="flat", highlightthickness=1,
                           padx=10, pady=8)
        self.log.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.log.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=bar.set)

        ttk.Label(frm, text=core.SIGNATURE, style="Muted.TLabel") \
            .grid(row=row + 1, column=0, sticky="e", pady=(8, 0))
        self._paint_log()

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

    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(title="Choose output folder")
        if d:
            self.out.delete(0, "end")
            self.out.insert(0, d)

    def _pick_ffmpeg_dir(self) -> None:
        d = filedialog.askdirectory(title="Choose the folder containing ffmpeg.exe/ffprobe.exe")
        if d:
            self.ffmpeg_dir.delete(0, "end")
            self.ffmpeg_dir.insert(0, d)

    # ------------------------------------------------------------------ #
    #  Settings persistence
    # ------------------------------------------------------------------ #
    def _current_limit(self) -> int | None:
        text = self.limit.get().strip()
        try:
            return int(text) if text else None
        except ValueError:
            return None

    def _current_settings(self, limit: int | None) -> core.Settings:
        # A withdrawn or not-yet-mapped window reports 1x1; keep the old size
        # rather than persisting that.
        w, h = self.root.winfo_width(), self.root.winfo_height()
        if w < 100 or h < 100:
            w, h = self.settings.window_width, self.settings.window_height
        return core.Settings(
            output_dir=self.out.get().strip(),
            overwrite=self.overwrite.get(),
            bulk_limit=limit,
            # No GUI control for these two; carry them through or closing the
            # window would silently reset a hand-edited value.
            bulk_delay=self.settings.bulk_delay,
            theme=self.settings.theme,
            ffmpeg_dir=self.ffmpeg_dir.get().strip(),
            window_width=w,
            window_height=h,
            clipboard_watch=self.watch_clipboard.get(),
            clipboard_mode="auto" if self.auto_download.get() else "ask",
        )

    def _save_current_settings(self, limit: int | None) -> None:
        core.save_settings(self._current_settings(limit))

    def _on_close(self) -> None:
        # Catches preference changes (window size, clipboard-watch toggle, ...) even
        # if the user never clicks Download this session.
        self._save_current_settings(self._current_limit())
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
        self.dl_btn.configure(state="disabled")
        self.upd_btn.configure(state="disabled")
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

        self.dl_btn.configure(state="disabled")
        self.upd_btn.configure(state="disabled")
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
        self.dl_btn.configure(state="disabled")
        self.upd_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
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
        self._save_current_settings(self._current_limit())
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
        self._closing = True
        self.root.destroy()

    # ------------------------------------------------------------------ #
    #  Start / background work (never touches GUI widgets - queue only)
    # ------------------------------------------------------------------ #
    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        target = self.target.get().strip()
        if not target:
            self._log("⚠ Please enter a URL or username.")
            return
        if not core.has_ffmpeg():        # ffmpeg is required to produce video
            self._offer_ffmpeg()
            return
        limit_text = self.limit.get().strip()
        try:
            limit = int(limit_text) if limit_text else None
        except ValueError:
            self._log("⚠ Limit must be an integer.")
            return

        # Whatever was just used becomes the new default (also saved on window close,
        # so a plain preference change like a resize is captured even without a run).
        self._save_current_settings(limit)

        self.cancel.clear()
        self.dl_btn.configure(state="disabled")
        self.upd_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.prog.configure(value=0)
        self.worker = threading.Thread(
            target=self._run,
            args=(target, self.out.get().strip(), limit, self.overwrite.get(),
                  self.settings.bulk_delay),
            daemon=True,
        )
        self.worker.start()

    def _start_target(self, url: str) -> None:
        """Fills the target box with a clipboard-detected URL and starts as usual."""
        self.target.delete(0, "end")
        self.target.insert(0, url)
        self._start()

    def _run(self, target: str, out_text: str, limit: int | None, overwrite: bool,
              delay: float) -> None:
        try:
            mode, value = detect_mode(target)
            if mode == "bulk":
                self._run_bulk(value, out_text, limit, overwrite, delay)
            else:
                self._run_single(value, out_text)
        except core.BlerpError as e:
            self.q.put(("error", str(e)))
        except Exception as e:  # no error on this background thread should crash the app
            self.q.put(("error", f"Unexpected error: {e}"))
        finally:
            self.q.put(("finish", None))

    def _run_single(self, url: str, out_text: str) -> None:
        self.q.put(("log", f"Scraping page: {url}"))
        media = core.fetch_bite_media(url)
        self.q.put(("log", f"Title: {media.title}"))
        self.q.put(("total", 1))
        out_path = self._single_out(out_text, media.title)
        self.q.put(("status", "Downloading…"))
        core.process_bite(media, out_path)
        self.q.put(("progress", 1))
        self.q.put(("done", f"✓ Done → {out_path.resolve()}"))

    def _run_bulk(self, username: str, out_text: str, limit: int | None,
                  overwrite: bool, delay: float) -> None:
        self.q.put(("log", f"Scanning user: {username}"))
        bites = core.list_user_bites(username)
        if limit:
            bites = bites[:limit]
        out_dir = Path(out_text) if out_text else Path(core.sanitize(username))
        out_dir.mkdir(parents=True, exist_ok=True)
        total = len(bites)
        self.q.put(("total", total))
        self.q.put(("log", f"{total} blerps found → {out_dir}"))

        ok = skip = fail = 0
        for i, m in enumerate(bites, 1):
            if self.cancel.is_set():
                self.q.put(("log", "⏹ Stopped by user."))
                break
            out_path = out_dir / f"{core.sanitize(m.title)}_{m.bite_id}.mp4"
            self.q.put(("status", f"[{i}/{total}] {m.title[:45]}"))
            if out_path.exists() and not overwrite:
                skip += 1
                self.q.put(("log", f"[{i}/{total}] - skipped: {out_path.name}"))
            else:
                try:
                    core.process_bite(m, out_path)
                    ok += 1
                    self.q.put(("log", f"[{i}/{total}] ✓ {out_path.name}"))
                except Exception as e:
                    fail += 1
                    self.q.put(("log", f"[{i}/{total}] ✗ ERROR: {e}"))
                time.sleep(delay)
            self.q.put(("progress", i))
        self.q.put(("done",
                    f"Done: {ok} downloaded, {skip} skipped, {fail} failed → {out_dir.resolve()}"))

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
        if not self.watch_clipboard.get():
            return
        if self.worker and self.worker.is_alive():
            return  # don't interrupt/queue on top of a running download
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
        if self.auto_download.get():
            self._log(f"📋 Clipboard: Blerp link detected, downloading automatically…\n    {url}")
            self._start_target(url)
        elif messagebox.askyesno("Blerp link detected", f"Download this blerp?\n\n{url}"):
            self._start_target(url)

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
            self.dl_btn.configure(state="normal")
            self.upd_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
        elif kind == "update_result":
            self._on_update_result(val)
        elif kind == "update_downloaded":
            self._on_update_downloaded(*val)

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
        mode = theming.detect_windows_theme()
        if mode == self._mode:
            return
        self._mode = mode
        self.palette = theming.apply_theme(self.root, mode)
        self._paint_log()   # the classic Text isn't covered by ttk styles
        theming.set_titlebar_theme(self.root, mode == "dark")

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
        if not self._closing:
            self.root.after(100, self._poll)


def main() -> None:
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

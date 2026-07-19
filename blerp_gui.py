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
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import blerp_downloader as core


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

        # Baseline the clipboard so whatever was already copied before the app
        # opened doesn't immediately trigger a prompt/auto-download.
        try:
            self._last_clipboard = self.root.clipboard_get()
        except tk.TclError:
            self._last_clipboard = ""

        root.title(f"{core.APP_NAME}  ·  {core.SIGNATURE}")
        root.minsize(580, 460)
        root.geometry(f"{max(self.settings.window_width, 580)}x"
                      f"{max(self.settings.window_height, 460)}")
        try:  # window icon (bundled or from source)
            root.iconbitmap(str(resource_path("assets/icon.ico")))
        except Exception:
            pass  # fine if there's no icon
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self.root.after(100, self._poll)

    # ------------------------------------------------------------------ #
    #  UI construction
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Soundbite URL  or  username / profile URL:") \
            .grid(row=0, column=0, columnspan=3, sticky="w")
        self.target = ttk.Entry(frm)
        self.target.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        ttk.Label(frm, text="Output (optional):").grid(row=2, column=0, sticky="w")
        self.out = ttk.Entry(frm)
        self.out.insert(0, self.settings.output_dir)
        self.out.grid(row=2, column=1, sticky="ew", padx=(6, 6))
        ttk.Button(frm, text="Choose Folder…", command=self._pick_dir) \
            .grid(row=2, column=2)

        ttk.Label(frm, text="FFmpeg folder (optional, if not on PATH):") \
            .grid(row=3, column=0, sticky="w")
        self.ffmpeg_dir = ttk.Entry(frm)
        self.ffmpeg_dir.insert(0, self.settings.ffmpeg_dir)
        self.ffmpeg_dir.grid(row=3, column=1, sticky="ew", padx=(6, 6))
        ttk.Button(frm, text="Browse…", command=self._pick_ffmpeg_dir) \
            .grid(row=3, column=2)

        opt = ttk.Frame(frm)
        opt.grid(row=4, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Label(opt, text="Limit (bulk):").pack(side="left")
        self.limit = ttk.Entry(opt, width=6)
        if self.settings.bulk_limit is not None:
            self.limit.insert(0, str(self.settings.bulk_limit))
        self.limit.pack(side="left", padx=(4, 14))
        self.overwrite = tk.BooleanVar(value=self.settings.overwrite)
        ttk.Checkbutton(opt, text="Overwrite existing files", variable=self.overwrite) \
            .pack(side="left")

        cw = ttk.Frame(frm)
        cw.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.watch_clipboard = tk.BooleanVar(value=self.settings.clipboard_watch)
        ttk.Checkbutton(cw, text="Watch clipboard for Blerp links",
                        variable=self.watch_clipboard).pack(side="left")
        self.auto_download = tk.BooleanVar(value=(self.settings.clipboard_mode == "auto"))
        ttk.Checkbutton(cw, text="Auto-download (skip confirmation)",
                        variable=self.auto_download).pack(side="left", padx=(14, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=3, pady=4)
        self.dl_btn = ttk.Button(btns, text="Download", command=self._start)
        self.dl_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.cancel.set,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.prog = ttk.Progressbar(frm, mode="determinate")
        self.prog.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 2))
        self.status = ttk.Label(frm, text="Ready.")
        self.status.grid(row=8, column=0, columnspan=3, sticky="w")

        self.log = ScrolledText(frm, height=12, state="disabled", wrap="word")
        self.log.grid(row=9, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        frm.rowconfigure(9, weight=1)

        # Signature
        ttk.Label(frm, text=core.SIGNATURE, foreground="#888") \
            .grid(row=10, column=0, columnspan=3, sticky="e", pady=(6, 0))

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
        return core.Settings(
            output_dir=self.out.get().strip(),
            overwrite=self.overwrite.get(),
            bulk_limit=limit,
            bulk_delay=self.settings.bulk_delay,  # no GUI control for this; carry it through
            ffmpeg_dir=self.ffmpeg_dir.get().strip(),
            window_width=self.root.winfo_width(),
            window_height=self.root.winfo_height(),
            clipboard_watch=self.watch_clipboard.get(),
            clipboard_mode="auto" if self.auto_download.get() else "ask",
        )

    def _save_current_settings(self, limit: int | None) -> None:
        core.save_settings(self._current_settings(limit))

    def _on_close(self) -> None:
        # Catches preference changes (window size, clipboard-watch toggle, ...) even
        # if the user never clicks Download this session.
        self._save_current_settings(self._current_limit())
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
        self.status.configure(text="Installing FFmpeg…")
        self.worker = threading.Thread(target=self._winget_ffmpeg, daemon=True)
        self.worker.start()

    def _winget_ffmpeg(self) -> None:
        self.q.put(("log", "Installing FFmpeg (winget) - this can take a few minutes…"))
        try:
            subprocess.run(
                ["winget", "install", "--id", "Gyan.FFmpeg", "-e",
                 "--accept-package-agreements", "--accept-source-agreements"],
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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

    def _poll(self) -> None:
        self._check_clipboard()
        try:
            while True:
                kind, val = self.q.get_nowait()
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
                    self.stop_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)


def main() -> None:
    root = tk.Tk()
    BlerpGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

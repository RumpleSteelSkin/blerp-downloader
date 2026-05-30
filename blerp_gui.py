#!/usr/bin/env python3
"""
blerp_gui.py
============
blerp_to_mp4 için basit bir Tkinter arayüzü (yalnızca Python stdlib — ek
bağımlılık yok). Hem tek blerp hem de toplu (kullanıcı/profil) indirmeyi
destekler.

Mimari notu:
    İndirme uzun sürdüğü için ASLA doğrudan ana (GUI) thread'inde çalıştırılmaz;
    yoksa pencere donar. İş bir arka plan thread'inde döner, ilerleme/log
    mesajlarını bir queue'ya yazar; ana thread bu kuyruğu root.after(...) ile
    periyodik boşaltıp arayüzü günceller. Arka plan thread'i Tkinter
    widget'larına ASLA dokunmaz (Tkinter thread-safe değildir).

Çalıştırma:  python blerp_gui.py
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from tkinter.scrolledtext import ScrolledText

import blerp_to_mp4 as core


def detect_mode(target: str) -> tuple[str, str]:
    """
    Tek kutuya girilen metni ('single', url) ya da ('bulk', kullanıcı) olarak
    sınıflandırır (CLI'nin algılama mantığını taklit eder):
      • /u/<kullanıcı> profil URL'si        -> bulk
      • soundbite URL'si (24-hex ObjectId)  -> single
      • düz kullanıcı adı (URL değil)        -> bulk
    """
    target = target.strip()
    username = core.parse_username(target)          # /u/<kullanıcı>
    if username:
        return "bulk", username
    if "/soundbites/" in target or core.OBJECTID_RE.search(target):
        return "single", target
    if target and "://" not in target and "/" not in target:
        return "bulk", target                       # düz kullanıcı adı
    return "single", target


class BlerpGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel = threading.Event()

        root.title("Blerp → MP4 İndirici")
        root.minsize(580, 440)
        self._build()
        self.root.after(100, self._poll)

    # ------------------------------------------------------------------ #
    #  Arayüz kurulumu
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Soundbite URL'si  veya  kullanıcı adı / profil URL'si:") \
            .grid(row=0, column=0, columnspan=3, sticky="w")
        self.target = ttk.Entry(frm)
        self.target.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        ttk.Label(frm, text="Çıktı (opsiyonel):").grid(row=2, column=0, sticky="w")
        self.out = ttk.Entry(frm)
        self.out.grid(row=2, column=1, sticky="ew", padx=(6, 6))
        ttk.Button(frm, text="Klasör Seç…", command=self._pick_dir) \
            .grid(row=2, column=2)

        opt = ttk.Frame(frm)
        opt.grid(row=3, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Label(opt, text="Limit (toplu):").pack(side="left")
        self.limit = ttk.Entry(opt, width=6)
        self.limit.pack(side="left", padx=(4, 14))
        self.overwrite = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Var olanın üzerine yaz", variable=self.overwrite) \
            .pack(side="left")

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=3, pady=4)
        self.dl_btn = ttk.Button(btns, text="İndir", command=self._start)
        self.dl_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(btns, text="Durdur", command=self.cancel.set,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.prog = ttk.Progressbar(frm, mode="determinate")
        self.prog.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(6, 2))
        self.status = ttk.Label(frm, text="Hazır.")
        self.status.grid(row=6, column=0, columnspan=3, sticky="w")

        self.log = ScrolledText(frm, height=12, state="disabled", wrap="word")
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        frm.rowconfigure(7, weight=1)

    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(title="Çıktı klasörü seç")
        if d:
            self.out.delete(0, "end")
            self.out.insert(0, d)

    # ------------------------------------------------------------------ #
    #  Başlat / arka plan işi (GUI widget'larına DOKUNMAZ — sadece queue)
    # ------------------------------------------------------------------ #
    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        target = self.target.get().strip()
        if not target:
            self._log("⚠ Lütfen bir URL veya kullanıcı adı girin.")
            return
        limit_text = self.limit.get().strip()
        try:
            limit = int(limit_text) if limit_text else None
        except ValueError:
            self._log("⚠ Limit bir tam sayı olmalı.")
            return

        self.cancel.clear()
        self.dl_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.prog.configure(value=0)
        self.worker = threading.Thread(
            target=self._run,
            args=(target, self.out.get().strip(), limit, self.overwrite.get()),
            daemon=True,
        )
        self.worker.start()

    def _run(self, target: str, out_text: str, limit: int | None, overwrite: bool) -> None:
        try:
            mode, value = detect_mode(target)
            if mode == "bulk":
                self._run_bulk(value, out_text, limit, overwrite)
            else:
                self._run_single(value, out_text)
        except core.BlerpError as e:
            self.q.put(("error", str(e)))
        except Exception as e:  # arka planda hiçbir hata uygulamayı çökertmesin
            self.q.put(("error", f"Beklenmeyen hata: {e}"))
        finally:
            self.q.put(("finish", None))

    def _run_single(self, url: str, out_text: str) -> None:
        self.q.put(("log", f"Sayfa taranıyor: {url}"))
        media = core.fetch_bite_media(url)
        self.q.put(("log", f"Başlık: {media.title}"))
        self.q.put(("total", 1))
        out_path = self._single_out(out_text, media.title)
        self.q.put(("status", "İndiriliyor…"))
        core.process_bite(media, out_path)
        self.q.put(("progress", 1))
        self.q.put(("done", f"✓ Bitti → {out_path.resolve()}"))

    def _run_bulk(self, username: str, out_text: str, limit: int | None,
                  overwrite: bool) -> None:
        self.q.put(("log", f"Kullanıcı taranıyor: {username}"))
        bites = core.list_user_bites(username)
        if limit:
            bites = bites[:limit]
        out_dir = Path(out_text) if out_text else Path(core.sanitize(username))
        out_dir.mkdir(parents=True, exist_ok=True)
        total = len(bites)
        self.q.put(("total", total))
        self.q.put(("log", f"{total} blerp bulundu → {out_dir}"))

        ok = skip = fail = 0
        for i, m in enumerate(bites, 1):
            if self.cancel.is_set():
                self.q.put(("log", "⏹ Kullanıcı tarafından durduruldu."))
                break
            out_path = out_dir / f"{core.sanitize(m.title)}_{m.bite_id}.mp4"
            self.q.put(("status", f"[{i}/{total}] {m.title[:45]}"))
            if out_path.exists() and not overwrite:
                skip += 1
                self.q.put(("log", f"[{i}/{total}] • atlandı: {out_path.name}"))
            else:
                try:
                    core.process_bite(m, out_path)
                    ok += 1
                    self.q.put(("log", f"[{i}/{total}] ✓ {out_path.name}"))
                except Exception as e:
                    fail += 1
                    self.q.put(("log", f"[{i}/{total}] ✗ HATA: {e}"))
            self.q.put(("progress", i))
        self.q.put(("done",
                    f"Bitti: {ok} indirildi, {skip} atlandı, {fail} hata → {out_dir.resolve()}"))

    def _single_out(self, out_text: str, title: str) -> Path:
        """Tek mod çıktısı: .mp4 verilirse o yol; klasör verilirse içine; boşsa cwd."""
        if out_text:
            p = Path(out_text)
            return p if p.suffix.lower() == ".mp4" else p / f"{core.sanitize(title)}.mp4"
        return Path(f"{core.sanitize(title)}.mp4")

    # ------------------------------------------------------------------ #
    #  Ana thread: kuyruğu boşalt, arayüzü güncelle
    # ------------------------------------------------------------------ #
    def _log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self) -> None:
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
                    self._log(f"✗ HATA: {val}")
                    self.status.configure(text="Hata oluştu.")
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

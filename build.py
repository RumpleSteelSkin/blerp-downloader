#!/usr/bin/env python3
"""
build.py
========
PyInstaller ile iki bağımsız .exe paketler (imza: By RumpleSteelSkin):

  • dist/BlerpDownloader.exe  — GUI (pencereli, konsolsuz)
  • dist/blerp.exe            — Konsol (komut satırı)

İkonu (assets/icon.ico) hem exe ikonu olarak gömer hem de pencere ikonu için
paketin içine ekler. İmza bilgisi (CompanyName/LegalCopyright) exe'nin
"Özellikler > Ayrıntılar" sekmesine de yazılır (version_info.txt).

Kullanım:  python build.py        (PyInstaller yoksa otomatik kurar)

Not: ffmpeg/ffprobe exe'ye GÖMÜLMEZ; çalıştıran makinede PATH üzerinde olmalı.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import blerp_to_mp4 as core

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "assets" / "icon.ico"
VERSION_FILE = ROOT / "version_info.txt"

AUTHOR = core.__author__
VERSION = core.__version__
_vtuple = ", ".join((VERSION.split(".") + ["0", "0", "0", "0"])[:4])


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller kuruluyor...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)


def write_version_info() -> None:
    """exe 'Özellikler' diyaloğunda görünen sürüm/imza kaynağını yazar."""
    VERSION_FILE.write_text(f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({_vtuple}), prodvers=({_vtuple}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{AUTHOR}'),
      StringStruct('FileDescription', 'Blerp Downloader'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'BlerpDownloader'),
      StringStruct('LegalCopyright', 'By {AUTHOR}'),
      StringStruct('OriginalFilename', 'BlerpDownloader.exe'),
      StringStruct('ProductName', 'Blerp Downloader'),
      StringStruct('ProductVersion', '{VERSION}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


def pyinstaller(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", *args], check=True, cwd=ROOT)


def main() -> None:
    if not ICON.exists():
        print("İkon yok; önce `python generate_logo.py` çalıştırın.")
        sys.exit(1)
    ensure_pyinstaller()
    write_version_info()

    add_data = f"{ICON}{os.pathsep}assets"
    common = [
        "--noconfirm", "--clean", "--onefile",
        "--icon", str(ICON),
        "--version-file", str(VERSION_FILE),
        "--add-data", add_data,
    ]

    print("\n=== GUI (BlerpDownloader.exe) paketleniyor ===")
    pyinstaller(*common, "--windowed", "--name", "BlerpDownloader", "blerp_gui.py")

    print("\n=== Konsol (blerp.exe) paketleniyor ===")
    pyinstaller(*common, "--console", "--name", "blerp", "blerp_to_mp4.py")

    print(f"\n✓ Bitti.  dist/ içinde:  BlerpDownloader.exe  +  blerp.exe   ·  By {AUTHOR}")


if __name__ == "__main__":
    main()

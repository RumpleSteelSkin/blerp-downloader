#!/usr/bin/env python3
"""
build.py
========
Packages two standalone .exe files with PyInstaller (signature: By RumpleSteelSkin):

  • dist/BlerpDownloader.exe  — GUI (windowed, no console)
  • dist/blerp.exe            — Console (command line)

Embeds the icon (assets/icon.ico) both as the exe icon and inside the bundle
for the window icon. Signature info (CompanyName/LegalCopyright) is also
written to the exe's "Properties > Details" tab (version_info.txt).

Usage:  python build.py        (installs PyInstaller automatically if missing)

Note: ffmpeg/ffprobe are NOT bundled into the exe; they must be on PATH on
the machine that runs it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import blerp_downloader as core

# The Windows console (cp1252) doesn't have the ✓ printed below when stdout
# isn't a real console (e.g. piped); reconfigure to UTF-8 so it doesn't crash.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

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
        print("Installing PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)


def write_version_info() -> None:
    """Writes the version/signature resource shown in the exe's 'Properties' dialog."""
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
        print("No icon found; run `python generate_logo.py` first.")
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

    print("\n=== Packaging GUI (BlerpDownloader.exe) ===")
    pyinstaller(*common, "--windowed", "--name", "BlerpDownloader", "blerp_gui.py")

    print("\n=== Packaging console (blerp.exe) ===")
    pyinstaller(*common, "--console", "--name", "blerp", "blerp_to_mp4.py")

    # installer.iss reads this so the version lives in exactly one place
    # (blerp_downloader/__init__.py) instead of being duplicated in the .iss.
    (ROOT / "dist" / "VERSION.txt").write_text(VERSION, encoding="ascii")

    print(f"\n✓ Done.  In dist/:  BlerpDownloader.exe  +  blerp.exe   ·  By {AUTHOR}")


if __name__ == "__main__":
    main()

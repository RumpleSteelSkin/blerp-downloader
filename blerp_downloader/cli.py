"""Command-line entry point: argument parsing + single/bulk mode orchestration."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import APP_NAME, SIGNATURE, __version__, load_settings
from .errors import BlerpError
from .ffmpeg_utils import FFMPEG_HELP, has_ffmpeg
from .listing import list_user_bites, parse_username
from .pipeline import process_bite, sanitize
from .scraping import fetch_bite_media

# The Windows console (cp1252) doesn't have the ✓/✗/· symbols printed below;
# reconfigure stdout/stderr to UTF-8 so it doesn't crash on them.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def run_single(url: str, out: Path | None) -> None:
    """Downloads a single soundbite URL."""
    print(f"[1/5] Scraping page: {url}")
    media = fetch_bite_media(url)
    print(f"      Title: {media.title}")
    print(f"      Audio: {media.audio_url}")
    print(f"      Image: {media.image_url}")

    out = out or Path(f"{sanitize(media.title)}.mp4")
    process_bite(media, out, verbose=True)
    print(f"\n✓ Done -> {out.resolve()}")


def run_bulk(username: str, out_dir: Path | None, *, limit: int | None,
             delay: float, overwrite: bool) -> None:
    """Downloads all of a user's blerps in sequence (skipping ones that already exist)."""
    print(f"Scanning user: {username}")

    def scanning(pages: int, found: int) -> None:
        # A large profile is many sequential requests; \r keeps it to one line.
        print(f"\r  {found} blerps found ({pages} pages)...", end="", flush=True)

    bites = list_user_bites(username, on_progress=scanning)
    print()
    dropped = getattr(bites, "dropped", 0)
    if limit:
        bites = bites[:limit]

    out_dir = out_dir or Path(sanitize(username))
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(bites)
    print(f"{total} blerps found -> {out_dir}/")
    if dropped:
        print(f"  ({dropped} skipped: no audio or image on the server)")
    print()

    ok = skip = fail = 0
    for i, media in enumerate(bites, 1):
        # The filename includes the blerp ID: unique AND stable across runs (same blerp
        # -> same name), which is what makes "skip existing" (resume) possible.
        out_path = out_dir / f"{sanitize(media.title)}_{media.bite_id}.mp4"
        tag = f"[{i}/{total}]"
        if out_path.exists() and not overwrite:
            skip += 1
            print(f"{tag} - skipped (already exists): {out_path.name}")
            continue
        try:
            process_bite(media, out_path)
            ok += 1
            print(f"{tag} ✓ {out_path.name}")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            # Deliberately broad. A closed tuple let IncompleteRead, IndexError,
            # KeyError and JSONDecodeError escape and abandon every remaining
            # bite; one bad blerp should cost one blerp.
            fail += 1
            print(f"{tag} ✗ ERROR ({media.title[:30]}): {e}")
        time.sleep(delay)

    print(f"\nDone: {ok} downloaded, {skip} skipped, {fail} failed -> {out_dir.resolve()}")


def main() -> None:
    print(f"{APP_NAME}  v{__version__}  ·  {SIGNATURE}\n")

    # Settings supply argparse defaults (output folder, delay, limit, overwrite) so a
    # value saved from the GUI also applies here. The CLI only reads them, never
    # writes them back, so scripted/repeated CLI usage stays deterministic regardless
    # of whatever the GUI last saved.
    settings = load_settings()

    ap = argparse.ArgumentParser(
        description="Blerp -> MP4 (gif + audio). A single blerp, or all of a user's blerps.",
        epilog=SIGNATURE)
    ap.add_argument("target", nargs="?",
                    help="A soundbite URL OR a /u/<username> profile URL")
    ap.add_argument("--user", metavar="USERNAME",
                    help="Download ALL of a user's blerps (bulk mode)")
    ap.add_argument("-o", "--out", type=Path,
                    default=(Path(settings.output_dir) if settings.output_dir else None),
                    help="Single mode: output file | Bulk mode: output folder")
    ap.add_argument("--limit", type=int, default=settings.bulk_limit,
                    help="Bulk mode: only the first N blerps")
    ap.add_argument("--delay", type=float, default=settings.bulk_delay,
                    help=f"Bulk mode: wait between blerps (s, default: {settings.bulk_delay})")
    ap.add_argument("--overwrite", action=argparse.BooleanOptionalAction, default=settings.overwrite,
                    help="Bulk mode: overwrite existing files (default: skip, or as saved in settings)")
    args = ap.parse_args()

    if not has_ffmpeg():
        print(FFMPEG_HELP, file=sys.stderr)
        sys.exit(1)

    username = args.user or parse_username(args.target or "")
    try:
        if username:
            run_bulk(username, args.out, limit=args.limit,
                     delay=args.delay, overwrite=args.overwrite)
        elif args.target:
            run_single(args.target, args.out)
        else:
            ap.error("Provide a soundbite URL, a /u/<username> profile, or --user.")
    except BlerpError as e:
        sys.exit(f"ERROR: {e}")
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")
    except OSError as e:
        # Unwritable output directory, full disk, unmapped network drive: a
        # traceback here tells the user nothing they can act on.
        sys.exit(f"ERROR: {e}")

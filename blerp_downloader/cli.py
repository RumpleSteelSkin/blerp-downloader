"""Command-line entry point: argument parsing + single/bulk mode orchestration."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import APP_NAME, SIGNATURE, __version__, jobs, load_settings, maintenance
from .errors import BlerpError
from .ffmpeg_utils import FFMPEG_HELP, has_ffmpeg
from .listing import list_user_bites, parse_username
from .pipeline import FAILURE_STREAK_LIMIT, bulk_out_path, process_bite, sanitize
from .scraping import fetch_bite_media
from .settings import reset_settings

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


def _resolve_listing(username: str, resume: bool) -> tuple[list, int, bool]:
    """The profile listing, from the saved job if one fits. Returns
    (bites, dropped, came_from_saved_job)."""
    saved = jobs.load_job(username) if resume else None
    if saved and saved.matches(username) and saved.is_usable():
        # The scan is the slow part; reusing it is also more faithful, since a
        # fresh one can reorder under --limit and rename files whose titles
        # changed server-side.
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(saved.created_at))
        print(f"Resuming saved listing for {username} "
              f"({len(saved.bites)} blerps, scanned {when}). Use --no-resume to re-scan.")
        if not saved.scan_complete:
            print("  (that scan was interrupted, so it may not cover the whole profile)")
        return saved.bites, saved.dropped, True

    print(f"Scanning user: {username}")

    def scanning(pages: int, found: int) -> None:
        # A large profile is many sequential requests; \r keeps it to one line.
        print(f"\r  {found} blerps found ({pages} pages)...", end="", flush=True)

    bites = list_user_bites(username, on_progress=scanning)
    print()
    return bites, getattr(bites, "dropped", 0), False


def _download_all(bites, out_dir: Path, total: int, overwrite: bool,
                  delay: float) -> tuple[int, int, int, bool]:
    """The download loop. Returns (ok, skipped, failed, ran_to_completion)."""
    ok = skip = fail = streak = 0
    for i, media in enumerate(bites, 1):
        out_path = bulk_out_path(out_dir, media)
        tag = f"[{i}/{total}]"
        if out_path.exists() and not overwrite:
            skip += 1
            print(f"{tag} - skipped (already exists): {out_path.name}")
            continue
        try:
            process_bite(media, out_path)
            ok += 1
            streak = 0
            print(f"{tag} ✓ {out_path.name}")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            # Deliberately broad. A closed tuple let IncompleteRead, IndexError,
            # KeyError and JSONDecodeError escape and abandon every remaining
            # bite; one bad blerp should cost one blerp.
            fail += 1
            streak += 1
            print(f"{tag} ✗ ERROR ({media.title[:30]}): {e}")
            if streak >= FAILURE_STREAK_LIMIT:
                # Every remaining bite would fail the same way, each burning a
                # 30s timeout. Better to stop than to grind through thousands.
                print(f"\n{FAILURE_STREAK_LIMIT} blerps in a row failed. Stopping - "
                      "the connection may be down, or the saved listing may be "
                      "stale (re-run with --no-resume to re-scan).")
                return ok, skip, fail, False
        time.sleep(delay)
    return ok, skip, fail, True


def run_bulk(username: str, out_dir: Path | None, *, limit: int | None,
             delay: float, overwrite: bool, resume: bool = True) -> None:
    """Downloads all of a user's blerps in sequence (skipping ones that already exist)."""
    bites, dropped, from_saved = _resolve_listing(username, resume)

    out_dir = out_dir or Path(sanitize(username))
    out_dir.mkdir(parents=True, exist_ok=True)   # before recording the job

    if not from_saved:
        jobs.save_job(jobs.Job(
            username=username, bites=list(bites), dropped=dropped, limit=limit,
            overwrite=overwrite, out_dir=str(out_dir.resolve()),
            created_at=time.time(), app_version=__version__))

    if limit:
        bites = bites[:limit]
    total = len(bites)
    print(f"{total} blerps found -> {out_dir}/")
    if dropped:
        print(f"  ({dropped} skipped: no audio or image on the server)")
    print()

    ok, skip, fail, completed = _download_all(bites, out_dir, total, overwrite, delay)

    if completed:
        # Reached the end without stopping early: nothing left to come back to.
        # Keyed on that rather than "nothing remaining", because a bite that
        # keeps failing writes no file and would otherwise make the job immortal.
        jobs.clear_job(username)

    print(f"\nDone: {ok} downloaded, {skip} skipped, {fail} failed -> {out_dir.resolve()}")


def _do_clear_cache() -> None:
    """--clear-cache. The GUI offers the same thing behind a confirmation."""
    print(f"Cached: {maintenance.cache_usage().summary()}")
    for saved in jobs.saved_jobs():
        print(f"Forgetting the unfinished download for {saved.username} "
              f"({len(saved.bites)} blerps).")
    result = maintenance.clear_cache()
    if result.details:
        print("Removed: " + ", ".join(result.details))
    print(f"Freed {result.freed_mb:.1f} MB.")
    if result.in_use:
        print(f"{result.in_use} item(s) were in use and left alone.")


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
    ap.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True,
                    help="Bulk mode: reuse the saved profile listing instead of "
                         "re-scanning (default: on)")
    ap.add_argument("--clear-cache", action="store_true",
                    help="Delete downloaded update installers, leftover temp files "
                         "and the saved unfinished download, then exit")
    ap.add_argument("--reset-settings", action="store_true",
                    help="Restore every setting to its default, then exit")
    args = ap.parse_args()

    # Before the FFmpeg gate: tidying up and resetting settings have nothing to do
    # with whether FFmpeg is installed, and both must work without a target.
    if args.clear_cache:
        _do_clear_cache()
        return
    if args.reset_settings:
        reset_settings()
        print("Settings restored to defaults.")
        return

    if not has_ffmpeg():
        print(FFMPEG_HELP, file=sys.stderr)
        sys.exit(1)

    username = args.user or parse_username(args.target or "")
    try:
        if username:
            run_bulk(username, args.out, limit=args.limit, delay=args.delay,
                     overwrite=args.overwrite, resume=args.resume)
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

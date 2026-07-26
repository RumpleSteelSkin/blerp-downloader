<p align="center">
  <img src="assets/logo.png" alt="Blerp Downloader" width="128">
</p>

<h1 align="center">Blerp -> MP4 Downloader</h1>

<p align="center"><em>By RumpleSteelSkin</em></p>

> 🌐 **English** · [Türkçe](README.tr.md)

Downloads a Blerp soundbite's animated image (WebP) and its audio (MP3), then combines them with FFmpeg to produce an MP4.

> ⚙️ **The only external requirement is FFmpeg.** Everything else (Python, Pillow) is bundled in the packaged build. If FFmpeg is missing, **the app guides you** instead of crashing — the quickest fix on Windows is:
>
> ```bash
> winget install Gyan.FFmpeg
> ```
>
> Then restart the app. (The installer also installs FFmpeg automatically via winget.) See [Troubleshooting](#troubleshooting) for alternatives.

## Features

- **Two operating modes:** download a single soundbite, or bulk-download ALL of a user's blerps.
- **Animated WebP -> MP4:** merges the image and audio into a single MP4 file.
- **True frame durations:** reads each animation frame's duration directly from the WebP's raw ANMF chunks, preserving the original speed.
- **"Audio is king" sync:** the final video's length is matched to the audio length; if the animation is shorter it is looped, if longer it is cut, and the audio is never cut.
- **Stop and pick up again:** a bulk download can be stopped at any point and carried on later — even after closing the app — without re-scanning the profile. See [Stopping and resuming](#stopping-and-resuming).
- **Cache maintenance:** a button (and `--clear-cache`) to reclaim space from downloaded updates and leftover temporary files.
- **No authentication required:** bulk listing uses Blerp's public GraphQL API.
- **Persistent settings:** output folder, overwrite, bulk limit, and a custom FFmpeg location are remembered between runs — see [Settings](#settings).
- **Clipboard watch (optional, GUI):** detects a copied Blerp soundbite link and either asks before downloading or downloads it automatically.
- **In-app updates (GUI, packaged build):** a **Check for Updates** button fetches the latest release from GitHub, downloads the installer, and applies it — see [Updating](#updating).

## Requirements

- **Python 3.9+**
- **ffmpeg** and **ffprobe** — both must be available on PATH (external binaries; not listed in `requirements.txt`).
- **Pillow** (`Pillow>=10.3,<13`) — for splitting the animated WebP into frames.

## Installation

### 1. Get the code

```bash
git clone https://github.com/RumpleSteelSkin/blerp-downloader.git
cd blerp-downloader
```

> Prefer a ready-made Windows installer instead of running from source? See [Packaging (.exe & installer)](#packaging-exe--installer) — it builds `BlerpDownloader-Setup-<version>.exe`, which needs neither Python nor this clone step.

### 2. Install the Python dependency

```bash
pip install -r requirements.txt
# (or directly)
pip install Pillow
```

### 3. Install ffmpeg/ffprobe

```bash
# Windows (winget)
winget install Gyan.FFmpeg

# macOS (Homebrew)
brew install ffmpeg

# Debian / Ubuntu
sudo apt install ffmpeg
```

Verify the installation:

```bash
ffmpeg -version
ffprobe -version
```

## Usage

### Single mode (one blerp)

```bash
# Default: saves as ./<title>.mp4
python blerp_to_mp4.py "<soundbite-url>"

# Specify the output file
python blerp_to_mp4.py "<soundbite-url>" -o cikti.mp4
```

Single mode prints the 5 steps of the process (`[1/5]`...`[5/5]`) to the screen.

### Bulk mode (all of a user's blerps)

```bash
# Username via --user
python blerp_to_mp4.py --user blerpusername

# or a profile URL (/u/<username>)
python blerp_to_mp4.py "https://blerp.com/u/blerpusername"

# Only the first 10 blerps
python blerp_to_mp4.py --user blerpusername --limit 10

# Specify the output folder (default: ./<username>/)
python blerp_to_mp4.py --user blerpusername -o klasor/

# Overwrite existing files (default: skip)
python blerp_to_mp4.py --user blerpusername --overwrite
```

In bulk mode, files are named `<title>_<biteId>.mp4` and existing ones are skipped (resume). At the end of the run, a summary is printed: `Done: <n> downloaded, <n> skipped, <n> failed -> <output-path>`.

> **Note:** If both `--user` (or a `/u/` profile URL) and a soundbite URL are given together, bulk mode wins; the single-blerp URL is ignored.

### Graphical interface (GUI)

A minimal Tkinter GUI (Python standard library — no extra dependencies) is included:

```bash
python blerp_gui.py
```

Paste a soundbite URL **or** a username / profile URL into the single box (the mode is auto-detected), optionally pick an output folder and/or an FFmpeg folder (only needed if it's not on `PATH`), then click **Download**. A progress bar and a live log are shown; long bulk downloads can be stopped mid-run with **Stop**.

Two checkboxes enable clipboard watching: **Watch clipboard for Blerp links** detects a copied single-soundbite link while the window is open, and **Auto-download (skip confirmation)** decides whether it's downloaded right away or only after you confirm a prompt. Both fields and checkboxes (plus window size) are remembered for next time — see [Settings](#settings).

## Options

| Argument | Description |
|---|---|
| `target` (positional, optional) | Soundbite URL **OR** `/u/<username>` profile URL |
| `--user USERNAME` | Download ALL of a user's blerps (bulk mode) |
| `-o`, `--out` | Single mode: output file \| Bulk mode: output folder |
| `--limit N` | Bulk mode only: only the first N blerps (`bites[:N]`) |
| `--delay SN` | Bulk mode: wait between blerps (seconds, default: `0.3`) |
| `--overwrite` / `--no-overwrite` | Bulk mode: overwrite existing files / force skip, overriding the saved default |
| `--resume` / `--no-resume` | Bulk mode: reuse the saved profile listing instead of re-scanning (default: on) |
| `--clear-cache` | Delete downloaded updates, leftover temp files and the saved unfinished download, then exit |
| `--reset-settings` | Restore every setting to its default, then exit |

> `--limit`, `--delay`, and `--overwrite` take effect only in bulk mode. `-o/--out` is interpreted as a file in single mode and as a folder in bulk mode. `-o`, `--limit`, `--delay`, and `--overwrite` all default to whatever is saved in [Settings](#settings) if present, otherwise to the values shown above.

## Updating

The GUI has a **Check for Updates** button that queries this repository's [Releases](https://github.com/RumpleSteelSkin/blerp-downloader/releases).

**Packaged build (installed via the setup wizard):** if a newer version exists, the app downloads `BlerpDownloader-Setup-X.Y.Z.exe`, then — after you confirm — closes itself and runs the installer silently. The installer replaces the files, keeps your shortcuts, and reopens the app automatically. Your `settings.ini` is preserved.

**Running from source:** the button does **not** download or change anything — it tells you to use `git pull` instead, so your checkout (and any local changes) is never touched.

Notes:
- Downloads land in `%LOCALAPPDATA%\BlerpDownloader\updates\` and are cleaned up automatically after a week. The **Stop** button cancels an in-progress update download.
- **The download is verified before it is run.** Its SHA-256 is checked against the `SHA256SUMS.txt` published with the release, and the file is only moved into place — and only then launched — if it matches. A release without a checksum file is refused rather than trusted, and the saved filename comes from the version, never from the name the server supplies.
- Update checks use GitHub's unauthenticated API, which allows 60 requests per hour per IP. If you hit that limit the app says so and offers to open the Releases page instead.
- If you're running a version *newer* than the latest release (e.g. a local dev build), the app says so and refuses to "update" you downwards.
- Each release publishes a `SHA256SUMS.txt` so you can verify the installer if you download it manually. The executables are unsigned, so Windows SmartScreen may warn when you run a browser-downloaded installer — "More info" → "Run anyway", after checking the hash.

### Cutting a release (maintainers)

Releases are built by [`.github/workflows/release.yml`](.github/workflows/release.yml) on a Windows runner:

```bash
# 1. bump __version__ in blerp_downloader/__init__.py
# 2. commit it
git tag v1.1.0
git push --tags
```

The workflow verifies the tag matches `__version__` (and fails loudly if you forgot to bump it), builds both executables, compiles the installer, and publishes the release with a SHA-256 checksum file. A tag containing a hyphen (`v1.1.0-rc.1`) is published as a **prerelease**, which the in-app updater ignores — useful for testing the pipeline without shipping to users.

## Stopping and resuming

Press **Stop** during a bulk download and it finishes the blerp it is on, then stops. Press **Download** again — with the same username — and it carries on from there. This works after closing the app, and after a crash.

What makes that fast is that the *scan* is remembered, not just the files. Walking a profile is many sequential requests (the API returns 12 blerps a page), which is minutes for a large profile; resuming reuses the listing from the interrupted run, so downloading restarts immediately. Even a scan that was itself interrupted is kept, so the blerps found so far aren't thrown away.

Reusing the listing is also more faithful than scanning again: a fresh scan reorders results, so `--limit 50` would cover a different 50, and a blerp renamed on the site would be downloaded a second time under its new name.

```bash
python blerp_to_mp4.py --user someone          # resumes if an unfinished run is saved
python blerp_to_mp4.py --user someone --no-resume   # ignore it and re-scan
```

The saved run is forgotten once the download reaches the end, and after 30 days. **`--overwrite` runs are never resumed**: they deliberately ignore what is already downloaded, so there would be nothing to work out how far they got — they always start from the first blerp.

Files already downloaded are recognised by name, so nothing is fetched twice. A half-written file is never mistaken for a finished one: each MP4 is encoded alongside its destination and only moved into place once complete.

## Cache

**Clear cache…** (or `--clear-cache`) removes:

- downloaded update installers (13-35 MB each)
- temporary folders left behind by downloads that were interrupted
- the saved unfinished download, if there is one — named in the confirmation, since losing it means the next run re-scans

Optionally it also removes half-written `.part` files from your output folder. That is off by default: they are harmless, and it is the only place the app would delete anything inside a folder you chose.

Anything in use — an installer being downloaded, a temporary folder belonging to a download in progress, including one in a second copy of the app — is detected and left alone.

**Reset settings…** (or `--reset-settings`) is deliberately separate: it restores the output folder, FFmpeg folder, limit, overwrite and clipboard options to their defaults. It does not touch your downloads or the window size.

## Settings

Output folder, overwrite, bulk limit/delay, a custom FFmpeg location, window size, the theme, and the clipboard-watch options are persisted (updates use a separate folder, see [Updating](#updating)) in a small INI file (via Python's stdlib `configparser` — a database would be overkill for a handful of key-value settings):

- Windows: `%APPDATA%\BlerpDownloader\settings.ini`
- macOS/Linux: `~/.config/blerp-downloader/settings.ini`

The **GUI** reads this file on startup to prefill its fields, and writes it back whenever a download starts or the window is closed — so whatever you last used becomes the new default. The **CLI** reads the same file for its argument defaults (`-o`, `--limit`, `--delay`, `--overwrite`) but never writes to it, so repeated/scripted CLI invocations stay deterministic regardless of what the GUI last saved. A missing or corrupted settings file is never fatal — it's ignored and the built-in defaults are used.

The file is plain text and safe to edit by hand (e.g. to fix a bad `ffmpeg_dir` path) while the app is closed. A UTF-8 byte order mark is tolerated, so editors that add one won't break it.

### Appearance

The GUI follows the Windows light/dark setting and switches within a couple of seconds if you change it while the app is open, title bar included. To pin it instead, set `theme` in the settings file to `dark` or `light` (`auto` is the default):

```ini
[general]
theme = dark
```

If Windows high contrast is on, the app leaves the system theme alone so your accessibility colours still apply. Note that the file-picker and message dialogs are provided by Windows and always follow the system theme, so in a pinned mode they may not match the window.

## How It Works

### Single-blerp pipeline

1. **[1/5] The page is scraped:** the 24-character ObjectId in the URL is resolved, the page is downloaded with a browser User-Agent, and the `<script id="__NEXT_DATA__">` JSON is extracted. The `Bite:<id>` object (or the first `Bite:` key if absent) is located within `props.pageProps.initialApolloState`; `audio.mp3.url` and `image.original.url` are obtained by resolving the Apollo `__ref` pointers.
2. **[2/5] Media is downloaded:** the image is written as `image.webp` and the audio as `audio.mp3` into a temporary folder.
3. **[3/5] Frames are extracted:** the WebP is split into PNG frames (`frame_00000.png`...) with Pillow; each frame's true duration is read from the raw ANMF chunks, and missing durations default to 40ms (~25fps).
4. **[4/5] The animation video is built:** a concat demuxer list is written (the last frame is added twice, because concat ignores the value of the final duration), and a silent h264 MP4 is produced with `ffmpeg ... -vsync vfr -c:v libx264 -pix_fmt yuv420p`.
5. **[5/5] Sync + mux:** the audio's true length is measured with `ffprobe`, a `SyncPlan` is built, and the image + audio are muxed into the final MP4 with `ffmpeg`.

### Bulk listing (GraphQL)

- First, the user's `_id` is found via the `userByUsername` query (a `User not found: <username>` error if the user does not exist).
- The `soundEmotesFeaturedContentPagination` query is called page by page over the public GraphQL endpoint (`https://api.blerp.com/graphql`), which requires no authentication.
- Because the listing response already includes each blerp's audio (`audio.mp3.url`) and image (`image.original.url`) URLs, no separate page download is needed per blerp.
- Blerps are processed **sequentially** (one at a time, not in parallel); each blerp goes through the shared `process_bite` core. Bulk mode does not print the `[2/5]`...`[5/5]` sub-steps that single mode prints.

## Technical Notes

- **Animated WebP, not GIF:** Blerp images are animated WebP. Because FFmpeg cannot reliably decode this format, **Pillow** parses the frames, after which FFmpeg only concatenates the PNG frames.
- **Raw ANMF durations:** since Pillow returns frame durations of `0` for these files, the true durations are read directly from the WebP RIFF/ANMF chunks (the 24-bit little-endian value at payload bytes 12-14). This keeps the animation speed identical to the original.
- **True audio length via ffprobe:** the audio length used for sync is resolved in the following priority order: first the true value measured with `ffprobe`, then the site metadata (`audioDuration`, ms->s), and finally the built video duration.
- **"Audio is king" sync:** final length = audio length. If the animation is meaningfully shorter than the audio (`TOLERANCE = 0.05s`), it is looped from the start; if longer, it is cut with `-t` when the audio ends; the audio is never padded with silence.
- **GraphQL details (bulk):**
  - The endpoint is public and **requires no auth**; requests send a browser User-Agent and `Origin: https://blerp.com`.
  - Although the request sends `perPage=50`, **the server caps the response at 12 items per page**; `pageInfo.pageCount`/`itemCount` are unreliable (always 12) and are not used — only `pageInfo.hasNextPage` is trusted for loop control.
  - Pagination stops when `hasNextPage` becomes false (or when no items remain); if `hasNextPage` never goes false, the `max_pages=1000` upper limit prevents an infinite loop.
- **File naming (bulk):** `<title>_<biteId>.mp4`. Including the blerp ID in the name makes names unique **and** stable across runs (same blerp -> same name); this is the basis of the resume/skip behavior.
- **Temporary files:** the WebP, MP3, PNG frames, intermediate animation, and concat list are kept in an auto-cleaned `TemporaryDirectory`; only the final MP4 persists.
- **Console/encoding:** stdout/stderr are reconfigured to UTF-8, so the Windows console (cp1252) does not crash on Turkish characters or symbols such as `•`, `✓`, `✗`.

## Packaging (.exe & installer)

Build standalone Windows executables with [PyInstaller](https://pyinstaller.org):

```bash
pip install pyinstaller
python generate_logo.py   # regenerates assets/icon.ico (only needed once)
python build.py
```

This produces `dist/BlerpDownloader/` (signed *By RumpleSteelSkin* in the file properties), holding both programs plus the Python runtime they share:

- **`BlerpDownloader.exe`** — the GUI (windowed)
- **`blerp.exe`** — the command-line tool
- **`_internal/`** — the shared runtime

It's a folder build rather than two single-file executables on purpose. A single-file exe unpacks its whole runtime into `%TEMP%` on every launch, and if anything disturbs that — an antivirus scanning a freshly written unsigned exe, temp cleanup — it fails with *"Failed to load Python DLL"*. A folder build has nothing to unpack, so it starts faster and can't fail that way. Sharing one runtime between the two executables also makes the installer smaller. The trade-off: the executables need the folder around them, so copying just the `.exe` somewhere else won't work.

> ffmpeg/ffprobe are **not** bundled; the target machine must have them on its `PATH` (or the folder set in [Settings](#settings)).

To build a Windows setup wizard, install [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`) and compile the included script:

```bash
ISCC installer.iss
```

The installer (`dist/installer/BlerpDownloader-Setup-<version>.exe`) installs both executables, creates Start Menu / desktop shortcuts, and lists **RumpleSteelSkin** as the publisher. It installs **per-user (no admin prompt)** and, if ffmpeg is not already on the `PATH`, fetches it automatically via **winget** during setup — so the end user needs **neither Python nor ffmpeg** pre-installed. (If winget is unavailable, the installer shows the ffmpeg download link instead.)

## Development

```bash
python -m unittest discover tests        # the full suite
python -m unittest tests.test_scraping   # one module
```

The suite runs on every push and pull request ([`ci.yml`](.github/workflows/ci.yml)) on Python 3.9 and 3.12, and again before a release is built — a release cannot be published from a failing suite.

`tests/fixtures/` holds captured shapes of blerp.com's responses: the `__NEXT_DATA__` blob a soundbite page embeds, and a paginated profile listing. They exist so that a change to the site's structure fails a test at commit time rather than surfacing as "no audio URL found" in a user's bug report — so if the scraping breaks in the wild, updating the fixture is the first step.

## Troubleshooting

- **`ERROR: Pillow is required.`** — run `pip install Pillow`.
- **FFmpeg not found** — the app detects this and guides you instead of crashing (the CLI prints the fix; the GUI offers to install it via winget). Quickest fix: `winget install Gyan.FFmpeg`, then **restart the app**. Verify with `ffmpeg -version` / `ffprobe -version`. Alternatives: download from <https://ffmpeg.org/download.html> and add it to `PATH`, or `choco install ffmpeg` / `scoop install ffmpeg` — or, if it's installed somewhere you don't want on `PATH`, point the GUI's "FFmpeg folder" field (or `ffmpeg_dir` in [Settings](#settings)) at that folder instead.
- **`HTTP 403` / download failed** — the site/CDN blocks the default urllib User-Agent; the script already sends a browser UA. If the error persists, check for a network/access issue. The script has no network retry/backoff; in single mode an error ends the program, while in bulk mode only that blerp is skipped.
- **`__NEXT_DATA__ not found on the page (the site structure may have changed).`** — single-mode scraping depends on the site's `__NEXT_DATA__`/Apollo structure; the site structure may have changed.
- **`User not found: <username>`** — in bulk mode, the username is wrong or the user does not exist.
- **`No audio/image URL found for this blerp.`** — the expected `audio.mp3.url`/`image.original.url` fields were not found. In bulk mode, items with missing media are silently dropped from the list.
- **`Cancelled.`** — the operation was stopped with Ctrl+C.
- **Static / non-WebP image:** if ANMF durations cannot be read, single/multiple frames are still processed using Pillow + the 40ms default duration.

## Disclaimer

This tool should be used only in compliance with Blerp's Terms of Service (ToS) and only for content you have the right to download. The copyright and usage terms of the downloaded content are your responsibility; downloading, distributing, or republishing third-party content without permission is your own responsibility. In bulk mode, be considerate to the service by leaving a wait between requests with `--delay`.

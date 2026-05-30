<p align="center">
  <img src="assets/logo.png" alt="Blerp Downloader" width="128">
</p>

<h1 align="center">Blerp -> MP4 Downloader</h1>

<p align="center"><em>By RumpleSteelSkin</em></p>

> 🌐 **English** · [Türkçe](README.tr.md)

Downloads a Blerp soundbite's animated image (WebP) and its audio (MP3), then combines them with FFmpeg to produce an MP4.

## Features

- **Two operating modes:** download a single soundbite, or bulk-download ALL of a user's blerps.
- **Animated WebP -> MP4:** merges the image and audio into a single MP4 file.
- **True frame durations:** reads each animation frame's duration directly from the WebP's raw ANMF chunks, preserving the original speed.
- **"Audio is king" sync:** the final video's length is matched to the audio length; if the animation is shorter it is looped, if longer it is cut, and the audio is never cut.
- **Resume in bulk mode:** existing files are skipped, so an interrupted download continues where it left off instead of starting over.
- **No authentication required:** bulk listing uses Blerp's public GraphQL API.
- **Turkish interface:** all output and error messages are in Turkish.

## Requirements

- **Python 3.8+**
- **ffmpeg** and **ffprobe** — both must be available on PATH (external binaries; not listed in `requirements.txt`).
- **Pillow** (`Pillow>=10.0`) — for splitting the animated WebP into frames.

## Installation

```bash
# Python dependency
pip install -r requirements.txt
# (or directly)
pip install Pillow
```

Installing ffmpeg/ffprobe:

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

In bulk mode, files are named `<title>_<biteId>.mp4` and existing ones are skipped (resume). At the end of the run, a summary is printed: `<n> indirildi, <n> atlandı, <n> hata` ("<n> downloaded, <n> skipped, <n> errors").

> **Note:** If both `--user` (or a `/u/` profile URL) and a soundbite URL are given together, bulk mode wins; the single-blerp URL is ignored.

### Graphical interface (GUI)

A minimal Tkinter GUI (Python standard library — no extra dependencies) is included:

```bash
python blerp_gui.py
```

Paste a soundbite URL **or** a username / profile URL into the single box (the mode is auto-detected), optionally pick an output folder, then click **İndir** (Download). A progress bar and a live log are shown; long bulk downloads can be stopped mid-run with **Durdur** (Stop).

## Options

| Argument | Description |
|---|---|
| `target` (positional, optional) | Soundbite URL **OR** `/u/<username>` profile URL |
| `--user KULLANICI` | Download ALL of a user's blerps (bulk mode) |
| `-o`, `--out` | Single mode: output file \| Bulk mode: output folder |
| `--limit N` | Bulk mode only: only the first N blerps (`bites[:N]`) |
| `--delay SN` | Bulk mode: wait between blerps (seconds, default: `0.3`) |
| `--overwrite` | Bulk mode: overwrite existing files (default: skip) |

> `--limit`, `--delay`, and `--overwrite` take effect only in bulk mode. `-o/--out` is interpreted as a file in single mode and as a folder in bulk mode.

## How It Works

### Single-blerp pipeline

1. **[1/5] The page is scraped:** the 24-character ObjectId in the URL is resolved, the page is downloaded with a browser User-Agent, and the `<script id="__NEXT_DATA__">` JSON is extracted. The `Bite:<id>` object (or the first `Bite:` key if absent) is located within `props.pageProps.initialApolloState`; `audio.mp3.url` and `image.original.url` are obtained by resolving the Apollo `__ref` pointers.
2. **[2/5] Media is downloaded:** the image is written as `image.webp` and the audio as `audio.mp3` into a temporary folder.
3. **[3/5] Frames are extracted:** the WebP is split into PNG frames (`frame_00000.png`...) with Pillow; each frame's true duration is read from the raw ANMF chunks, and missing durations default to 40ms (~25fps).
4. **[4/5] The animation video is built:** a concat demuxer list is written (the last frame is added twice, because concat ignores the value of the final duration), and a silent h264 MP4 is produced with `ffmpeg ... -vsync vfr -c:v libx264 -pix_fmt yuv420p`.
5. **[5/5] Sync + mux:** the audio's true length is measured with `ffprobe`, a `SyncPlan` is built, and the image + audio are muxed into the final MP4 with `ffmpeg`.

### Bulk listing (GraphQL)

- First, the user's `_id` is found via the `userByUsername` query (a "Kullanıcı bulunamadı" / "User not found" error if the user does not exist).
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

This produces two single-file executables in `dist/` (signed *By RumpleSteelSkin* in their file properties):

- **`BlerpDownloader.exe`** — the GUI (windowed)
- **`blerp.exe`** — the command-line tool

> ffmpeg/ffprobe are **not** bundled into the executables; the target machine must have them on its `PATH`.

To build a Windows setup wizard, install [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`) and compile the included script:

```bash
ISCC installer.iss
```

The installer (`dist/installer/BlerpDownloader-Setup-1.0.0.exe`) installs both executables, creates Start Menu / desktop shortcuts, and lists **RumpleSteelSkin** as the publisher. It installs **per-user (no admin prompt)** and, if ffmpeg is not already on the `PATH`, fetches it automatically via **winget** during setup — so the end user needs **neither Python nor ffmpeg** pre-installed. (If winget is unavailable, the installer shows the ffmpeg download link instead.)

## Troubleshooting

- **`HATA: Pillow gerekli.`** ("ERROR: Pillow required.") — run `pip install Pillow`.
- **`'ffmpeg' / 'ffprobe' bulunamadı` (FileNotFoundError) or mux/probe fails** — make sure ffmpeg and ffprobe are installed and on PATH (`ffmpeg -version`, `ffprobe -version`).
- **`HTTP 403` / download failed** — the site/CDN blocks the default urllib User-Agent; the script already sends a browser UA. If the error persists, check for a network/access issue. The script has no network retry/backoff; in single mode an error ends the program, while in bulk mode only that blerp is skipped.
- **`Sayfada __NEXT_DATA__ bulunamadı (site yapısı değişmiş olabilir).`** ("__NEXT_DATA__ not found on the page (the site structure may have changed).") — single-mode scraping depends on the site's `__NEXT_DATA__`/Apollo structure; the site structure may have changed.
- **`Kullanıcı bulunamadı: <ad>`** ("User not found: <name>") — in bulk mode, the username is wrong or the user does not exist.
- **`Bu blerp için ses/görsel URL'si bulunamadı.`** ("No audio/image URL found for this blerp.") — the expected `audio.mp3.url`/`image.original.url` fields were not found. In bulk mode, items with missing media are silently dropped from the list.
- **`İptal edildi.`** ("Cancelled.") — the operation was stopped with Ctrl+C.
- **Static / non-WebP image:** if ANMF durations cannot be read, single/multiple frames are still processed using Pillow + the 40ms default duration.

## Disclaimer

This tool should be used only in compliance with Blerp's Terms of Service (ToS) and only for content you have the right to download. The copyright and usage terms of the downloaded content are your responsibility; downloading, distributing, or republishing third-party content without permission is your own responsibility. In bulk mode, be considerate to the service by leaving a wait between requests with `--delay`.

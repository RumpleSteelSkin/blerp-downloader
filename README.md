# Blerp Downloader (blerp_to_mp4)

> 🌐 **English** · [Türkçe](README.tr.md)

A single-file Python 3 command-line tool that downloads the animated image (WebP) and audio (MP3) from a [Blerp](https://blerp.com) soundbite URL and merges them with FFmpeg into a single **MP4** file.

A blerp is essentially a pairing of "audio + an accompanying animated image." This tool combines the two into a single shareable, playable video.

## Features

- Automatically scrapes the Blerp soundbite page; it resolves the audio (MP3) and image (WebP) links from the page's `__NEXT_DATA__` (Apollo state) data.
- Splits the animated WebP into PNG frames and preserves correct timing by reading each frame's **actual duration** from the WebP's raw ANMF chunks.
- Also supports static (non-animated) images; uses a reasonable default duration with a single frame.
- Builds a silent, variable-frame-duration H.264 animation video with the FFmpeg concat demuxer, then merges it with the audio.
- Smart synchronization policy: **audio is king**. The final video's length is set equal to the audio's length.
  - If the animation is shorter than the audio, it is looped from the start.
  - If the animation is longer than the audio, it is cut off when the audio ends.
  - The audio is never cut off.
- Determines the audio length first from the actual file (`ffprobe`), failing that from the site metadata, and failing that from the video duration.
- Output: an H.264 video optimized for web playback (`+faststart`), `yuv420p`, plus 192 kbps AAC audio.
- For titles containing Turkish characters, output is set to UTF-8 to prevent the Windows console from crashing; the file name is cleaned of invalid characters.

## Requirements

- **Python 3.8+** — type hints use the PEP 604 union syntax (`float | None`) enabled via `from __future__ import annotations`.
- **Pillow** `>=10.0` — for animated WebP frame extraction (`pip install Pillow`).
- **ffmpeg** and **ffprobe** — must be installed on the system and accessible on the `PATH`.

> FFmpeg/ffprobe do not come with `pip`; they must be installed separately on the system. If `ffprobe` is not found, the tool keeps working by falling back to the site metadata or the video duration instead of crashing.

## Installation

1. Download this repository/files.
2. Install the Python dependency:

   ```bash
   pip install -r requirements.txt
   ```

   `requirements.txt` contents:

   ```
   Pillow>=10.0
   ```

3. Make sure FFmpeg and ffprobe are installed and on the `PATH`:

   ```bash
   ffmpeg -version
   ffprobe -version
   ```

   If the commands do not print version information, install FFmpeg and add it to your `PATH`.

## Usage

Basic usage — provide a Blerp soundbite URL:

```bash
python blerp_to_mp4.py "https://blerp.com/soundbites/<blerp-id>"
```

Specify the output file's path/name with `-o` (or `--out`):

```bash
python blerp_to_mp4.py "https://blerp.com/soundbites/<blerp-id>" -o cikti.mp4
```

```bash
python blerp_to_mp4.py "https://blerp.com/soundbites/<blerp-id>" --out "C:\Videolar\benim_ses.mp4"
```

If `-o` is not provided, the output is a `<title>.mp4` file derived from the soundbite title (e.g. `Komik Ses.mp4`).

### Options

| Argument | Description |
| --- | --- |
| `url` | Blerp soundbite URL (required, positional argument) |
| `-o`, `--out` | Output MP4 path (default: `<title>.mp4`) |

For help:

```bash
python blerp_to_mp4.py -h
```

### Example output

While running, progress is shown in 5 steps. The program prints these strings in Turkish; English glosses are added in parentheses below on the first occurrence:

```
[1/5] Sayfa taranıyor: https://blerp.com/soundbites/<blerp-id>          (Scraping page)
      Başlık : Örnek Ses                                                (Title)
      Ses    : https://.../audio.mp3                                    (Audio)
      Görsel : https://.../image.webp                                   (Image)
[2/5] Medya indiriliyor...                                              (Downloading media)
[3/5] WebP kareleri çıkarılıyor...                                      (Extracting WebP frames)
      24 kare, ~3.60s animasyon                                         (24 frames, ~3.60s animation)
[4/5] Animasyon videosu kuruluyor...                                    (Building animation video)
      Plan: hedef=5.42s loop_video=True pad_audio=False                 (Plan: target=...)
[5/5] Ses + video birleştiriliyor...                                    (Merging audio + video)

✓ Bitti -> C:\...\Örnek Ses.mp4                                         (Done)
```

The result is a single `.mp4` file: its duration equals the audio's length, the animated image looped when needed, containing H.264 video + AAC audio, optimized for web playback with `+faststart`.

## How It Works

The tool runs as a single linear pipeline (the `run()` function, 5 steps):

```
URL
 -> [1] Download the page, extract the __NEXT_DATA__ (Apollo state) JSON
        Resolve audio.mp3.url and image.original.url from the Bite object
 -> [2] Download the media (WebP + MP3) to a temporary directory
 -> [3] Split the animated WebP into PNG frames with Pillow
        (reading the actual frame durations from the raw ANMF chunks)
 -> [4] Build a silent H.264 animation video with the FFmpeg concat demuxer
 -> [5] Merge the audio with the video according to the sync policy -> out.mp4
```

1. **Scraping the page** — `parse_bite_id()` extracts the 24-character hex Blerp `ObjectId` from the URL with a regex (`[0-9a-fA-F]{24}`). `fetch_bite_media()` downloads the page, parses out the `__NEXT_DATA__` JSON, and finds the `Bite:<id>` object in the Apollo Client cache under `props.pageProps.initialApolloState`. As a result, the audio URL, image URL, and title are resolved; the title and the audio and image links are printed.
2. **Downloading media** — the WebP image and the MP3 audio are written to a temporary directory (`tempfile.TemporaryDirectory`) as `image.webp` and `audio.mp3`. All intermediate files stay in this directory and are automatically cleaned up when the job finishes.
3. **Extracting WebP frames** — `extract_frames()` opens the image with Pillow and writes each frame to disk as an `RGBA` PNG; it reads the actual frame durations from the raw WebP bytes. The frame count and the approximate total animation duration are printed.
4. **Building the animation video** — `build_animation_video()` writes a concat list file and produces a silent `anim.mp4` (libx264, yuv420p) with FFmpeg. Then `probe_duration()` measures the audio duration, `resolve_sync()` builds the synchronization plan (`SyncPlan`), and the plan (`hedef`, `loop_video`, `pad_audio`) is printed.
5. **Merging audio + video** — `mux()` combines the silent video and the audio according to the `SyncPlan` to produce the final MP4, and `✓ Bitti -> <output path>` is printed.

## Technical Notes

### `__NEXT_DATA__` Apollo state scraping

Blerp is a Next.js site. Within the page HTML there is a `<script id="__NEXT_DATA__" type="application/json">...</script>` tag carrying the server-side rendered data. The relevant data is held in the **Apollo Client cache** under `props.pageProps.initialApolloState`. The `Bite:<id>` key is tried first; if there is no match, it falls back to the first object whose key starts with `Bite:`. Because the Apollo cache is normalized, nested objects are stored as `{"__ref": "Type:id"}` pointers; `_resolve_ref()` resolves these into the actual objects. The audio is read from `audio.mp3.url` and the image from `image.original.url`.

> **User-Agent spoofing:** The CDN and site reject the default `urllib` User-Agent with a **403**. For this reason, all requests (`http_get()`) are made with a real Chrome/120 desktop browser UA header and a 30-second timeout.

### Animated WebP — not GIF

Blerp images are not GIFs but **animated WebP**. There are two technical subtleties here:

**a) FFmpeg cannot decode animated WebP.** For this reason, frame splitting is not left to FFmpeg; `extract_frames()` opens the image with **Pillow**, performs `seek()` for each of `n_frames`, and writes each frame to disk as an `RGBA` PNG (`frame_00000.png`, `frame_00001.png`, ...).

**b) Pillow does not reliably return frame durations for these files (it mostly returns 0).** The actual frame durations (ground truth) are read directly from the WebP's raw bytes. `parse_anmf_durations()` traverses the RIFF/WEBP container, finds each `ANMF` (animation frame) chunk, and extracts the **24-bit little-endian frame_duration** value (ms) from bytes 12–14 of the header. Since RIFF chunks are even-aligned, a padding byte is skipped on odd-sized payloads.

The duration list is aligned with the frame count: missing or 0 durations are filled with a **40 ms (~25 fps)** default. For static (single-frame) images, a single frame plus a reasonable default duration is produced as well.

### Variable frame duration with the FFmpeg concat demuxer

`build_animation_video()` creates a **concat demuxer** list file (`*.concat.txt`, UTF-8). For each frame, a `file '<path>'` line and its actual duration `duration <seconds>` line are written (paths use forward slashes and single quotes for FFmpeg compatibility). Because the concat demuxer **ignores the `duration` value of the last frame**, the last frame is added to the list one more time. The video is rendered with `libx264` / `yuv420p`, using `-vsync vfr` to preserve the variable frame durations, and written with `+faststart`.

### Audio duration measurement and "audio is king" synchronization

The site's `audioDuration` metadata is not always reliable. For this reason, before the final length is determined, the **actual duration** of the downloaded MP3 is measured using **ffprobe** via `probe_duration()`. This measurement is the ground truth; if it fails, it falls back to the metadata duration, and failing that to the video duration:

```python
audio_dur = probe_duration(mp3_path) or media.audio_duration_s or video_dur
```

The `resolve_sync()` policy is based on the **"audio is king"** principle (`TOLERANCE = 0.05s`) and returns a `SyncPlan`:

- The final video length is **always** set equal to the audio length; the video is cut to the audio length with `-t`. The audio is the main content of a blerp, and the image merely adorns it — that is why the audio is never cut off.
- If the animation is meaningfully shorter than the audio (`video + TOLERANCE < audio`), the **video is looped from the start** until the audio is filled (`-stream_loop -1` inside `mux()`).
- Since the target is already the audio length, the audio is not padded with silence (`pad_audio_with_silence` is always `False`).

This policy is fixed and there is no CLI option to change it. Likewise, the codec/quality settings are fixed as well: video is `libx264` / `yuv420p`, audio is `aac` 192 kbps — there is no CLI control over the codec, bit rate, or resolution.

## Troubleshooting

- **`HATA: Pillow gerekli.`** (ERROR: Pillow required) — Run `pip install Pillow`.
- **`ffmpeg` / `ffprobe` not found (FileNotFoundError, etc.)** — FFmpeg is not installed or not on the `PATH`; verify with `ffmpeg -version`. (If FFmpeg fails during the merge step, the error is not caught and surfaces as a stack trace.)
- **`HATA: URL'de geçerli bir blerp ID bulunamadı`** (ERROR: no valid blerp ID found in the URL) — The URL you provided does not contain a 24-character blerp ID; use the full address of the soundbite page directly.
- **`HATA: <url> -> HTTP 403`** / **`HATA: <url> indirilemedi`** (ERROR: <url> could not be downloaded) — The CDN or site may have blocked access; check your connection and the URL and try again.
- **`HATA: Sayfada __NEXT_DATA__ bulunamadı`** (ERROR: __NEXT_DATA__ not found on the page) or **`HATA: Apollo state içinde Bite nesnesi yok.`** (ERROR: no Bite object in the Apollo state) — Blerp's page structure may have changed; the tool may need to be updated.
- **`HATA: Bu blerp için ses (mp3) URL'si bulunamadı.`** (ERROR: no audio (mp3) URL found for this blerp) / **`... görsel URL'si bulunamadı.`** (... no image URL found) — The relevant media data for this soundbite was not present on the page; try another blerp.

## Disclaimer

This tool is for personal and educational purposes only. Before using it, make sure you comply with [Blerp](https://blerp.com)'s terms of use and terms of service. Only download content that you have the right to download and use. The copyrights of the downloaded audio and images belong to their respective owners; all responsibility for the use of this content rests with the user.

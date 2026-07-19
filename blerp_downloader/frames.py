"""WebP animation frame extraction (Pillow) + raw ANMF duration parsing."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("ERROR: Pillow is required. Install: pip install Pillow")


def parse_anmf_durations(webp_bytes: bytes) -> list[int]:
    """
    Reads each ANMF (animation frame) duration (ms) directly from the WebP's raw RIFF
    chunks. Pillow returns 0 for these durations on these files, so this is ground truth.
    Returns an empty list if the image isn't animated.
    """
    if webp_bytes[:4] != b"RIFF" or webp_bytes[8:12] != b"WEBP":
        return []
    durations, pos = [], 12
    while pos + 8 <= len(webp_bytes):
        fourcc = webp_bytes[pos : pos + 4]
        size = struct.unpack("<I", webp_bytes[pos + 4 : pos + 8])[0]
        payload = webp_bytes[pos + 8 : pos + 8 + size]
        if fourcc == b"ANMF" and len(payload) >= 16:
            # frame_duration lives at bytes 12-14 of the ANMF header, 24-bit little-endian.
            durations.append(payload[12] | (payload[13] << 8) | (payload[14] << 16))
        pos += 8 + size + (size & 1)  # chunks are padded to an even boundary
    return durations


def extract_frames(webp_path: Path, out_dir: Path) -> tuple[list[Path], list[int]]:
    """
    Splits a WebP into PNG frames. Returns (frame_paths, frame_durations_ms).
    Static images produce a single frame with a reasonable default duration.
    """
    raw = webp_path.read_bytes()
    durations = parse_anmf_durations(raw)

    im = Image.open(webp_path)
    n = getattr(im, "n_frames", 1)
    out_dir.mkdir(parents=True, exist_ok=True)

    frames: list[Path] = []
    for i in range(n):
        im.seek(i)
        fp = out_dir / f"frame_{i:05d}.png"
        im.convert("RGBA").save(fp)
        frames.append(fp)

    # Align the duration list with the frame count (missing values default to 40ms ~25fps).
    if len(durations) != n:
        durations = [d if d > 0 else 40 for d in durations]
        durations += [40] * (n - len(durations))
    durations = [d if d > 0 else 40 for d in durations[:n]]
    return frames, durations

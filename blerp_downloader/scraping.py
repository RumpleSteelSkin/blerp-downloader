"""Single-blerp scraping: resolves a soundbite URL to its audio/image media URLs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .errors import BlerpError
from .network import http_get

OBJECTID_RE = re.compile(r"[0-9a-fA-F]{24}")
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


@dataclass
class BiteMedia:
    bite_id: str
    title: str
    audio_url: str
    image_url: str
    audio_duration_s: float  # from site metadata (ms -> s), 0 if unknown


def parse_bite_id(url: str) -> str:
    """Extracts the 24-character Blerp ObjectId from a URL."""
    m = OBJECTID_RE.search(url)
    if not m:
        raise BlerpError(f"No valid blerp ID found in URL: {url}")
    return m.group(0)


def _resolve_ref(apollo: dict, node):
    """Resolves an Apollo `{'__ref': 'Type:id'}` pointer to the actual object."""
    if isinstance(node, dict) and "__ref" in node:
        return apollo.get(node["__ref"], {})
    return node or {}


def fetch_bite_media(url: str) -> BiteMedia:
    """Fetches a soundbite page and extracts its audio/image URLs and title."""
    bite_id = parse_bite_id(url)
    html = http_get(url).decode("utf-8", "replace")

    m = NEXT_DATA_RE.search(html)
    if not m:
        raise BlerpError("__NEXT_DATA__ not found on the page (the site structure may have changed).")
    data = json.loads(m.group(1))

    apollo = data.get("props", {}).get("pageProps", {}).get("initialApolloState", {})
    bite = apollo.get(f"Bite:{bite_id}")
    if bite is None:  # ID mismatch: fall back to the first Bite-typed object
        bite = next((v for k, v in apollo.items() if k.startswith("Bite:")), None)
    if bite is None:
        raise BlerpError("No Bite object in the Apollo state.")

    audio = _resolve_ref(apollo, bite.get("audio"))
    image = _resolve_ref(apollo, bite.get("image"))
    audio_url = (audio.get("mp3") or {}).get("url", "")
    image_url = (image.get("original") or {}).get("url", "")

    if not audio_url:
        raise BlerpError("No audio (mp3) URL found for this blerp.")
    if not image_url:
        raise BlerpError("No image URL found for this blerp.")

    return BiteMedia(
        bite_id=bite_id,
        title=bite.get("title") or bite_id,
        audio_url=audio_url,
        image_url=image_url,
        audio_duration_s=(bite.get("audioDuration") or 0) / 1000.0,
    )

"""Single-blerp scraping: resolves a soundbite URL to its audio/image media URLs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .errors import BlerpError
from .network import http_get

OBJECTID_RE = re.compile(r"[0-9a-fA-F]{24}")
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)

BLERP_HOSTS = ("blerp.com",)


def is_blerp_url(text: str) -> bool:
    """Whether `text` is an http(s) URL whose host really is Blerp.

    Parsed rather than substring-matched: "blerp.com" can appear in a path or
    query of someone else's URL, and "blerp.com.attacker.tld" starts with it.
    """
    try:
        parsed = urlparse((text or "").strip())
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in BLERP_HOSTS)


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
    # Only Blerp pages are scrapeable, and only Blerp should be fetched: the URL
    # comes from whatever the user pasted, and OBJECTID_RE matches any 24-hex run
    # in any string, so without this a link to another host would be requested.
    if not is_blerp_url(url):
        raise BlerpError(
            f"Not a blerp.com link: {url[:120]}\n"
            "Paste a soundbite URL such as https://blerp.com/soundbites/<id>")
    bite_id = parse_bite_id(url)
    html = http_get(url).decode("utf-8", "replace")

    m = NEXT_DATA_RE.search(html)
    if not m:
        raise BlerpError("__NEXT_DATA__ not found on the page (the site structure may have changed).")
    try:
        data = json.loads(m.group(1))
    except ValueError as e:
        raise BlerpError(f"The page's embedded data could not be read ({e}).")
    if not isinstance(data, dict):
        raise BlerpError("The page's embedded data has an unexpected shape.")

    apollo = data.get("props", {}).get("pageProps", {}).get("initialApolloState", {})
    if not isinstance(apollo, dict):
        raise BlerpError("The page's embedded data has an unexpected shape.")
    bite = apollo.get(f"Bite:{bite_id}")
    if bite is None:
        # The requested ID isn't in the page. The cache also holds related and
        # recommended bites, so picking "the first one" would hand back someone
        # else's audio and report success - only accept it when it's unambiguous.
        candidates = [v for k, v in apollo.items() if k.startswith("Bite:")]
        if len(candidates) != 1:
            raise BlerpError(
                "That soundbite is not on the page (it may have been removed or "
                "made private). Refusing to guess which of the "
                f"{len(candidates)} other blerps on it you meant.")
        bite = candidates[0]
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

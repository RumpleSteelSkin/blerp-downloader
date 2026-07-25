"""Low-level HTTP + GraphQL transport for the Blerp API."""

from __future__ import annotations

import json
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .errors import BlerpError

# The site's CDN returns 403 for the default urllib User-Agent; a real browser UA is required.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
GRAPHQL_ENDPOINT = "https://api.blerp.com/graphql"

# urllib's default opener also handles file://, ftp:// and data:. Media URLs come
# out of JSON parsed from a remote page, so without this a crafted response could
# make the app read a local file and mux it into the user's video.
ALLOWED_SCHEMES = ("http", "https")

# Media is a few MB. The cap only exists so a hostile or broken server cannot
# stream until the process runs out of memory - the body is read whole.
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024


def require_web_url(url: str) -> str:
    """Rejects anything that isn't an ordinary http(s) URL."""
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise BlerpError(f"Malformed URL: {e}")
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise BlerpError(
            f"Refusing to fetch {parsed.scheme or 'scheme-less'} URL "
            f"(only {'/'.join(ALLOWED_SCHEMES)} are allowed): {url[:120]}")
    if not parsed.netloc:
        raise BlerpError(f"URL has no host: {url[:120]}")
    return url


def _read_capped(resp, limit: int, url: str) -> bytes:
    """Reads at most `limit` bytes, treating an over-long body as an error
    rather than silently truncating it into a corrupt media file."""
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise BlerpError(f"Response from {url[:80]} is larger than {limit // 1048576} MB; stopping.")
    return data


def http_get(url: str, *, limit: int = MAX_DOWNLOAD_BYTES) -> bytes:
    """Downloads a URL with a browser User-Agent and returns the raw bytes."""
    require_web_url(url)
    req = Request(url, headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=30) as resp:
            return _read_capped(resp, limit, url)
    except HTTPError as e:
        raise BlerpError(f"{url} -> HTTP {e.code}")
    except URLError as e:
        raise BlerpError(f"Failed to download {url} -> {e.reason}")
    except HTTPException as e:
        # IncompleteRead and friends are not OSError, so without this they escape
        # every caller's handler and abort a whole bulk run.
        raise BlerpError(f"Connection broke while downloading {url} -> {e}")
    except OSError as e:  # socket timeouts, TLS failures
        raise BlerpError(f"Failed to download {url} -> {e}")


def graphql(query: str, variables: dict) -> dict:
    """POSTs to Blerp's public GraphQL endpoint (no auth required) and returns the data block."""
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = Request(
        GRAPHQL_ENDPOINT, data=body,
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 "Origin": "https://blerp.com"},
    )
    try:
        with urlopen(req, timeout=30) as resp:
            raw = _read_capped(resp, MAX_JSON_BYTES, GRAPHQL_ENDPOINT)
    except URLError as e:  # HTTPError included (subclass)
        raise BlerpError(f"GraphQL request failed: {e}")
    except HTTPException as e:
        raise BlerpError(f"GraphQL connection broke: {e}")
    except OSError as e:
        raise BlerpError(f"GraphQL request failed: {e}")

    try:
        data = json.loads(raw)
    except ValueError as e:
        # A captive portal or proxy returning an HTML login page lands here.
        raise BlerpError(f"GraphQL returned something that isn't JSON ({e}).")
    if not isinstance(data, dict):
        raise BlerpError("GraphQL returned an unexpected response shape.")
    if data.get("errors"):
        raise BlerpError("GraphQL error: " + json.dumps(data["errors"])[:200])
    return data.get("data") or {}

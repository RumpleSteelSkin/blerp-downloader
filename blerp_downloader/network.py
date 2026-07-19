"""Low-level HTTP + GraphQL transport for the Blerp API."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import BlerpError

# The site's CDN returns 403 for the default urllib User-Agent; a real browser UA is required.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
GRAPHQL_ENDPOINT = "https://api.blerp.com/graphql"


def http_get(url: str) -> bytes:
    """Downloads a URL with a browser User-Agent and returns the raw bytes."""
    req = Request(url, headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except HTTPError as e:
        raise BlerpError(f"{url} -> HTTP {e.code}")
    except URLError as e:
        raise BlerpError(f"Failed to download {url} -> {e.reason}")


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
            data = json.loads(resp.read())
    except URLError as e:  # HTTPError included (subclass)
        raise BlerpError(f"GraphQL request failed: {e}")
    if data.get("errors"):
        raise BlerpError("GraphQL error: " + json.dumps(data["errors"])[:200])
    return data.get("data") or {}

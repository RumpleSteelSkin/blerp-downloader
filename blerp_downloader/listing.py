"""Bulk listing via Blerp's public GraphQL API - all blerps for a user profile."""

from __future__ import annotations

import re
import time

from .errors import BlerpError
from .network import graphql
from .scraping import BiteMedia

# Profile URL: https://blerp.com/u/<username>
USER_URL_RE = re.compile(r"/u/([A-Za-z0-9_.\-]+)")

# Minimal shape of the query the profile grid actually sends (found via browser network capture).
# soundEmotesFeaturedContentPagination caps each page at 12 items regardless of perPage, and
# pageInfo.pageCount/itemCount are UNRELIABLE (always 12) - only hasNextPage is trustworthy.
_USER_ID_QUERY = (
    "query($u:String!){ web { userByUsername(username:$u){ _id username } } }"
)
_PROFILE_BITES_QUERY = """
query($page:Int,$perPage:Int,$streamerId:MongoID,$sortOverride:String,$purposeTypes:[String],$pageType:String){
  soundEmotes {
    soundEmotesFeaturedContentPagination(
      page:$page, perPage:$perPage, userId:$streamerId,
      sortOverride:$sortOverride, purposeTypes:$purposeTypes,
      pageType:$pageType, isDashboard:false
    ){
      pageInfo { hasNextPage }
      items {
        biteId
        bite {
          _id title audioDuration
          audio { mp3 { url } }
          image { original { url } }
        }
      }
    }
  }
}"""


def parse_username(target: str) -> str | None:
    """Extracts the username from a `/u/<username>` profile URL; None if not a match."""
    m = USER_URL_RE.search(target or "")
    return m.group(1) if m else None


def fetch_user_id(username: str) -> tuple[str, str]:
    """Resolves a username to (_id, canonical username)."""
    data = graphql(_USER_ID_QUERY, {"u": username})
    user = (data.get("web") or {}).get("userByUsername")
    if not user:
        raise BlerpError(f"User not found: {username}")
    return user["_id"], user.get("username") or username


def _edge_to_media(edge: dict) -> BiteMedia | None:
    """Converts one pagination edge to a BiteMedia; None if its media is missing."""
    bid = edge.get("biteId")
    b = edge.get("bite") or {}
    audio_url = ((b.get("audio") or {}).get("mp3") or {}).get("url", "")
    image_url = ((b.get("image") or {}).get("original") or {}).get("url", "")
    if not bid or not audio_url or not image_url:
        return None
    return BiteMedia(
        bite_id=bid,
        title=b.get("title") or bid,
        audio_url=audio_url,
        image_url=image_url,
        audio_duration_s=(b.get("audioDuration") or 0) / 1000.0,
    )


class BiteList(list):
    """The bites found, plus how many entries had no usable media.

    A list subclass so every existing caller - len(), slicing for --limit,
    iteration - keeps working untouched.
    """

    def __init__(self, items=(), dropped: int = 0):
        super().__init__(items)
        self.dropped = dropped


def _collect_page(pg: dict | None, bites: list, seen: set) -> tuple[int, int]:
    """Adds one page's usable bites. Returns (items seen, entries dropped)."""
    items = (pg or {}).get("items") or []
    dropped = 0
    for item in items:
        media = _edge_to_media(item)
        if media is None:
            dropped += 1
        elif media.bite_id not in seen:
            seen.add(media.bite_id)
            bites.append(media)
    return len(items), dropped


def _fetch_page(user_id: str, page: int, *, attempts: int = 3) -> dict | None:
    """One page of the profile listing, retried on transient failures.

    The server caps a page at 12 items, so a large profile is a long chain of
    requests. Without retries a single hiccup near the end discarded everything
    collected so far and the run downloaded nothing at all.
    """
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            data = graphql(_PROFILE_BITES_QUERY, {
                "page": page, "perPage": 50, "streamerId": user_id,
                "sortOverride": "LEX_DESC_REAL", "purposeTypes": ["SOUND"],
                "pageType": "PROFILE",
            })
        except BlerpError:
            if attempt == attempts:
                raise
            time.sleep(delay)
            delay *= 2
            continue
        return (data.get("soundEmotes") or {}).get("soundEmotesFeaturedContentPagination")
    return None


def list_user_bites(username: str, *, max_pages: int = 1000,
                    on_progress=None, cancel=None) -> list[BiteMedia]:
    """
    Collects ALL blerps on a user's profile via pagination.
    The response already includes each blerp's audio+image URL, so no extra request is needed.
    max_pages: safety net against an infinite loop if the API never returns hasNextPage=false.

    on_progress(pages_done, bites_so_far) is called after each page - scanning a
    large profile takes minutes, and without it the UI looks hung.
    cancel: a threading.Event; checked between pages so Stop works during the scan.

    Entries whose media is missing are skipped; `dropped` on the result says how
    many, so the caller can say so rather than silently reporting a smaller
    profile than the user sees on the site.
    """
    user_id, _ = fetch_user_id(username)
    bites: list[BiteMedia] = []
    seen: set[str] = set()
    dropped = 0

    for page in range(1, max_pages + 1):
        if cancel is not None and cancel.is_set():
            break
        pg = _fetch_page(user_id, page)
        n_items, n_dropped = _collect_page(pg, bites, seen)
        dropped += n_dropped
        if on_progress:
            on_progress(page, len(bites))
        # pageInfo can be absent on a partial response; treat that as the end
        # rather than raising KeyError out of the whole run.
        if not pg or not n_items or not ((pg.get("pageInfo") or {}).get("hasNextPage")):
            break

    return BiteList(bites, dropped)

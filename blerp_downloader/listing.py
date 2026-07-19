"""Bulk listing via Blerp's public GraphQL API - all blerps for a user profile."""

from __future__ import annotations

import re

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


def list_user_bites(username: str, *, max_pages: int = 1000) -> list[BiteMedia]:
    """
    Collects ALL blerps on a user's profile via pagination.
    The response already includes each blerp's audio+image URL, so no extra request is needed.
    max_pages: safety net against an infinite loop if the API never returns hasNextPage=false.
    """
    user_id, _ = fetch_user_id(username)
    bites: list[BiteMedia] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        data = graphql(_PROFILE_BITES_QUERY, {
            "page": page, "perPage": 50, "streamerId": user_id,
            "sortOverride": "LEX_DESC_REAL", "purposeTypes": ["SOUND"],
            "pageType": "PROFILE",
        })
        pg = (data.get("soundEmotes") or {}).get("soundEmotesFeaturedContentPagination")
        items = pg.get("items", []) if pg else []
        for media in (_edge_to_media(it) for it in items):
            if media and media.bite_id not in seen:
                seen.add(media.bite_id)
                bites.append(media)
        if not pg or not items or not pg["pageInfo"]["hasNextPage"]:
            break
    return bites

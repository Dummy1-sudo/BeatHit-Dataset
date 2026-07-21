from __future__ import annotations
from ..http import CachedHttp

URL = "https://graphql.anilist.co"
QUERY = r'''query ($page:Int!, $perPage:Int!) {
  Page(page:$page, perPage:$perPage) {
    pageInfo { hasNextPage }
    media(type:ANIME, sort:POPULARITY_DESC, isAdult:false) {
      id idMal popularity averageScore format seasonYear
      title { romaji english native }
    }
  }
}'''

def fetch_top_anime(http: CachedHttp, limit: int = 10000) -> list[dict]:
    out=[]; page=1
    while len(out) < limit:
        data=http.post_json(URL,{"query":QUERY,"variables":{"page":page,"perPage":50}})
        block=data["data"]["Page"]
        out.extend(block["media"])
        if not block["pageInfo"]["hasNextPage"]: break
        page += 1
    return out[:limit]

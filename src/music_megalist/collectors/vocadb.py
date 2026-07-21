from __future__ import annotations
from ..http import CachedHttp

URL="https://vocadb.net/api/songs"

def iter_popular_songs(http: CachedHttp, max_candidates: int = 100000):
    start=0; page_size=50
    while start < max_candidates:
        params={
          "start":start,"maxResults":page_size,"sort":"RatingScore",
          "getTotalCount":"true","fields":"MainPicture,PVs,Artists,Tags,Albums"
        }
        data=http.get_json(URL,params=params)
        items=data.get("items",[])
        if not items: break
        for item in items: yield item
        start += len(items)
        if start >= data.get("totalCount", max_candidates): break

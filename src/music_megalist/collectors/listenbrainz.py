from __future__ import annotations
from ..http import CachedHttp

API="https://api.listenbrainz.org/1"
DATASETS="https://datasets.listenbrainz.org"

def recording_popularity(http: CachedHttp, mbids: list[str]) -> list[dict]:
    out=[]
    for i in range(0,len(mbids),1000):
        out.extend(http.post_json(f"{API}/popularity/recording", {"recording_mbids":mbids[i:i+1000]}))
    return out

def tag_radio(http: CachedHttp, tags: list[str], count: int=1000, pop_begin: int=0, pop_end: int=100) -> dict:
    params=[("tag",t) for t in tags]
    # httpx list-valued params are intentionally constructed by direct client for repeated tag keys.
    http._pace()
    r=http.client.get(f"{API}/lb-radio/tags",params=params+[("operator","OR"),("count",count),("pop_begin",pop_begin),("pop_end",pop_end)])
    r.raise_for_status(); return r.json()

from __future__ import annotations
from ..http import CachedHttp

BASE="https://api.animethemes.moe/api/anime"

def fetch_by_anilist_id(http: CachedHttp, anilist_id: int) -> list[dict]:
    # AnimeThemes supports filtering on linked external resources and nested includes.
    # Keep raw payload cached because API include/filter syntax can evolve.
    params={
        "filter[has]":"resources",
        "filter[site]":"AniList",
        "filter[external_id]":str(anilist_id),
        "include":"animethemes.song.artists,resources",
        "page[size]":"100"
    }
    data=http.get_json(BASE,params=params)
    return data.get("anime", data.get("data", []))

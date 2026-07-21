from __future__ import annotations
from ..http import CachedHttp

WS="https://musicbrainz.org/ws/2"

def lookup_recording(http: CachedHttp, mbid: str) -> dict:
    return http.get_json(f"{WS}/recording/{mbid}",params={"fmt":"json","inc":"artists+releases+isrcs+tags+genres"})

def search_recording(http: CachedHttp, title: str, artist: str, limit: int=5) -> dict:
    q=f'recording:"{title}" AND artist:"{artist}"'
    return http.get_json(f"{WS}/recording",params={"fmt":"json","query":q,"limit":limit})

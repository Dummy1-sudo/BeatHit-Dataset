from __future__ import annotations
import re
from bs4 import BeautifulSoup
from ..http import CachedHttp

NUM=re.compile(r"(?<!\d)(\d{1,3}(?:[,.]\d{3})+|\d{5,})(?!\d)")

def parse_public_track_page(html: str) -> int | None:
    """Best-effort parser for cumulative play count exposed in Spotify's public track page text."""
    text=BeautifulSoup(html,"lxml").get_text(" ",strip=True)
    candidates=[]
    for m in NUM.finditer(text):
        n=int(m.group(1).replace(",","").replace(".",""))
        if n >= 1000: candidates.append(n)
    # Avoid pretending a number is a play count if no plausible candidate exists.
    return max(candidates) if candidates else None

def fetch_track_count(http: CachedHttp, spotify_track_id: str) -> tuple[int | None,str]:
    url=f"https://open.spotify.com/track/{spotify_track_id}"
    html=http.get_text(url)
    return parse_public_track_page(html), url

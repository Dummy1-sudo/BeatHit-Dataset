from __future__ import annotations
import re
from bs4 import BeautifulSoup
from ..http import CachedHttp

def artist_song_totals(http: CachedHttp, spotify_artist_id: str) -> list[dict]:
    url=f"https://kworb.net/spotify/artist/{spotify_artist_id}_songs.html"
    soup=BeautifulSoup(http.get_text(url),"lxml")
    rows=[]
    for tr in soup.select("table tbody tr, table tr"):
        td=[x.get_text(" ",strip=True) for x in tr.find_all("td")]
        if len(td) < 2: continue
        nums=[re.sub(r"[^0-9]","",x) for x in td[1:]]
        if not nums or not nums[0]: continue
        rows.append({"title":td[0],"streams":int(nums[0]),"source_url":url})
    return rows

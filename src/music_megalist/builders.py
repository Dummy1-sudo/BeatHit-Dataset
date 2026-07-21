from __future__ import annotations
from pathlib import Path
from .http import CachedHttp
from .io import write_rows, read_rows
from .models import SongRow
from .dedupe import dedupe
from .collectors.anilist import fetch_top_anime

DATA=Path("data")

def build_anime() -> None:
    http=CachedHttp()
    anime=fetch_top_anime(http,10000)
    # Stage 1 is deliberately saved before theme joins so progress is resumable/auditable.
    import json
    p=DATA/"anime"/"anime_rank_stage.jsonl"; p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("w",encoding="utf-8") as f:
        for i,a in enumerate(anime,1):
            a["rank"]=i; f.write(json.dumps(a,ensure_ascii=False)+"\n")

def build_megalist() -> None:
    paths=[
      DATA/"anime"/"anime_songs.csv", DATA/"vocaloid"/"vocaloid_spotify_10m.csv",
      DATA/"worldwide"/"worldwide_51000.csv", DATA/"classical"/"classical_10000.csv",
      DATA/"vtuber_original"/"vtuber_original_10000.csv", DATA/"emerging"/"emerging_10000.csv",
      DATA/"genres"/"genres_10000.csv", DATA/"screen_soundtracks"/"screen_soundtracks_10000.csv",
      DATA/"vtuber_non_original"/"vtuber_non_original_10000.csv"
    ]
    rows=[]
    for p in paths:
        if p.exists(): rows.extend(read_rows(p))
    country_rows=[]
    for p in sorted((DATA/"countries").glob("*_top1000.csv")):
        country_rows.extend(read_rows(p))
    if country_rows:
        from .countries import collapse_country_rows_for_megalist
        rows.extend(collapse_country_rows_for_megalist(country_rows))
    write_rows(dedupe(rows), DATA/"megalist"/"megalist.csv")

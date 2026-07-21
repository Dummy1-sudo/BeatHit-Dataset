from __future__ import annotations
from collections import defaultdict
from math import log1p
from .models import SongRow

def reciprocal_rank_fusion(rows: list[SongRow], metric_groups: dict[str, list[SongRow]], k: int = 60) -> None:
    scores = defaultdict(float)
    for _, group in metric_groups.items():
        ranked = sorted(group, key=lambda r: r.metric_value, reverse=True)
        for pos, row in enumerate(ranked, 1):
            ident = row.spotify_track_id or row.musicbrainz_recording_mbid or f"{row.title}\0{row.main_artist}"
            scores[ident] += 1.0 / (k + pos)
    if not scores: return
    max_score = max(scores.values())
    for row in rows:
        ident = row.spotify_track_id or row.musicbrainz_recording_mbid or f"{row.title}\0{row.main_artist}"
        row.overall_popularity_score = 100.0 * scores.get(ident, 0.0) / max_score

def growth_score(recent_listens: int, historical_listens: int, unique_listeners: int, catalog_tracks: int, age_days: int) -> float:
    velocity = recent_listens / max(age_days, 30)
    growth = recent_listens / max(historical_listens - recent_listens, 1)
    return 0.42*log1p(velocity) + 0.28*log1p(growth) + 0.20*log1p(unique_listeners) + 0.10*log1p(catalog_tracks)

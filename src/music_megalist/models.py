from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field, field_validator

class SongRow(BaseModel):
    rank: int | None = None
    title: str
    main_artist: str
    featured_artists: list[str] = Field(default_factory=list)
    album: str | None = None
    release_date: str | None = None
    release_year: int | None = None
    genres: list[str] = Field(default_factory=list)
    composer: str | None = None
    anime_title: str | None = None
    anime_popularity: int | None = None
    screen_work: str | None = None
    vtuber: str | None = None
    is_original: bool | None = None
    metric_name: str
    metric_value: float
    metric_unit: str
    # Explicit convenience columns. These are populated only when the underlying source
    # actually exposes a count; they are never estimated from popularity scores.
    listen_count: int | None = None
    listen_source: str | None = None
    view_count: int | None = None
    overall_popularity_score: float | None = None
    spotify_track_id: str | None = None
    musicbrainz_recording_mbid: str | None = None
    isrc: str | None = None
    source_url: str
    retrieved_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())
    source_notes: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "main_artist", "metric_name", "metric_unit", "source_url")
    @classmethod
    def nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field must not be empty")
        return value

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from .models import SongRow


def norm(s: str) -> str:
    """Normalize display strings for conservative song-level matching."""
    s = unicodedata.normalize("NFKC", s or "").casefold()
    # Treat common release-edition decorations as the same song while preserving
    # meaningful version names such as acoustic/live/remix.
    s = re.sub(r"\([^)]*(?:remaster|remastered|deluxe|explicit)[^)]*\)", "", s)
    s = re.sub(r"\b(?:19|20)\d{2}\s+remaster(?:ed)?\b", "", s)
    return re.sub(r"[^\w]+", "", s)


def key(row: SongRow) -> tuple[str, str]:
    """Return a song-level key rather than a release/recording-level key.

    Spotify IDs can differ for the same song across album/single/regional releases.
    The requested output is a song list, so normalized title + main artist is the
    primary identity. Stable IDs remain valuable provenance fields and are merged.
    """
    nt, na = norm(row.title), norm(row.main_artist)
    if nt and na:
        return ("text_artist", f"{nt}\x1f{na}")
    if row.isrc:
        return ("isrc", row.isrc.upper())
    if row.spotify_track_id:
        return ("spotify", row.spotify_track_id)
    if row.musicbrainz_recording_mbid:
        return ("mbid", row.musicbrainz_recording_mbid)
    return ("fallback", f"{nt}\x1f{na}")


def _metric_priority(row: SongRow) -> int:
    """Prefer the most directly useful song-level evidence when duplicate rows disagree."""
    if row.metric_name in {"spotify_streams", "spotify_streams_snapshot"} and row.listen_count is not None:
        return 6
    if row.metric_name == "youtube_views" and row.view_count is not None:
        return 5
    if row.listen_count is not None and row.metric_name != "spotify_daily_streams":
        return 4
    if row.metric_name == "spotify_daily_streams" and row.listen_count is not None:
        return 3
    if row.view_count is not None:
        return 2
    return 1


def _quality(row: SongRow) -> tuple[int, float, float]:
    # For cumulative counters, a larger observation is normally the fresher/stronger one.
    # Metric class is considered before composite scores so a popularity proxy cannot displace
    # a real Spotify/YouTube counter during cross-source merge.
    observed = float(row.listen_count if row.listen_count is not None else (row.view_count if row.view_count is not None else row.metric_value))
    score = row.overall_popularity_score if row.overall_popularity_score is not None else -1.0
    return (_metric_priority(row), observed, score)


def _merge(preferred: SongRow, other: SongRow) -> SongRow:
    """Fill missing provenance/metadata without inventing or summing incompatible counts."""
    out = preferred.model_copy(deep=True)
    scalar_fields = [
        "album", "release_date", "release_year", "composer", "anime_title",
        "anime_popularity", "screen_work", "vtuber", "is_original",
        "spotify_track_id", "musicbrainz_recording_mbid", "isrc",
    ]
    for field in scalar_fields:
        if getattr(out, field) in (None, "") and getattr(other, field) not in (None, ""):
            setattr(out, field, getattr(other, field))
    out.featured_artists = list(dict.fromkeys([*out.featured_artists, *other.featured_artists]))
    out.genres = sorted(set(out.genres) | set(other.genres))

    # Keep alternate metrics as provenance instead of combining unlike counters.
    if (other.metric_name, other.metric_value, other.metric_unit) != (out.metric_name, out.metric_value, out.metric_unit):
        alt = out.extra.setdefault("alternate_metrics", [])
        candidate = {
            "metric_name": other.metric_name,
            "metric_value": other.metric_value,
            "metric_unit": other.metric_unit,
            "listen_count": other.listen_count,
            "view_count": other.view_count,
            "source_url": other.source_url,
            "retrieved_at": other.retrieved_at,
        }
        if candidate not in alt:
            alt.append(candidate)
    sources = out.extra.setdefault("merged_sources", [])
    for source in (out.source_url, other.source_url):
        if source and source not in sources:
            sources.append(source)
    return out


def _stable_keys(row: SongRow) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if row.isrc:
        keys.append(("isrc", row.isrc.upper()))
    if row.spotify_track_id:
        keys.append(("spotify", row.spotify_track_id))
    if row.musicbrainz_recording_mbid:
        keys.append(("mbid", row.musicbrainz_recording_mbid))
    return keys


def dedupe(rows: Iterable[SongRow]) -> list[SongRow]:
    """Deduplicate at song level while honoring stable recording identifiers.

    Either a shared stable ID *or* the same normalized title+main-artist pair is enough
    to join records. This catches cross-source rows where one source has a Spotify ID
    and another does not, while still satisfying exact-ID deduplication.
    """
    groups: dict[int, SongRow] = {}
    alias: dict[tuple[str, str], int] = {}
    next_id = 0

    for row in rows:
        text_key = ("text_artist", f"{norm(row.title)}\x1f{norm(row.main_artist)}")
        aliases = [*_stable_keys(row), text_key]
        matches = {alias[a] for a in aliases if a in alias and alias[a] in groups}

        if not matches:
            gid = next_id
            next_id += 1
            groups[gid] = row.model_copy(deep=True)
        else:
            gid = min(matches)
            combined = groups[gid]
            # If multiple existing groups are bridged by this row, merge them now.
            for other_gid in sorted(matches - {gid}):
                other = groups.pop(other_gid)
                combined = _merge(combined, other) if _quality(combined) >= _quality(other) else _merge(other, combined)
                for a, mapped in list(alias.items()):
                    if mapped == other_gid:
                        alias[a] = gid
            combined = _merge(row, combined) if _quality(row) > _quality(combined) else _merge(combined, row)
            groups[gid] = combined

        # Register all aliases from the merged record as well as the incoming row.
        merged = groups[gid]
        all_aliases = aliases + _stable_keys(merged) + [("text_artist", f"{norm(merged.title)}\x1f{norm(merged.main_artist)}")]
        for a in all_aliases:
            alias[a] = gid

    out = list(groups.values())
    out.sort(key=_quality, reverse=True)
    for i, row in enumerate(out, 1):
        row.rank = i
    return out

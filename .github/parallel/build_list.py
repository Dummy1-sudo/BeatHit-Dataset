#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ALL_TARGETS, DATA, dataset_name, read_json, status_path, write_json
from music_megalist import fullbuild
from music_megalist.dedupe import norm
from music_megalist.io import read_rows, write_rows
from music_megalist.models import SongRow

TODAY = date.today().isoformat()


def _status_fragment(target: str, dataset: dict, **extra: object) -> None:
    payload = {
        "target_name": target,
        "dataset_name": dataset_name(target),
        "worker_success": False,
        "dataset": dataset,
        **extra,
    }
    write_json(status_path(target), payload)


def _load_children_policy() -> dict:
    policy_path = DATA / "seeds" / "children_quebec_policy.json"
    policy = read_json(policy_path)
    if not policy:
        raise RuntimeError(f"Missing or invalid children policy: {policy_path}")
    return policy


def _children_text(row: SongRow) -> str:
    parts = [row.title, row.main_artist, " ".join(row.genres or [])]
    try:
        parts.append(json.dumps(row.extra or {}, ensure_ascii=False))
    except Exception:
        pass
    return norm(" ".join(str(x or "") for x in parts))


def _children_marker_match(text: str, markers: list[str]) -> bool:
    return any(norm(marker) in text for marker in markers if norm(marker))


def _children_queries(policy: dict, start_year: int) -> list[dict]:
    queries = [dict(q) for q in (policy.get("queries") or []) if isinstance(q, dict)]
    end_year = date.today().year
    for template in policy.get("year_query_templates") or []:
        if not isinstance(template, dict):
            continue
        term_template = str(template.get("term") or "")
        if "{year}" not in term_template:
            continue
        for year in range(start_year, end_year + 1):
            item = dict(template)
            item["term"] = term_template.format(year=year)
            if item.get("youtube_term"):
                item["youtube_term"] = str(item["youtube_term"]).format(year=year)
            queries.append(item)
    return queries


def _children_filter_existing(rows: list[SongRow], policy: dict, start_year: int) -> list[SongRow]:
    """Keep only existing rows with explicit Quebec-childhood relevance evidence.

    The old global children/tag list is intentionally not grandfathered in. A row must
    either identify a Quebec/Canada childhood property/artist or a globally common child
    franchise that is explicitly allowed by the Quebec policy. Generic world-wide
    children's music is discarded rather than treated as Quebec exposure evidence.
    """
    local_markers = list(policy.get("quebec_markers") or [])
    franchise_markers = list(policy.get("franchise_markers") or [])
    evergreen_markers = list(policy.get("evergreen_markers") or [])
    reject_terms = [norm(x) for x in (policy.get("reject_terms") or [])]
    out: list[SongRow] = []
    for row in rows:
        text = _children_text(row)
        if any(term and term in text for term in reject_terms):
            continue
        local = _children_marker_match(text, local_markers)
        franchise = _children_marker_match(text, franchise_markers)
        evergreen = _children_marker_match(text, evergreen_markers)
        recent = bool(row.release_year and row.release_year >= start_year)
        if not (local or franchise):
            continue
        if not (recent or evergreen):
            continue
        extra = dict(row.extra or {})
        extra.update({
            "children_policy": "quebec_age_0_12_last_10_years",
            "quebec_exposure_evidence": "existing row matched curated Quebec/child-franchise marker",
            "observation_window_start_year": start_year,
        })
        row.extra = extra
        out.append(row)
    return out


def _apple_children_candidates(policy: dict, start_year: int) -> list[SongRow]:
    """Discover Quebec-relevant child songs from the Canadian Apple storefront."""
    queries = _children_queries(policy, start_year)
    reject_terms = [norm(x) for x in (policy.get("reject_terms") or [])]
    child_genres = {norm(x) for x in (policy.get("apple_children_genres") or ["Children's Music"])}
    franchise_markers = list(policy.get("franchise_markers") or [])
    local_markers = list(policy.get("quebec_markers") or [])
    evergreen_markers = list(policy.get("evergreen_markers") or [])
    min_score = float(policy.get("minimum_accept_score") or 55.0)
    rows: list[SongRow] = []
    seen_text: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    headers = {"User-Agent": "BeatHit-Dataset/1.0 (+https://github.com/Dummy1-sudo/BeatHit-Dataset)"}

    with httpx.Client(timeout=45, follow_redirects=True, headers=headers) as client:
        for q in queries:
            term = str(q.get("term") or "").strip()
            if not term:
                continue
            kind = str(q.get("kind") or "generic")
            lang = str(q.get("language") or "und")
            weight = float(q.get("weight") or 30.0)
            evergreen_query = bool(q.get("evergreen"))
            anchors = [str(x) for x in (q.get("anchors") or []) if str(x).strip()]
            url = (
                "https://itunes.apple.com/search?entity=song&limit=200&country=CA&term="
                f"{quote_plus(term)}"
            )
            try:
                response = client.get(url)
                response.raise_for_status()
                items = response.json().get("results") or []
            except Exception:
                continue

            for rank, item in enumerate(items, 1):
                title = str(item.get("trackName") or "").strip()
                artist = str(item.get("artistName") or "").strip()
                album = str(item.get("collectionName") or "").strip()
                genre = str(item.get("primaryGenreName") or "").strip()
                track_id = str(item.get("trackId") or "").strip()
                if not title or not artist:
                    continue
                combined = norm(f"{title} {artist} {album} {genre}")
                if any(term_norm and term_norm in combined for term_norm in reject_terms):
                    continue
                if str(item.get("trackExplicitness") or "").casefold() == "explicit":
                    continue

                anchor_match = _children_marker_match(combined, anchors)
                local_match = _children_marker_match(combined, local_markers)
                franchise_match = _children_marker_match(combined, franchise_markers)
                genre_match = norm(genre) in child_genres or "children" in norm(genre)

                if kind in {"quebec_artist", "franchise"} and anchors and not anchor_match:
                    continue
                if kind in {"generic", "quebec_generic"} and not genre_match:
                    continue
                if not (genre_match or local_match or franchise_match or anchor_match):
                    continue

                release = str(item.get("releaseDate") or "")
                year = int(release[:4]) if re.match(r"^\d{4}", release) else None
                evergreen = evergreen_query or _children_marker_match(combined, evergreen_markers)
                recent = bool(year and year >= start_year)
                if not (recent or evergreen):
                    continue

                rank_score = max(0.0, 20.0 - ((rank - 1) / max(1, len(items))) * 20.0)
                score = weight + rank_score
                if genre_match:
                    score += 15.0
                if local_match or kind.startswith("quebec"):
                    score += 20.0
                if franchise_match or kind == "franchise":
                    score += 10.0
                if recent:
                    score += 10.0
                score = min(100.0, score)
                if score < min_score:
                    continue

                key = (norm(title), norm(artist))
                if key in seen_text or (track_id and track_id in seen_ids):
                    continue
                seen_text.add(key)
                if track_id:
                    seen_ids.add(track_id)

                rows.append(SongRow(
                    title=title,
                    main_artist=artist,
                    release_year=year,
                    genres=[genre or "children's music"],
                    languages=[lang if lang in {"fr", "en"} else "und"],
                    metric_name="quebec_child_exposure_score",
                    metric_value=score,
                    metric_unit="score_0_100",
                    overall_popularity_score=score,
                    source_url=str(item.get("trackViewUrl") or item.get("collectionViewUrl") or "https://music.apple.com/ca/"),
                    retrieved_at=TODAY,
                    source_notes=(
                        "Canadian Apple storefront candidate selected by a Quebec age-0-12 childhood query; "
                        "recent-window or curated-evergreen evidence required."
                    ),
                    extra={
                        "culture_category": "children_childhood",
                        "children_policy": "quebec_age_0_12_last_10_years",
                        "source": "apple_itunes_ca",
                        "itunes_track_id": track_id,
                        "itunes_query": term,
                        "itunes_query_kind": kind,
                        "itunes_primary_genre": genre,
                        "quebec_exposure_score": score,
                        "observation_window_start_year": start_year,
                        "evidence": {
                            "canadian_storefront": True,
                            "children_genre": genre_match,
                            "quebec_marker": local_match,
                            "franchise_marker": franchise_match,
                            "query_anchor": anchor_match,
                            "recent_release": recent,
                            "evergreen": evergreen,
                        },
                    },
                ))
            time.sleep(0.06)
    return rows


def _youtube_children_candidates(policy: dict, start_year: int) -> list[SongRow]:
    """Use targeted Canadian YouTube Music searches as exposure evidence.

    Search requests are deliberately bounded because search.list is expensive. The
    results are ordered by views, use regionCode=CA and safeSearch=strict, and only
    child-specific Quebec/franchise queries from the policy are issued.
    """
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return []
    queries = _children_queries(policy, start_year)
    max_searches = int(os.getenv("BEATHIT_CHILDREN_YOUTUBE_SEARCHES", str(policy.get("youtube_max_searches") or 48)))
    reject_terms = [norm(x) for x in (policy.get("youtube_reject_terms") or policy.get("reject_terms") or [])]
    min_views_generic = int(policy.get("youtube_min_views_generic") or 5_000)
    min_views_local = int(policy.get("youtube_min_views_local") or 500)
    rows: list[SongRow] = []
    seen_ids: set[str] = set()
    headers = {"User-Agent": "BeatHit-Dataset/1.0 (+https://github.com/Dummy1-sudo/BeatHit-Dataset)"}

    def clean_video_title(value: str) -> tuple[str, str]:
        text = re.sub(r"\s*[\[(](?:official|lyrics?|paroles|clip officiel|music video|video officiel)[^\])]*[\])]\s*", " ", value, flags=re.I)
        text = re.sub(r"\s+", " ", text).strip(" -|")
        for sep in (" - ", " – ", " — ", " | "):
            if sep in text:
                left, right = text.split(sep, 1)
                if left.strip() and right.strip():
                    return right.strip(), left.strip()
        return text, ""

    with httpx.Client(timeout=45, follow_redirects=True, headers=headers) as client:
        for q in queries[:max_searches]:
            term = str(q.get("youtube_term") or q.get("term") or "").strip()
            if not term:
                continue
            kind = str(q.get("kind") or "generic")
            lang = str(q.get("language") or "und")
            params = {
                "part": "snippet",
                "type": "video",
                "videoCategoryId": "10",
                "maxResults": 50,
                "order": "viewCount",
                "safeSearch": "strict",
                "regionCode": "CA",
                "q": term,
                "publishedAfter": f"{start_year}-01-01T00:00:00Z",
                "key": api_key,
            }
            if lang in {"fr", "en"}:
                params["relevanceLanguage"] = lang
            try:
                search = client.get("https://www.googleapis.com/youtube/v3/search", params=params)
                search.raise_for_status()
                items = search.json().get("items") or []
            except Exception:
                continue
            ids = [str((item.get("id") or {}).get("videoId") or "") for item in items]
            ids = [x for x in ids if x and x not in seen_ids]
            if not ids:
                continue
            try:
                details = client.get("https://www.googleapis.com/youtube/v3/videos", params={
                    "part": "snippet,statistics",
                    "id": ",".join(ids),
                    "key": api_key,
                })
                details.raise_for_status()
                videos = details.json().get("items") or []
            except Exception:
                continue
            for video in videos:
                vid = str(video.get("id") or "")
                snippet = video.get("snippet") or {}
                raw_title = str(snippet.get("title") or "").strip()
                channel = str(snippet.get("channelTitle") or "").strip()
                combined = norm(f"{raw_title} {channel} {snippet.get('description') or ''}")
                if any(term_norm and term_norm in combined for term_norm in reject_terms):
                    continue
                anchors = [str(x) for x in (q.get("anchors") or []) if str(x).strip()]
                if kind in {"quebec_artist", "franchise"} and anchors and not _children_marker_match(combined, anchors):
                    continue
                try:
                    views = int((video.get("statistics") or {}).get("viewCount") or 0)
                except Exception:
                    views = 0
                minimum = min_views_local if kind.startswith("quebec") else min_views_generic
                if views < minimum:
                    continue
                title, parsed_artist = clean_video_title(raw_title)
                artist = parsed_artist or channel
                if not title or not artist:
                    continue
                seen_ids.add(vid)
                # View count is global, but discovery is a Canada-region, child-specific query.
                score = min(100.0, 45.0 + (20.0 if kind.startswith("quebec") else 10.0) + min(35.0, max(0.0, math.log10(max(views, 1)) * 5.0)))
                rows.append(SongRow(
                    title=title,
                    main_artist=artist,
                    genres=["children's music"],
                    languages=[lang if lang in {"fr", "en"} else "und"],
                    metric_name="youtube_views",
                    metric_value=float(views),
                    metric_unit="views",
                    view_count=views,
                    overall_popularity_score=score,
                    source_url=f"https://www.youtube.com/watch?v={vid}",
                    retrieved_at=TODAY,
                    source_notes=(
                        "Discovered by a Canada-region YouTube Music search restricted to the current 10-year Quebec childhood window; "
                        "safeSearch=strict and child-specific query."
                    ),
                    extra={
                        "culture_category": "children_childhood",
                        "children_policy": "quebec_age_0_12_last_10_years",
                        "source": "youtube_ca_child_search",
                        "youtube_video_id": vid,
                        "youtube_query": term,
                        "youtube_query_kind": kind,
                        "youtube_region": "CA",
                        "observation_window_start_year": start_year,
                    },
                ))
    return rows


def _build_children_quebec(limit: int = 10_000) -> dict:
    policy = _load_children_policy()
    window_years = int(os.getenv("BEATHIT_CHILDREN_WINDOW_YEARS", str(policy.get("window_years") or 10)))
    start_year = date.today().year - max(1, window_years)
    path = DATA / "children_childhood" / "children_childhood_10000.csv"
    existing = read_rows(path) if path.exists() else []
    existing_qualified = _children_filter_existing(existing, policy, start_year)
    apple_candidates = _apple_children_candidates(policy, start_year)
    youtube_candidates = _youtube_children_candidates(policy, start_year)
    candidates = [*existing_qualified, *apple_candidates, *youtube_candidates]

    candidates.sort(
        key=lambda r: (float(r.overall_popularity_score or 0), float(r.metric_value or 0)),
        reverse=True,
    )
    out: list[SongRow] = []
    seen_text: set[tuple[str, str]] = set()
    seen_spotify: set[str] = set()
    seen_mbid: set[str] = set()
    seen_isrc: set[str] = set()
    seen_youtube: set[str] = set()
    seen_itunes: set[str] = set()
    for row in candidates:
        key = (norm(row.title), norm(row.main_artist))
        sid = str(row.spotify_track_id or "").casefold()
        mbid = str(row.musicbrainz_recording_mbid or "").casefold()
        isrc = str(row.isrc or "").casefold()
        extra = row.extra or {}
        yid = str(extra.get("youtube_video_id") or "").casefold()
        iid = str(extra.get("itunes_track_id") or "").casefold()
        if not all(key) or key in seen_text:
            continue
        if sid and sid in seen_spotify:
            continue
        if mbid and mbid in seen_mbid:
            continue
        if isrc and isrc in seen_isrc:
            continue
        if yid and yid in seen_youtube:
            continue
        if iid and iid in seen_itunes:
            continue
        seen_text.add(key)
        if sid: seen_spotify.add(sid)
        if mbid: seen_mbid.add(mbid)
        if isrc: seen_isrc.add(isrc)
        if yid: seen_youtube.add(yid)
        if iid: seen_itunes.add(iid)
        out.append(row)
        if len(out) >= limit:
            break

    for rank, row in enumerate(out, 1):
        row.rank = rank
    write_rows(out, path)
    return {
        "target": limit,
        "materialized_rows": len(out),
        "complete": len(out) > 9_000,
        "builder_revision": "quebec-children-v1",
        "metric_coverage": {},
        "notes": [
            f"Quebec age-0-12 childhood scope; observation window {start_year}-{date.today().year}.",
            "Global generic children's-music rows are not accepted without curated Quebec/franchise exposure evidence.",
            "Primary discovery sources: Canadian Apple storefront and Canada-region YouTube Music child queries; no fabricated quota padding.",
            f"existing_qualified={len(existing_qualified)}; apple_candidates={len(apple_candidates)}; youtube_candidates={len(youtube_candidates)}",
        ],
    }


def _build_core(target: str) -> dict:
    if target == "children_childhood":
        return _build_children_quebec()
    if target == "countries":
        import build_remaining_core
        build_remaining_core._install_country_deep_history()

    fullbuild.full_build(
        skip_zenodo=os.getenv("BEATHIT_SKIP_ZENODO", "0") == "1",
        only=[target],
        reuse_complete=True,
    )

    status = read_json(ROOT / "STATUS.json")
    return dict((status.get("datasets") or {}).get(target) or {})



def _build_language_cover_discovery(target: str, deadline: float, request_budget: int) -> dict:
    """Discover cover candidates without mislabelling them as verified covers."""
    from cover_discovery import discover
    return discover(target.removeprefix("cover_"), deadline, request_budget)


def _build_expansion(target: str) -> dict:
    import build_expansion_lists as expansion

    expansion.CACHE.mkdir(parents=True, exist_ok=True)
    seconds = int(os.getenv("BEATHIT_WORKER_MAX_SECONDS", "19000"))
    deadline = time.monotonic() + max(60, seconds)

    if target == "per_game_vgm":
        return expansion.build_per_game_vgm(deadline, int(os.getenv("BEATHIT_PER_GAME_MAX_GAMES", "3162")))
    if target == "vocaloid_covers":
        return expansion.build_vocaloid_covers(deadline, int(os.getenv("BEATHIT_VOCALOID_COVER_REQUESTS", "50000")))
    if target.startswith("cover_"):
        slug = target.removeprefix("cover_")
        if slug not in expansion.LANGUAGE_COVERS:
            raise ValueError(f"Unknown language cover target: {slug}")
        return _build_language_cover_discovery(
            target,
            deadline,
            int(os.getenv("BEATHIT_COVER_YOUTUBE_SEARCHES", "60")),
        )
    raise ValueError(target)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ALL_TARGETS:
        print("usage: build_list.py <target>", file=sys.stderr)
        return 2
    target = sys.argv[1]
    try:
        dataset = _build_core(target) if target in {"anime", "vocaloid", "video_game_music", "children_childhood", "countries"} else _build_expansion(target)
        normalized = {
            "target": dataset.get("target"),
            "materialized_rows": int(dataset.get("materialized_rows", dataset.get("rows", 0)) or 0),
            "complete": bool(dataset.get("complete")),
            "builder_revision": "parallel-workers-v1",
            "metric_coverage": dataset.get("metric_coverage") or {},
            "notes": dataset.get("notes") or [json.dumps({k: v for k, v in dataset.items() if k not in {"target", "rows", "materialized_rows", "complete"}}, ensure_ascii=False)],
        }
        _status_fragment(target, normalized)
        print(json.dumps({"target": target, "dataset": normalized}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        _status_fragment(target, {}, build_error=f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())

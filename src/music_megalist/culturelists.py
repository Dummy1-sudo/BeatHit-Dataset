from __future__ import annotations

import ast
import html
import json
import math
import os
import re
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import httpx
import pandas as pd
from rapidfuzz import fuzz

from .dedupe import norm
from .io import read_rows, write_rows
from .models import SongRow

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CACHE = ROOT / ".cache" / "full_build"
TODAY = date.today().isoformat()
LISTENBRAINZ_API = "https://api.listenbrainz.org/1"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_SPARQL_FALLBACK = "https://query.wikidata.org/bigdata/namespace/wdq/sparql"
WIKIDATA_QLEVER = "https://qlever.dev/api/wikidata"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"

TAG_LISTS = {
    "internet_native": [
        "meme", "internet meme", "nerdcore", "nightcore", "vaporwave", "future funk",
        "hyperpop", "digicore", "glitchcore", "webcore", "scenecore", "phonk",
        "drift phonk", "breakcore", "chiptune", "bitpop", "otacore", "speedcore",
        "happy hardcore", "doujin", "denpa",
    ],
    "electronic_subcultures": [
        "ambient", "dark ambient", "drone", "idm", "glitch", "breakbeat", "breakcore",
        "drum and bass", "jungle", "liquid funk", "neurofunk", "dubstep", "brostep",
        "future garage", "uk garage", "2-step", "grime", "house", "deep house",
        "acid house", "tech house", "progressive house", "electro house", "trance",
        "goa trance", "psytrance", "hardstyle", "gabber", "hardcore techno",
        "industrial techno", "minimal techno", "detroit techno", "electro",
        "synthwave", "darksynth", "vaporwave", "future funk", "witch house",
        "chillwave", "downtempo", "trip hop", "ebm", "electro-industrial",
    ],
    "alternative_extreme": [
        "alternative rock", "indie rock", "post-punk", "gothic rock", "darkwave",
        "coldwave", "shoegaze", "dream pop", "noise rock", "no wave", "math rock",
        "post-rock", "emo", "screamo", "hardcore punk", "crust punk", "d-beat",
        "anarcho-punk", "grindcore", "powerviolence", "metalcore", "deathcore",
        "black metal", "death metal", "doom metal", "sludge metal", "drone metal",
        "thrash metal", "speed metal", "industrial metal", "avant-garde metal",
        "progressive metal", "noise", "harsh noise", "power electronics",
        "industrial", "neofolk", "psychobilly", "horror punk",
    ],
    "jazz_depth": [
        "jazz", "bebop", "hard bop", "cool jazz", "modal jazz", "free jazz",
        "avant-garde jazz", "spiritual jazz", "jazz fusion", "jazz funk",
        "acid jazz", "nu jazz", "smooth jazz", "vocal jazz", "big band",
        "swing", "ragtime", "stride", "dixieland", "new orleans jazz",
        "gypsy jazz", "latin jazz", "afro-cuban jazz", "bossa nova",
        "third stream", "jazz piano", "jazz trumpet", "jazz saxophone",
    ],
    "children_childhood": [
        "children's music", "children", "kids", "nursery rhyme", "nursery rhymes",
        "lullaby", "educational music", "sing-along", "preschool", "family music",
        "cartoon music", "sesame street",
    ],
    "unserious": [
        "novelty", "novelty song", "comedy", "comedy rock", "musical comedy",
        "comedy rap", "comedy hip hop", "parody", "song parody", "meme",
        "internet meme", "party novelty", "funny", "humorous", "satire",
        "absurdist", "silly music", "filk", "geek rock", "children's novelty",
        "denpa song",
    ],
}

TAG_TARGETS = {
    "internet_native": 1_000,
    "electronic_subcultures": 1_000,
    "alternative_extreme": 1_000,
    "jazz_depth": 1_000,
    "children_childhood": 100,
    "unserious": 1_000,
}

TAG_OUTPUTS = {
    "internet_native": DATA / "internet_native" / "internet_native_1000.csv",
    "electronic_subcultures": DATA / "electronic_subcultures" / "electronic_subcultures_1000.csv",
    "alternative_extreme": DATA / "alternative_extreme" / "alternative_extreme_1000.csv",
    "jazz_depth": DATA / "jazz_depth" / "jazz_depth_1000.csv",
    "children_childhood": DATA / "children_childhood" / "children_childhood_100.csv",
    "unserious": DATA / "unserious" / "unserious_1000.csv",
}

UNSERIOUS_TITLE_PHRASES = (
    "pink fluffy unicorn",
    "nyan cat",
    "trololo",
    "what does the fox say",
    "peanut butter jelly",
    "badger badger",
    "llama song",
    "narwhals",
    "duck song",
    "gummy bear",
    "crazy frog",
    "banana phone",
    "hamster dance",
    "hamsterdance",
    "ultimate showdown",
    "potato song",
    "poop song",
    "fart song",
    "skibidi",
)

REQUIRED_SPECIAL = [
    {
        "title": "Beethoven Virus",
        "main_artist": "BanYa",
        "languages": ["zxx"],
        "source_url": "https://musicbrainz.org/release/029e1725-8b4a-4a8f-8421-0c9c1c351835",
        "categories": ["special_required"],
        "note": "Explicit required inclusion. Official Pump It Up soundtrack release; instrumental.",
    },
    {
        "title": "The Pi Song (100 Digits of π)",
        "main_artist": "AsapSCIENCE",
        "languages": ["en"],
        "source_url": "https://www.youtube.com/watch?v=3HRkKznJoZA",
        "categories": ["special_required", "children_childhood", "unserious", "internet_native"],
        "note": "Explicit required educational internet-song inclusion.",
    },
    {
        "title": "SpongeBob SquarePants Theme",
        "main_artist": "SpongeBob SquarePants",
        "languages": ["en"],
        "source_url": "https://music.apple.com/us/song/323049096",
        "categories": ["special_required", "children_childhood", "unserious"],
        "note": "Explicit required television opening-theme inclusion.",
    },
    {
        "title": "Pink Fluffy Unicorns Dancing on Rainbows",
        "main_artist": "Andrew Huang",
        "languages": ["en"],
        "source_url": "https://www.youtube.com/watch?v=eWM2joNb9NE",
        "categories": ["special_required", "unserious", "internet_native"],
        "note": "Explicit required internet novelty-song inclusion.",
    },
    {
        "title": "Basique",
        "main_artist": "Orelsan",
        "languages": ["fr"],
        "source_url": "https://www.youtube.com/watch?v=2bjk26RwjyU",
        "categories": ["unserious"],
        "note": "Explicit user-requested inclusion for its absurd, deadpan comedic observations.",
    },
]

LANGUAGE_ALIASES = {
    "english": "en", "en": "en",
    "japanese": "ja", "ja": "ja",
    "korean": "ko", "ko": "ko",
    "spanish": "es", "es": "es",
    "french": "fr", "fr": "fr",
    "german": "de", "de": "de",
    "italian": "it", "it": "it",
    "portuguese": "pt", "pt": "pt",
    "russian": "ru", "ru": "ru",
    "arabic": "ar", "ar": "ar",
    "hindi": "hi", "hi": "hi",
    "punjabi": "pa", "pa": "pa",
    "chinese": "zh", "mandarin": "zh", "zh": "zh",
    "cantonese": "yue", "yue": "yue",
    "thai": "th", "th": "th",
    "vietnamese": "vi", "vi": "vi",
    "indonesian": "id", "id": "id",
    "instrumental": "zxx", "no linguistic content": "zxx", "zxx": "zxx",
    "unknown": "und", "undetermined": "und", "und": "und",
}


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        if not text or text.casefold() in {"nan", "none", "null", "<na>"}:
            return None
        return float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    number = _safe_float(value)
    return int(number) if number is not None else None


def _parse_genres(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip().casefold() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "null", "[]"}:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(x).strip().casefold() for x in parsed if str(x).strip()]
    except Exception:
        pass
    return [x.strip().casefold() for x in re.split(r"[|;,]", text) if x.strip()]



def _parse_clean_genres(value: Any) -> list[str]:
    """Flatten malformed nested-list genre fragments for strict category classifiers."""
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        if raw is None:
            return
        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                add(item)
            return

        text = str(raw).strip()
        if not text or text.casefold() in {"nan", "none", "null", "[]", "<na>"}:
            return

        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
            except Exception:
                continue
            if isinstance(parsed, (list, tuple, set)):
                add(parsed)
                return
            if isinstance(parsed, str) and parsed != text:
                add(parsed)
                return

        for part in re.split(r"[|;,]", text):
            cleaned = part.strip().strip("[](){}").strip().strip("'\"").strip().casefold()
            if cleaned and cleaned not in {"nan", "none", "null", "<na>"} and cleaned not in seen:
                seen.add(cleaned)
                out.append(cleaned)

    add(value)
    return out


def _normalize_languages(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(x) for x in value]
    else:
        text = str(value).strip()
        if not text or text.casefold() in {"nan", "none", "null", "<na>", "[]"}:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                raw = [str(x) for x in parsed]
            else:
                raw = [text]
        except Exception:
            raw = re.split(r"[|;,/]", text)
    out: list[str] = []
    for item in raw:
        token = item.strip()
        if not token:
            continue
        mapped = LANGUAGE_ALIASES.get(token.casefold(), token.casefold())
        if re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", mapped) and mapped not in out:
            out.append(mapped)
    return out


def catalog_languages(row: pd.Series) -> list[str]:
    """Use only explicit source language metadata; never infer lyric language from a title."""
    for key in ("languages", "language", "track_language", "lyrics_language", "vocal_language"):
        if key in row.index:
            values = _normalize_languages(row.get(key))
            if values:
                return values

    genres = _parse_genres(row.get("genres"))
    text = f"{row.get('title', '')} | {row.get('album_name', '')}".casefold()
    if "instrumental" in genres or re.search(r"\b(?:instrumental|karaoke|wordless|no vocals?)\b", text):
        return ["zxx"]
    return ["und"]


def _genre_matches(genres: Iterable[str], terms: Iterable[str]) -> bool:
    def clean(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

    normalized = [clean(str(g)) for g in genres if str(g).strip()]
    wanted = [clean(str(t)) for t in terms if str(t).strip()]
    for genre in normalized:
        padded = f" {genre} "
        for term in wanted:
            if genre == term:
                return True
            # Match a complete genre phrase inside a more specific source tag
            # (for example "jazz" inside "avant-garde jazz"), never arbitrary
            # short character substrings such as the old "ia" Vocaloid bug.
            if len(term) >= 4 and f" {term} " in padded:
                return True
    return False


def _unserious_title_match(value: Any) -> bool:
    text = norm(str(value or ""))
    return any(norm(phrase) in text for phrase in UNSERIOUS_TITLE_PHRASES)


def _catalog_score(row: pd.Series) -> float:
    streams = max(_safe_float(row.get("streams")) or 0.0, 0.0)
    views = max(_safe_float(row.get("youtube_views")) or 0.0, 0.0)
    popularity = max(_safe_float(row.get("popularity")) or 0.0, 0.0)
    track_score = max(_safe_float(row.get("track_score")) or 0.0, 0.0)
    daily = max(_safe_float(row.get("daily_streams")) or 0.0, 0.0)
    return (
        math.log10(streams + 1) * 10
        + math.log10(views + 1) * 7
        + popularity * 0.35
        + min(track_score, 1_000) * 0.02
        + math.log10(daily + 1) * 3
    )


def _catalog_metric(row: pd.Series) -> tuple[str, float, str, str]:
    streams = _safe_float(row.get("streams"))
    if streams is not None:
        return (
            str(row.get("streams_metric_name") or "spotify_streams"),
            streams,
            "streams",
            str(row.get("streams_source_url") or row.get("source_url") or "dataset source"),
        )
    views = _safe_float(row.get("youtube_views"))
    if views is not None:
        return (
            "youtube_views",
            views,
            "views",
            str(row.get("youtube_views_source_url") or row.get("source_url") or "dataset source"),
        )
    daily = _safe_float(row.get("daily_streams"))
    if daily is not None:
        return (
            "spotify_daily_streams",
            daily,
            "streams",
            str(row.get("daily_streams_source_url") or row.get("source_url") or "dataset source"),
        )
    popularity = _safe_float(row.get("popularity"))
    if popularity is not None:
        return (
            "spotify_popularity",
            popularity,
            "score_0_100",
            str(row.get("popularity_source_url") or row.get("source_url") or "dataset source"),
        )
    return "source_rank_score", 0.0, "score", str(row.get("source_url") or "dataset source")


def _catalog_song(
    row: pd.Series,
    *,
    extra: dict[str, Any] | None = None,
    force_metric: tuple[str, float, str, str] | None = None,
) -> SongRow:
    title = str(row.get("title") or "").strip()
    artist = str(row.get("main_artist") or row.get("artists") or "Unknown").strip()
    genres = _parse_genres(row.get("genres"))
    metric = force_metric or _catalog_metric(row)
    return SongRow(
        title=title,
        main_artist=artist,
        album=str(row.get("album_name") or "").strip() or None,
        release_date=str(row.get("release_date") or "").strip() or None,
        release_year=_safe_int(row.get("release_year")),
        genres=genres,
        languages=catalog_languages(row),
        metric_name=metric[0],
        metric_value=float(metric[1]),
        metric_unit=metric[2],
        listen_count=int(metric[1]) if metric[2] in {"streams", "listens"} else None,
        listen_source="Spotify" if metric[0].startswith("spotify_") and metric[2] == "streams" else None,
        view_count=int(metric[1]) if metric[2] == "views" else None,
        overall_popularity_score=_catalog_score(row),
        spotify_track_id=str(row.get("track_id") or "").strip() or None,
        isrc=str(row.get("isrc") or "").strip().upper() or None,
        source_url=metric[3],
        retrieved_at=TODAY,
        source_notes="Category membership comes from source genre/metadata tags; popularity evidence is preserved.",
        extra=extra or {},
    )


def _dedupe_rank(rows: list[SongRow], target: int | None = None) -> list[SongRow]:
    seen_text: set[tuple[str, str]] = set()
    seen_spotify: set[str] = set()
    seen_mbid: set[str] = set()
    seen_isrc: set[str] = set()
    out: list[SongRow] = []
    ordered = sorted(
        rows,
        key=lambda row: (
            float(row.overall_popularity_score or 0),
            1 if row.metric_unit in {"views", "streams", "listens"} else 0,
            float(row.metric_value),
        ),
        reverse=True,
    )
    for row in ordered:
        text_key = (norm(row.title), norm(row.main_artist))
        spotify = str(row.spotify_track_id or "").strip()
        mbid = str(row.musicbrainz_recording_mbid or "").strip().casefold()
        isrc = str(row.isrc or "").strip().casefold()
        if (
            not all(text_key)
            or text_key in seen_text
            or bool(spotify and spotify in seen_spotify)
            or bool(mbid and mbid in seen_mbid)
            or bool(isrc and isrc in seen_isrc)
        ):
            continue
        seen_text.add(text_key)
        if spotify:
            seen_spotify.add(spotify)
        if mbid:
            seen_mbid.add(mbid)
        if isrc:
            seen_isrc.add(isrc)
        out.append(row)
        if target is not None and len(out) >= target:
            break
    for rank, row in enumerate(out, 1):
        row.rank = rank
    return out


def _metric_counts(rows: Iterable[SongRow]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row.metric_name] += 1
    return dict(sorted(counts.items()))


def _seed_rows(category: str) -> list[SongRow]:
    rows: list[SongRow] = []
    for seed in REQUIRED_SPECIAL:
        if category not in seed["categories"]:
            continue
        rows.append(
            SongRow(
                title=seed["title"],
                main_artist=seed["main_artist"],
                languages=list(seed["languages"]),
                metric_name="curated_required_seed",
                metric_value=1.0,
                metric_unit="required_item",
                source_url=seed["source_url"],
                retrieved_at=TODAY,
                source_notes=seed["note"],
                extra={"culture_categories": list(seed["categories"]), "required_by_user": True},
            )
        )
    return rows


def _listenbrainz_tag_rows(
    tags: list[str],
    needed: int,
    category: str,
    used: set[tuple[str, str]],
    status: Any,
) -> list[SongRow]:
    if needed <= 0:
        return []
    rows: list[SongRow] = []
    with httpx.Client(
        timeout=60,
        follow_redirects=True,
        headers={"User-Agent": "BeatHit-Dataset/1.0"},
    ) as client:
        for tag in tags:
            if len(rows) >= needed:
                break
            try:
                response = client.get(
                    f"{LISTENBRAINZ_API}/lb-radio/tags",
                    params={"tag": tag, "operator": "OR", "count": 1000, "pop_begin": 0, "pop_end": 100},
                )
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                status.warnings.append(f"ListenBrainz {category} tag {tag}: {exc}")
                continue

            payload = data.get("payload", data) if isinstance(data, dict) else data
            tracks = (
                payload.get("jspf", {}).get("playlist", {}).get("track", [])
                if isinstance(payload, dict)
                else []
            )
            if not tracks and isinstance(payload, list):
                tracks = payload

            for position, item in enumerate(tracks, 1):
                title = item.get("title") or item.get("track_name") or item.get("recording_name")
                artist = item.get("creator") or item.get("artist_name") or item.get("artist_credit_name")
                if not title or not artist:
                    continue
                key = (norm(str(title)), norm(str(artist)))
                if key in used:
                    continue
                used.add(key)

                identifier = item.get("identifier") or item.get("recording_mbid")
                if isinstance(identifier, list):
                    identifier = identifier[0] if identifier else None
                if isinstance(identifier, str) and "/" in identifier:
                    identifier = identifier.rsplit("/", 1)[-1]

                rows.append(
                    SongRow(
                        title=str(title),
                        main_artist=str(artist),
                        genres=[tag],
                        languages=["und"],
                        metric_name="listenbrainz_tag_radio_rank",
                        metric_value=float(max(1, 1001 - position)),
                        metric_unit="rank_score",
                        musicbrainz_recording_mbid=identifier,
                        source_url="https://listenbrainz.org/",
                        retrieved_at=TODAY,
                        source_notes="ListenBrainz tag-radio popularity rank; no stream count is implied.",
                        extra={"culture_category": category, "source_tag": tag},
                    )
                )
                if len(rows) >= needed:
                    break
            time.sleep(0.12)
    return rows


def _build_tag_list(catalog: pd.DataFrame, status: Any, name: str) -> list[SongRow]:
    target = TAG_TARGETS[name]
    tags = TAG_LISTS[name]
    output = TAG_OUTPUTS[name]

    rows = _seed_rows(name)
    used = {(norm(row.title), norm(row.main_artist)) for row in rows}

    candidates: list[tuple[float, pd.Series, list[str]]] = []
    for _, row in catalog.iterrows():
        row_genres = _parse_genres(row.get("genres"))
        genre_match = _genre_matches(row_genres, tags)
        title_match = name == "unserious" and _unserious_title_match(row.get("title"))
        if genre_match or title_match:
            score = _catalog_score(row) + (10.0 if title_match else 0.0)
            candidates.append((score, row, row_genres))
    candidates.sort(key=lambda item: item[0], reverse=True)

    for _, row, row_genres in candidates:
        key = (
            norm(str(row.get("title") or "")),
            norm(str(row.get("main_artist") or row.get("artists") or "")),
        )
        if not all(key) or key in used:
            continue
        used.add(key)
        rows.append(
            _catalog_song(
                row,
                extra={
                    "culture_category": name,
                    "matched_source_genres": row_genres,
                    "matched_unserious_title_phrase": (
                        _unserious_title_match(row.get("title")) if name == "unserious" else False
                    ),
                    "selection": (
                        "strict novelty/comedy tag or known absurd novelty-title pattern; source-backed popularity ranking"
                        if name == "unserious"
                        else "source genre/tag membership plus source-backed popularity ranking"
                    ),
                },
            )
        )
        if len(rows) >= target:
            break

    if len(rows) < target:
        rows.extend(_listenbrainz_tag_rows(tags, target - len(rows), name, used, status))

    rows = _dedupe_rank(rows, target)
    write_rows(rows, output)
    st = status.datasets[name]
    st.rows = len(rows)
    st.complete = len(rows) == target
    st.metric_coverage = _metric_counts(rows)
    if name == "unserious":
        st.notes = [
            "Strict novelty/comedy/parody/meme tags plus a bounded set of known absurd novelty-title patterns.",
            "Broad viral, children's music, cartoon music, nerdcore, and denpa tags do not qualify by themselves.",
            "Explicit inclusions include Pink Fluffy Unicorns Dancing on Rainbows and Orelsan's Basique.",
            f"catalog_candidates={len(candidates)}; target={target}",
        ]
    else:
        st.notes = [
            "Source-backed catalog genres/tags first; ListenBrainz tag-radio fallback second.",
            "No fabricated quota padding. Unknown song language is stored as ['und'], not guessed from title text.",
            f"catalog_candidates={len(candidates)}; target={target}",
        ]
    status.save()
    return rows


def build_internet_native(catalog: pd.DataFrame, status: Any) -> list[SongRow]:
    return _build_tag_list(catalog, status, "internet_native")


def build_electronic_subcultures(catalog: pd.DataFrame, status: Any) -> list[SongRow]:
    return _build_tag_list(catalog, status, "electronic_subcultures")


def build_alternative_extreme(catalog: pd.DataFrame, status: Any) -> list[SongRow]:
    return _build_tag_list(catalog, status, "alternative_extreme")


def build_jazz_depth(catalog: pd.DataFrame, status: Any) -> list[SongRow]:
    return _build_tag_list(catalog, status, "jazz_depth")


def build_children_childhood(catalog: pd.DataFrame, status: Any) -> list[SongRow]:
    return _build_tag_list(catalog, status, "children_childhood")


def build_unserious(catalog: pd.DataFrame, status: Any) -> list[SongRow]:
    return _build_tag_list(catalog, status, "unserious")


def build_required_special(catalog: pd.DataFrame, status: Any) -> list[SongRow]:
    del catalog
    rows = _dedupe_rank(_seed_rows("special_required"), 4)
    write_rows(rows, DATA / "special_required" / "special_required.csv")
    st = status.datasets["special_required"]
    st.rows = len(rows)
    st.complete = len(rows) == 4
    st.metric_coverage = _metric_counts(rows)
    st.notes = [
        "Explicit required inclusions: Beethoven Virus, The Pi Song, SpongeBob SquarePants Theme, and Pink Fluffy Unicorns Dancing on Rainbows.",
        "The inclusion metric is not represented as a popularity count.",
    ]
    status.save()
    return rows


KPOP_SEARCH_QUERIES = [
    "K-pop official MV",
    "K-pop girl group official MV",
    "K-pop boy group official MV",
    "K-pop soloist official MV",
    "Korean pop official music video",
    "K-pop 2000s official MV",
    "K-pop 2010s official MV",
    "K-pop 2020s official MV",
    "K-pop debut official MV",
    "K-pop viral official MV",
]
KPOP_STRONG_GENRES = {
    "k-pop",
    "korean pop",
    "k-pop boy group",
    "k-pop girl group",
}
# Curated artist identities prevent YouTube search relevance from being mistaken for
# genre membership. The order is also the artist-specific search priority.
KPOP_CURATED_ARTISTS = [
    ("BTS", {"BTS", "방탄소년단", "BTS (방탄소년단)"}),
    ("BLACKPINK", {"BLACKPINK", "블랙핑크"}),
    ("TWICE", {"TWICE", "트와이스"}),
    ("PSY", {"PSY", "싸이"}),
    ("BIGBANG", {"BIGBANG", "빅뱅"}),
    ("EXO", {"EXO", "엑소"}),
    ("Red Velvet", {"Red Velvet", "레드벨벳"}),
    ("SEVENTEEN", {"SEVENTEEN", "세븐틴", "SVT"}),
    ("Stray Kids", {"Stray Kids", "스트레이 키즈", "SKZ"}),
    ("(G)I-DLE", {"(G)I-DLE", "GIDLE", "여자아이들"}),
    ("aespa", {"aespa", "에스파"}),
    ("IVE", {"IVE", "아이브"}),
    ("ITZY", {"ITZY", "있지"}),
    ("NCT 127", {"NCT 127", "엔시티 127"}),
    ("NCT DREAM", {"NCT DREAM", "엔시티 드림"}),
    ("NewJeans", {"NewJeans", "뉴진스"}),
    ("LE SSERAFIM", {"LE SSERAFIM", "르세라핌"}),
    ("TOMORROW X TOGETHER", {"TOMORROW X TOGETHER", "TXT", "투모로우바이투게더"}),
    ("ENHYPEN", {"ENHYPEN", "엔하이픈"}),
    ("ATEEZ", {"ATEEZ", "에이티즈"}),
    ("GOT7", {"GOT7", "갓세븐"}),
    ("iKON", {"iKON", "아이콘"}),
    ("WINNER", {"WINNER", "위너"}),
    ("MAMAMOO", {"MAMAMOO", "마마무"}),
    ("MOMOLAND", {"MOMOLAND", "모모랜드"}),
    ("EVERGLOW", {"EVERGLOW", "에버글로우"}),
    ("GFRIEND", {"GFRIEND", "여자친구"}),
    ("IZ*ONE", {"IZ*ONE", "IZONE", "아이즈원"}),
    ("2NE1", {"2NE1", "투애니원"}),
    ("Girls' Generation", {"Girls' Generation", "SNSD", "소녀시대"}),
    ("Super Junior", {"Super Junior", "슈퍼주니어"}),
    ("SHINee", {"SHINee", "샤이니"}),
    ("IU", {"IU", "아이유"}),
    ("Sunmi", {"Sunmi", "선미"}),
    ("HyunA", {"HyunA", "현아"}),
    ("Jessi", {"Jessi", "제시"}),
    ("ZICO", {"ZICO", "지코"}),
    ("KARD", {"KARD", "카드"}),
    ("Dreamcatcher", {"Dreamcatcher", "드림캐쳐"}),
    ("Kep1er", {"Kep1er", "케플러"}),
    ("NMIXX", {"NMIXX", "엔믹스"}),
    ("BABYMONSTER", {"BABYMONSTER", "베이비몬스터"}),
    ("TREASURE", {"TREASURE", "트레저"}),
    ("MONSTA X", {"MONSTA X", "몬스타엑스"}),
    ("Apink", {"Apink", "에이핑크"}),
    ("T-ARA", {"T-ARA", "티아라"}),
    ("4Minute", {"4Minute", "포미닛"}),
    ("Wonder Girls", {"Wonder Girls", "원더걸스"}),
    ("miss A", {"miss A", "미쓰에이"}),
    ("TAEMIN", {"TAEMIN", "태민"}),
    ("G-DRAGON", {"G-DRAGON", "GD", "지드래곤"}),
    ("TAEYANG", {"TAEYANG", "태양"}),
    ("CL", {"CL", "씨엘"}),
    ("JENNIE", {"JENNIE", "제니"}),
    ("LISA", {"LISA", "리사"}),
    ("ROSÉ", {"ROSÉ", "ROSE", "로제"}),
    ("HWASA", {"HWASA", "화사"}),
    ("LeeHi", {"LeeHi", "LEE HI", "이하이"}),
    ("SHAUN", {"SHAUN", "숀"}),
]
KPOP_REJECT_TITLE = re.compile(
    r"\b(?:reaction|dance practice|dance cover|cover|lyrics?|karaoke|sped up|slowed|"
    r"nightcore|remix|teaser|trailer|shorts?|fanmade|fancam|instrumental|"
    r"performance|live(?:\s+clip)?|stage|audio|visualizer|choreography|behind)\b",
    re.I,
)
KPOP_OFFICIAL_MARKER = re.compile(
    r"(?:\bofficial\b.*\b(?:m/?v|music video|video)\b|\b(?:m/?v|music video)\b)",
    re.I,
)
KPOP_LABEL_CHANNEL = re.compile(
    r"(?:HYBE LABELS|SMTOWN|JYP Entertainment|YG ENTERTAINMENT|"
    r"1theK|Mnet K-POP|Stone Music Entertainment|STARSHIP|KQ ENTERTAINMENT|"
    r"CUBE ENTERTAINMENT|RBW|PLEDIS|SOURCE MUSIC|ADOR|BELIFT|BIGHIT MUSIC)",
    re.I,
)


def _kpop_primary_artist_key(value: Any) -> str:
    text = html.unescape(str(value or "")).strip()
    text = re.sub(r"^official\s*", "", text, flags=re.I)
    text = re.sub(r"\s*(?:official|vevo|tv)$", "", text, flags=re.I)
    text = re.sub(r"\(\s*[가-힣\s]+\s*\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return norm(text)


def _kpop_artist_aliases(value: Any) -> set[str]:
    text = html.unescape(str(value or "")).strip()
    if not text:
        return set()
    variants = {
        norm(text),
        _kpop_primary_artist_key(text),
        norm(re.sub(r"[가-힣]+", " ", text)),
        norm(re.sub(r"\([^)]*\)", " ", text)),
    }
    return {variant for variant in variants if variant}


def _build_trusted_kpop_artists(catalog: pd.DataFrame) -> tuple[dict[str, str], list[str]]:
    tagged: list[tuple[pd.Series, str, str]] = []
    for _, row in catalog.iterrows():
        genres = set(_parse_clean_genres(row.get("genres")))
        if not genres.intersection(KPOP_STRONG_GENRES):
            continue
        artist = str(row.get("main_artist") or row.get("artists") or "").strip()
        primary = _kpop_primary_artist_key(artist)
        if not primary:
            continue
        tagged.append((row, artist, primary))

    trusted_primary: set[str] = set()
    canonical: dict[str, str] = {}
    best_score: dict[str, float] = {}
    for row, artist, primary in tagged:
        isrc = str(row.get("isrc") or "").strip().upper()
        country_values = {
            str(row.get(field) or "").strip().casefold()
            for field in ("country", "artist_country", "release_country", "market")
        }
        korean_metadata = bool(
            isrc.startswith("KR")
            or country_values.intersection({"kr", "kor", "south korea", "republic of korea"})
            or re.search(r"[가-힣]", artist)
        )
        if not korean_metadata:
            continue
        trusted_primary.add(primary)
        score = _catalog_score(row)
        if primary not in canonical or score > best_score.get(primary, -1.0):
            canonical[primary] = artist
            best_score[primary] = score

    aliases: dict[str, str] = {}
    for row, artist, primary in tagged:
        if primary not in trusted_primary:
            continue
        name = canonical[primary]
        for alias in _kpop_artist_aliases(artist) | _kpop_artist_aliases(name):
            aliases.setdefault(alias, name)

    # Curated identities override noisy catalog spellings and supply artists whose source
    # rows omit Korean ISRC/country metadata. No song is added by this registry alone:
    # every output still needs an observed >100M YouTube count and official-video evidence.
    curated_ranked: list[str] = []
    for canonical_name, source_aliases in KPOP_CURATED_ARTISTS:
        curated_ranked.append(canonical_name)
        for alias_value in set(source_aliases) | {canonical_name}:
            for alias in _kpop_artist_aliases(alias_value):
                aliases[alias] = canonical_name

    ranked_primary = sorted(trusted_primary, key=lambda value: best_score.get(value, 0.0), reverse=True)
    ranked = list(curated_ranked)
    seen_ranked = {_kpop_primary_artist_key(value) for value in ranked}
    for primary in ranked_primary:
        name = canonical[primary]
        key = _kpop_primary_artist_key(name)
        if key not in seen_ranked:
            seen_ranked.add(key)
            ranked.append(name)
    return aliases, ranked


def _match_trusted_kpop_artist(value: Any, trusted_aliases: dict[str, str]) -> str | None:
    for alias in _kpop_artist_aliases(value):
        if alias in trusted_aliases:
            return trusted_aliases[alias]
    return None


def _kpop_title_key(value: Any) -> str:
    text = html.unescape(str(value or "")).strip(" \t'\"“”‘’")
    text = re.sub(r"\s*\((?:feat\.?|ft\.?)\s+[^)]*\)", "", text, flags=re.I)
    parenthetical = [
        part.strip()
        for part in re.findall(r"\(([^)]{2,})\)", text)
        if re.search(r"[A-Za-z]", part)
        and not re.search(r"\b(?:official|version|ver\.?|remix|live|performance)\b", part, re.I)
    ]
    outside = re.sub(r"\([^)]*\)", " ", text)
    if re.search(r"[가-힣]", outside) and parenthetical:
        text = parenthetical[0]
    else:
        text = outside
    text = re.sub(r"\b(?:official\s*)?(?:m/?v|music video)\b.*$", "", text, flags=re.I)
    return norm(text)


def _dedupe_kpop_rows(rows: list[SongRow], target: int = 10_000) -> list[SongRow]:
    ordered = sorted(
        rows,
        key=lambda row: int(row.view_count or row.metric_value or 0),
        reverse=True,
    )
    out: list[SongRow] = []
    seen_songs: set[tuple[str, str]] = set()
    seen_videos: set[str] = set()
    for row in ordered:
        song_key = (_kpop_title_key(row.title), _kpop_primary_artist_key(row.main_artist))
        video_id = str((row.extra or {}).get("youtube_video_id") or "").strip()
        if not all(song_key) or song_key in seen_songs or (video_id and video_id in seen_videos):
            continue
        seen_songs.add(song_key)
        if video_id:
            seen_videos.add(video_id)
        out.append(row)
        if len(out) >= target:
            break
    for rank, row in enumerate(out, 1):
        row.rank = rank
    return out


def _parse_kpop_video_identity(raw_title: str, channel_title: str) -> tuple[str, str]:
    title = html.unescape(str(raw_title or "")).strip()
    title = re.sub(r"^\s*(?:\[(?:mv|m/v|official(?:\s+video)?)\]\s*)+", "", title, flags=re.I)
    core = re.sub(
        r"\s*(?:\[(?:official[^\]]*|m/?v|music video)[^\]]*\]|"
        r"\((?:official[^)]*|m/?v|music video)[^)]*\)|"
        r"\b(?:official\s*)?(?:m/?v|music video|performance video)\b.*)$",
        "",
        title,
        flags=re.I,
    ).strip(" \t-–—|:")
    if " - " in core:
        artist, song = core.split(" - ", 1)
    elif " – " in core:
        artist, song = core.split(" – ", 1)
    elif " — " in core:
        artist, song = core.split(" — ", 1)
    elif " _ " in core:
        artist, song = core.split(" _ ", 1)
    else:
        quoted = re.match(r"^(.+?)\s+['\"“‘](.+?)['\"”’]\s*$", core)
        if quoted:
            artist, song = quoted.group(1), quoted.group(2)
        else:
            artist = re.sub(r"\s+(?:official|오피셜)$", "", channel_title, flags=re.I).strip()
            song = core or title
    artist = re.sub(r"^\[(?:mv|m/v)\]\s*", "", artist, flags=re.I).strip(" \t-–—|:'\"“”‘’")
    song = song.strip(" \t-–—|:'\"“”‘’")
    return song or title, artist or channel_title or "Unknown K-pop artist"


def _youtube_kpop_rows(
    status: Any,
    threshold: int,
    trusted_aliases: dict[str, str],
    trusted_artists: list[str],
) -> tuple[list[SongRow], bool]:
    """Resume a bounded, registry-wide official-video scan.

    YouTube ``search.list`` is expensive. Successful artist queries and the resulting
    ``videos.list`` snapshots are checkpointed after every request so a quota-limited
    workflow continues with the first unfinished artist instead of repeating prior work.
    """
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        status.warnings.append("K-pop YouTube discovery skipped: missing YOUTUBE_API_KEY")
        status.sources["youtube_kpop_discovery"] = {
            "source": "YouTube Data API v3 search.list + videos.list",
            "ok": False,
            "error": "missing YOUTUBE_API_KEY",
        }
        return [], False

    cache_path = CACHE / "kpop_youtube_checkpoint.json"
    now = int(time.time())
    detail_ttl = max(
        1,
        min(_safe_int(os.getenv("BEATHIT_KPOP_VIEW_CACHE_DAYS")) or 7, 30),
    ) * 86_400
    cache: dict[str, Any] = {
        "schema_version": 2,
        "queries": {},
        "videos": {},
    }
    try:
        if cache_path.exists():
            value = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("schema_version") == 2:
                cache = value
    except Exception as exc:
        status.warnings.append(f"K-pop YouTube checkpoint ignored: {exc}")

    query_cache = cache.setdefault("queries", {})
    video_cache = cache.setdefault("videos", {})
    if not isinstance(query_cache, dict) or not isinstance(video_cache, dict):
        query_cache = {}
        video_cache = {}
        cache = {"schema_version": 2, "queries": query_cache, "videos": video_cache}

    def save_cache() -> None:
        CACHE.mkdir(parents=True, exist_ok=True)
        cache["updated_at"] = now
        temp_path = cache_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(cache, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(cache_path)

    # Artist-specific queries are substantially cleaner than broad "K-pop" searches.
    # Optional generic queries remain available for an audited expansion pass, but the
    # default budget is devoted to every catalog/curation-verified artist identity.
    artist_queries = list(
        dict.fromkeys(f'"{artist}" official MV' for artist in trusted_artists if artist)
    )
    generic_limit = max(
        0,
        min(
            _safe_int(os.getenv("BEATHIT_KPOP_GENERIC_SEARCHES")) or 0,
            len(KPOP_SEARCH_QUERIES),
        ),
    )
    search_plan = artist_queries + KPOP_SEARCH_QUERIES[:generic_limit]
    search_call_budget = max(
        1,
        # 59 search calls leave enough of the default daily YouTube quota for
        # the exhaustive Vocaloid videos.list pass in the same workflow.
        min(_safe_int(os.getenv("BEATHIT_KPOP_SEARCH_CALL_BUDGET")) or 59, 59),
    )

    discovered: dict[str, dict[str, Any]] = {}
    search_calls = 0
    failed_queries: list[str] = []
    with httpx.Client(
        timeout=60,
        follow_redirects=True,
        headers={
            "User-Agent": (
                "BeatHit-Dataset/1.0 "
                "(+https://github.com/Dummy1-sudo/BeatHit-Dataset)"
            )
        },
    ) as client:
        for query in search_plan:
            cached_query = query_cache.get(query)
            if isinstance(cached_query, dict) and cached_query.get("complete"):
                cached_videos = cached_query.get("videos") or {}
                if isinstance(cached_videos, dict):
                    for video_id, snippet in cached_videos.items():
                        if video_id:
                            discovered.setdefault(
                                str(video_id),
                                {"query": query, "search_snippet": snippet or {}},
                            )
                continue
            if search_calls >= search_call_budget:
                continue

            params = {
                "part": "snippet",
                "q": query,
                "type": "video",
                "videoCategoryId": "10",
                "order": "viewCount",
                "maxResults": "50",
                "regionCode": "KR",
                "safeSearch": "none",
                "key": key,
            }
            try:
                response = client.get(f"{YOUTUBE_API}/search", params=params)
                search_calls += 1
                if response.is_error:
                    raise RuntimeError(
                        f"HTTP {response.status_code}: {response.text[:800]}"
                    )
                data = response.json()
            except Exception as exc:
                failed_queries.append(query)
                status.warnings.append(f"K-pop YouTube search {query!r}: {exc}")
                if "quota" in str(exc).casefold():
                    break
                continue

            query_videos: dict[str, Any] = {}
            for item in data.get("items", []) or []:
                video_id = str((item.get("id") or {}).get("videoId") or "").strip()
                if video_id:
                    snippet = item.get("snippet") or {}
                    query_videos[video_id] = snippet
                    discovered.setdefault(
                        video_id,
                        {"query": query, "search_snippet": snippet},
                    )
            query_cache[query] = {
                "complete": True,
                "checked_at": now,
                "videos": query_videos,
            }
            save_cache()
            time.sleep(0.05)

        video_ids = list(discovered)
        pending_video_ids: list[str] = []
        for video_id in video_ids:
            detail = video_cache.get(video_id)
            if not isinstance(detail, dict):
                pending_video_ids.append(video_id)
                continue
            checked_at = _safe_int(detail.get("checked_at")) or 0
            if not checked_at or now - checked_at > detail_ttl:
                pending_video_ids.append(video_id)

        failed_stat_batches = 0
        quota_stopped = False
        for start in range(0, len(pending_video_ids), 50):
            batch = pending_video_ids[start:start + 50]
            try:
                response = client.get(
                    f"{YOUTUBE_API}/videos",
                    params={
                        "part": "snippet,statistics",
                        "id": ",".join(batch),
                        "key": key,
                    },
                )
                if response.is_error:
                    raise RuntimeError(
                        f"HTTP {response.status_code}: {response.text[:800]}"
                    )
                items = response.json().get("items", []) or []
            except Exception as exc:
                failed_stat_batches += 1
                status.warnings.append(
                    f"K-pop YouTube statistics batch={start // 50 + 1}: {exc}"
                )
                if "quota" in str(exc).casefold():
                    quota_stopped = True
                    break
                continue

            returned: dict[str, Any] = {}
            for item in items:
                video_id = str(item.get("id") or "").strip()
                if video_id:
                    returned[video_id] = {
                        "checked_at": now,
                        "exists": True,
                        "snippet": item.get("snippet") or {},
                        "view_count": _safe_int(
                            (item.get("statistics") or {}).get("viewCount")
                        ),
                    }
            for video_id in batch:
                video_cache[video_id] = returned.get(
                    video_id,
                    {
                        "checked_at": now,
                        "exists": False,
                        "snippet": (
                            (discovered.get(video_id) or {}).get("search_snippet") or {}
                        ),
                        "view_count": None,
                    },
                )
            save_cache()

        rows: list[SongRow] = []
        for video_id in video_ids:
            detail = video_cache.get(video_id)
            if not isinstance(detail, dict):
                continue
            views = _safe_int(detail.get("view_count"))
            if views is None or views <= threshold:
                continue
            snippet = detail.get("snippet") or {}
            raw_title = html.unescape(str(snippet.get("title") or "")).strip()
            channel_title = html.unescape(
                str(snippet.get("channelTitle") or "")
            ).strip()
            if KPOP_REJECT_TITLE.search(raw_title):
                continue

            title, parsed_artist = _parse_kpop_video_identity(raw_title, channel_title)
            artist = _match_trusted_kpop_artist(parsed_artist, trusted_aliases)
            if artist is None:
                continue

            channel_matches_artist = bool(
                _kpop_artist_aliases(channel_title).intersection(
                    _kpop_artist_aliases(artist)
                )
            )
            official = bool(
                KPOP_LABEL_CHANNEL.search(channel_title) or channel_matches_artist
            )
            if not official or not KPOP_OFFICIAL_MARKER.search(raw_title):
                continue

            rows.append(
                SongRow(
                    title=title,
                    main_artist=artist,
                    genres=["k-pop"],
                    languages=["und"],
                    metric_name="youtube_views",
                    metric_value=float(views),
                    metric_unit="views",
                    view_count=views,
                    overall_popularity_score=math.log10(views + 1) * 10,
                    source_url=f"https://www.youtube.com/watch?v={video_id}",
                    retrieved_at=TODAY,
                    source_notes=(
                        "Discovered with the official YouTube Data API and retained "
                        "only when the parsed artist matches the audited K-pop artist "
                        "registry and the upload has official-video evidence."
                    ),
                    extra={
                        "culture_category": "kpop",
                        "youtube_video_id": video_id,
                        "youtube_channel_id": snippet.get("channelId"),
                        "youtube_channel_title": channel_title,
                        "youtube_original_title": raw_title,
                        "youtube_discovery_query": (
                            discovered.get(video_id) or {}
                        ).get("query"),
                        "youtube_view_threshold_strictly_greater_than": threshold,
                        "kpop_artist_verified": True,
                        "verified_kpop_artist": artist,
                        "selection": (
                            "official YouTube API discovery restricted to the "
                            "audited K-pop artist registry"
                        ),
                    },
                )
            )

    save_cache()
    completed_queries = sum(
        1
        for query in search_plan
        if isinstance(query_cache.get(query), dict)
        and query_cache[query].get("complete")
    )
    unresolved_statistics = sum(
        1
        for video_id in discovered
        if not isinstance(video_cache.get(video_id), dict)
        or not (_safe_int(video_cache[video_id].get("checked_at")) or 0)
    )
    registry_complete = bool(
        search_plan
        and completed_queries == len(search_plan)
        and not failed_queries
        and not failed_stat_batches
        and not quota_stopped
        and unresolved_statistics == 0
    )
    status.sources["youtube_kpop_discovery"] = {
        "source": "YouTube Data API v3 search.list + videos.list",
        "planned_artist_queries": len(artist_queries),
        "optional_generic_queries": generic_limit,
        "completed_queries": completed_queries,
        "live_search_calls": search_calls,
        "search_call_budget": search_call_budget,
        "unique_video_candidates": len(discovered),
        "qualified_rows": len(rows),
        "failed_queries": failed_queries,
        "failed_statistics_batches": failed_stat_batches,
        "unresolved_statistics": unresolved_statistics,
        "registry_scan_complete": registry_complete,
        "threshold": threshold,
        "checkpoint": str(cache_path.relative_to(ROOT)),
        "ok": registry_complete,
    }
    return rows, registry_complete


def build_kpop_youtube_100m(catalog: pd.DataFrame, status: Any) -> list[SongRow]:
    """Build verified K-pop tracks with observed YouTube views strictly above 100M."""
    threshold = 100_000_000
    trusted_aliases, trusted_artists = _build_trusted_kpop_artists(catalog)

    output_path = DATA / "kpop" / "kpop_youtube_over_100m.csv"
    rows: list[SongRow] = []

    # A quota-limited refresh must never erase previously verified canonical rows.
    if output_path.exists():
        try:
            for existing in read_rows(output_path):
                extra = existing.extra or {}
                if (
                    existing.metric_name == "youtube_views"
                    and existing.metric_unit == "views"
                    and int(existing.view_count or existing.metric_value or 0) > threshold
                    and bool(extra.get("kpop_artist_verified"))
                    and str(extra.get("verified_kpop_artist") or "").strip()
                ):
                    rows.append(existing)
        except Exception as exc:
            status.warnings.append(f"K-pop existing output was not reused: {exc}")
    preserved_rows = len(rows)

    for _, row in catalog.iterrows():
        row_genres = _parse_clean_genres(row.get("genres"))
        if not set(row_genres).intersection(KPOP_STRONG_GENRES):
            continue
        canonical_artist = _match_trusted_kpop_artist(
            row.get("main_artist") or row.get("artists"),
            trusted_aliases,
        )
        if canonical_artist is None:
            continue
        views = _safe_int(row.get("youtube_views"))
        if views is None or views <= threshold:
            continue
        url = str(row.get("youtube_views_source_url") or row.get("source_url") or "").strip()
        if not url:
            continue
        song = _catalog_song(
            row,
            force_metric=("youtube_views", float(views), "views", url),
            extra={
                "culture_category": "kpop",
                "youtube_view_threshold_strictly_greater_than": threshold,
                "matched_source_genres": row_genres,
                "kpop_artist_verified": True,
                "verified_kpop_artist": canonical_artist,
                "selection": "catalog K-pop tag plus Korean ISRC, Korean source-country metadata, or Korean artist identity",
            },
        )
        song.main_artist = canonical_artist
        rows.append(song)

    catalog_rows = len(rows) - preserved_rows
    youtube_rows, registry_complete = _youtube_kpop_rows(
        status,
        threshold,
        trusted_aliases,
        trusted_artists,
    )
    rows.extend(youtube_rows)
    rows = _dedupe_kpop_rows(rows, 10_000)
    write_rows(rows, output_path)
    st = status.datasets["kpop"]
    st.rows = len(rows)
    st.complete = registry_complete
    st.metric_coverage = _metric_counts(rows)
    st.notes = [
        "Catalog rows require an exact cleaned K-pop genre plus Korean ISRC, Korean source-country metadata, or Korean artist identity.",
        "YouTube discovery accepts only parsed artists in the audited catalog/curation-backed artist registry; generic Western-pop results are rejected.",
        "Artist aliases and Korean/Latin title variants are canonicalized before deduplication.",
        "Every retained row has an observed YouTube view count strictly above 100,000,000.",
        "Successful artist searches and video-statistics snapshots are checkpointed; a quota-limited rerun resumes at the first unfinished artist and preserves verified rows.",
        f"trusted_artists={len(trusted_artists)}; preserved_rows={preserved_rows}; catalog_qualified={catalog_rows}; live_youtube_qualified={len(youtube_rows)}; final_deduplicated_rows={len(rows)}",
        "Completeness is scoped to the fully scanned audited artist registry; it is not a claim that every upload labeled K-pop on the internet has been enumerated.",
    ]
    status.save()
    return rows


def _fetch_top_games(status: Any, limit: int = 1_000) -> list[dict[str, Any]]:
    cache_path = CACHE / "wikidata_top_video_games.json"
    query = f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX wikibase: <http://wikiba.se/ontology#>
SELECT ?game ?gameLabel ?sitelinks WHERE {{
  ?game wdt:P31 wd:Q7889 ;
        wikibase:sitelinks ?sitelinks ;
        rdfs:label ?gameLabel .
  FILTER(LANG(?gameLabel) = "en")
}}
ORDER BY DESC(?sitelinks)
LIMIT {int(limit)}
"""
    bindings: list[dict[str, Any]] = []
    successful_endpoint = ""
    errors: list[str] = []
    attempts = [
        ("GET", WIKIDATA_SPARQL),
        ("POST", WIKIDATA_SPARQL),
        ("POST", WIKIDATA_QLEVER),
        ("POST", WIKIDATA_SPARQL_FALLBACK),
    ]
    with httpx.Client(
        timeout=120,
        follow_redirects=True,
        headers={
            "User-Agent": (
                "BeatHit-Dataset/1.0 "
                "(+https://github.com/Dummy1-sudo/BeatHit-Dataset)"
            )
        },
    ) as client:
        for method, endpoint in attempts:
            try:
                if method == "POST":
                    response = client.post(
                        endpoint,
                        data={"query": query, "format": "json"},
                        headers={"Accept": "application/sparql-results+json"},
                    )
                else:
                    response = client.get(
                        endpoint,
                        params={"query": query, "format": "json"},
                        headers={"Accept": "application/sparql-results+json"},
                    )
                response.raise_for_status()
                bindings = response.json().get("results", {}).get("bindings", [])
                if bindings:
                    successful_endpoint = f"{method} {endpoint}"
                    break
            except Exception as exc:
                errors.append(f"{method} {endpoint}: {exc}")

    if not bindings:
        cached_games: list[dict[str, Any]] = []
        try:
            if cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_games = [
                    value for value in (cached.get("games") or [])
                    if isinstance(value, dict) and value.get("game_title")
                ][:limit]
        except Exception as exc:
            errors.append(f"cached top games: {exc}")
        if cached_games:
            status.warnings.append(
                "Live Wikidata endpoints failed; using the last successful cached top-game ranking."
            )
            status.sources["wikidata_top_video_games"] = {
                "url": "cached prior Wikidata/QLever result",
                "rows": len(cached_games),
                "ranking_proxy": "wikimedia_sitelinks",
                "ok": True,
                "cached": True,
                "errors": errors,
            }
            return cached_games
        status.warnings.append("Wikidata top video games failed: " + " | ".join(errors))
        status.sources["wikidata_top_video_games"] = {
            "url": [WIKIDATA_QLEVER, WIKIDATA_SPARQL, WIKIDATA_SPARQL_FALLBACK],
            "rows": 0,
            "ranking_proxy": "wikimedia_sitelinks",
            "ok": False,
            "errors": errors,
        }
        return []

    games: list[dict[str, Any]] = []
    for index, binding in enumerate(bindings, 1):
        uri = str((binding.get("game") or {}).get("value") or "")
        title = str((binding.get("gameLabel") or {}).get("value") or "").strip()
        sitelinks = _safe_int((binding.get("sitelinks") or {}).get("value")) or 0
        if uri and title:
            games.append(
                {
                    "game_rank": index,
                    "game_title": title,
                    "wikidata_url": uri,
                    "wikidata_id": uri.rsplit("/", 1)[-1],
                    "sitelinks": sitelinks,
                }
            )

    if games:
        CACHE.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps({"games": games, "retrieved_at": TODAY}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(cache_path)
    status.sources["wikidata_top_video_games"] = {
        "url": successful_endpoint,
        "rows": len(games),
        "ranking_proxy": "wikimedia_sitelinks",
        "ok": bool(games),
        "cached": False,
    }
    return games


GAME_STRONG_GENRES = {
    "video game music",
    "game soundtrack",
    "video game soundtrack",
    "vgm",
    "indie game soundtrack",
    "japanese vgm",
}
GAME_SOUNDTRACK_MARKER = re.compile(
    r"\b(?:original\s+(?:video\s+)?game\s+soundtrack|"
    r"(?:video\s+)?game\s+soundtrack|original\s+game\s+score)\b",
    re.I,
)
GAME_EXPLICIT_SOUNDTRACK_RELEASE = re.compile(
    r"(?:\b(?:original\s+(?:video\s+)?game\s+soundtracks?|"
    r"(?:video\s+)?game\s+soundtracks?|original\s+soundtracks?|"
    r"original\s+sound\s+versions?|original\s+(?:game\s+)?scores?|"
    r"soundtrack\s+recordings?|soundtracks?|\bosts?\b)\b|"
    r"オリジナル[・\s]*(?:ゲーム[・\s]*)?サウンド[・\s]*(?:トラック|ヴァージョン)|"
    r"サウンド[・\s]*トラック|サントラ|"
    r"오리지널\s*사운드트랙|사운드트랙|(?:游戏|遊戲)?原声(?:带|帶)?)",
    re.I,
)
GAME_MEDIA_REJECT = re.compile(
    r"\b(?:motion picture|original film|film soundtrack|movie soundtrack|"
    r"television soundtrack|tv soundtrack|original tv series|original series soundtrack|"
    r"music from the original tv series|anime score|anime soundtrack|animation soundtrack|"
    r"broadway|stage musical)\b",
    re.I,
)
GAME_ARRANGEMENT_REJECT = re.compile(
    r"\b(?:tribute|piano collections?|for piano solo|piano cover|soundtrack for piano|"
    r"music box|lullaby|karaoke|cover album|played by|lo-?fi|remix album|"
    r"orchestral arrangements?|reimagined)\b",
    re.I,
)
GAME_LICENSED_COMPILATION = re.compile(r"\bmusic from the video game\b", re.I)
GAME_FROM_TITLE_PATTERNS = [
    re.compile(r"\bfrom\s+[\"“](?P<game>[^\"”]{2,120})[\"”]", re.I),
    re.compile(r"\((?:theme\s+)?from\s+(?P<game>[^)\]]{2,120})\)", re.I),
    re.compile(r"\[(?:theme\s+)?from\s+(?P<game>[^\]]{2,120})\]", re.I),
]
GAME_ASSOCIATION_PRIORITY = {
    "official_soundtrack_album": 4,
    "franchise_artist": 4,
    "explicit_track_reference": 3,
    "genre_album": 1,
}
GAME_LISTENBRAINZ_TAGS = [
    "video game music",
    "video game soundtrack",
    "game soundtrack",
    "vgm",
    "game music",
]
GAME_FRANCHISE_ARTISTS = {
    "league of legends": "League of Legends",
    "valorant": "VALORANT",
}


def _game_genre_hit(genres: Iterable[str]) -> bool:
    normalized = {str(value).strip().casefold() for value in genres if str(value).strip()}
    return bool(normalized.intersection(GAME_STRONG_GENRES))


def _game_track_key(row: pd.Series) -> str:
    return str(row.get("track_id") or "").strip() or (
        f"{norm(str(row.get('title') or ''))}|{norm(str(row.get('main_artist') or ''))}"
    )


def _game_song_key(row: pd.Series) -> tuple[str, str]:
    return (
        norm(str(row.get("title") or "")),
        norm(str(row.get("main_artist") or row.get("artists") or "")),
    )


def _game_from_track_title(value: Any) -> str | None:
    title = html.unescape(str(value or "")).strip()
    for pattern in GAME_FROM_TITLE_PATTERNS:
        match = pattern.search(title)
        if not match:
            continue
        game = match.group("game").strip(" \t-–—:|,()[]{}'\"“”")
        game = re.sub(r"\s+(?:theme|soundtrack|ost|score)\s*$", "", game, flags=re.I)
        if game:
            return game
    return None


def _game_candidate_kind(row: pd.Series, game_title: str) -> str:
    album = html.unescape(str(row.get("album_name") or "")).strip()
    artist = html.unescape(str(row.get("main_artist") or row.get("artists") or "")).strip()
    if _game_from_track_title(row.get("title")):
        return "explicit_track_reference"
    if GAME_FRANCHISE_ARTISTS.get(norm(artist)):
        return "franchise_artist"
    if GAME_SOUNDTRACK_MARKER.search(album):
        return "official_soundtrack_album"
    return "genre_album"


def _infer_game_title(row: pd.Series) -> str | None:
    album = html.unescape(str(row.get("album_name") or "")).strip()
    title = html.unescape(str(row.get("title") or "")).strip()
    artist = html.unescape(str(row.get("main_artist") or row.get("artists") or "")).strip()
    genres = " ".join(_parse_clean_genres(row.get("genres")))
    text = f"{album} | {title} | {genres}"

    if not album or GAME_MEDIA_REJECT.search(text) or GAME_ARRANGEMENT_REJECT.search(text):
        return None

    explicit_game = _game_from_track_title(title)
    if explicit_game:
        return explicit_game

    artist_game = GAME_FRANCHISE_ARTISTS.get(norm(artist))
    if artist_game and not GAME_SOUNDTRACK_MARKER.search(album):
        return artist_game

    candidate = album
    candidate = re.sub(
        r"\s*(?:,|[-–—:])?\s*(?:volume|vol\.?|part|pt\.?)\s*\d+\s*$",
        "",
        candidate,
        flags=re.I,
    )
    candidate = re.sub(
        r"\s*[-–—:]\s*volume\s+(?:alpha|beta|\d+)\s*$",
        "",
        candidate,
        flags=re.I,
    )
    candidate = re.sub(
        r"\s*[\[(]\s*(?:the\s+)?(?:original\s+)?"
        r"(?:(?:video\s+)?game\s+)?(?:soundtrack|ost|score)"
        r"(?:[^\])}]*)?[\])}]\s*$",
        "",
        candidate,
        flags=re.I,
    )
    candidate = re.sub(
        r"\s*(?:[-–—:]\s*)?(?:the\s+)?(?:original\s+)?"
        r"(?:(?:video\s+)?game\s+)?(?:soundtrack|ost|score)"
        r"(?:\s+(?:expanded|deluxe|complete|remastered|edition)\b.*)?$",
        "",
        candidate,
        flags=re.I,
    )
    candidate = re.sub(
        r"\s*[\[(]\s*(?:the\s+)?(?:complete|deluxe|remastered)\s+edition\s*[\])}]\s*$",
        "",
        candidate,
        flags=re.I,
    )
    candidate = candidate.strip(" \t-–—:|,()[]{}")

    # Album subtitles usually describe a soundtrack volume, not a separate game.
    if " - " in candidate:
        left, right = candidate.split(" - ", 1)
        if re.search(
            r"\b(?:the\s+\w+ing|volume|vol\.?|chapter|part|music|selections?)\b",
            right,
            re.I,
        ):
            candidate = left.strip()

    if artist_game and (not candidate or norm(candidate) == norm(title)):
        candidate = artist_game

    if (
        not candidate
        or norm(candidate) in {"original soundtrack", "soundtrack", "ost", "score"}
        or (norm(candidate) == norm(title) and not artist_game and not GAME_SOUNDTRACK_MARKER.search(album))
    ):
        return None
    return candidate


def _game_title_from_explicit_soundtrack_release(album: Any) -> str | None:
    """Remove an explicit soundtrack suffix without fuzzy game matching."""
    value = html.unescape(str(album or "")).strip()
    marker = GAME_EXPLICIT_SOUNDTRACK_RELEASE.search(value)
    if not value or not marker:
        return None
    candidate = value[:marker.start()].strip(" \t-–—:|,()[]{}'\"“”・")
    candidate = re.sub(
        r"\s*(?:collector'?s|deluxe|complete|remastered|expanded)\s+edition\s*$",
        "",
        candidate,
        flags=re.I,
    )
    candidate = re.sub(
        r"\s*(?:,|[-–—:])?\s*(?:volume|vol\.?|disc|cd|part|pt\.?)\s*"
        r"(?:\d+|[ivxlcdm]+)\s*$",
        "",
        candidate,
        flags=re.I,
    )
    candidate = candidate.strip(" \t-–—:|,()[]{}'\"“”・")
    if not candidate or norm(candidate) in {
        "original",
        "game",
        "video game",
        "music",
        "soundtrack",
    }:
        return None
    return candidate


def _listenbrainz_video_game_rows(status: Any) -> list[SongRow]:
    """Fetch release-group-tagged recordings with explicit soundtrack albums.

    Tag-radio evidence is restricted to release-group tags; artist-only tags are too
    broad. MusicBrainz recording/release metadata then has to contain an explicit
    soundtrack marker, and common film/TV/anime and arrangement markers are rejected.
    """
    cache_path = CACHE / "listenbrainz_video_game_music.json"
    cache: dict[str, Any] = {
        "schema_version": 1,
        "tag_results": {},
        "metadata": {},
    }
    try:
        if cache_path.exists():
            value = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("schema_version") == 1:
                cache = value
    except Exception as exc:
        status.warnings.append(f"ListenBrainz video-game checkpoint ignored: {exc}")

    tag_cache = cache.setdefault("tag_results", {})
    metadata_cache = cache.setdefault("metadata", {})
    if not isinstance(tag_cache, dict) or not isinstance(metadata_cache, dict):
        tag_cache = {}
        metadata_cache = {}
        cache = {
            "schema_version": 1,
            "tag_results": tag_cache,
            "metadata": metadata_cache,
        }

    def save_cache() -> None:
        CACHE.mkdir(parents=True, exist_ok=True)
        cache["updated_at"] = TODAY
        temp_path = cache_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(cache, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(cache_path)

    source_errors: list[str] = []
    with httpx.Client(
        timeout=90,
        follow_redirects=True,
        headers={
            "User-Agent": (
                "BeatHit-Dataset/1.0 "
                "(+https://github.com/Dummy1-sudo/BeatHit-Dataset)"
            )
        },
    ) as client:
        for tag in GAME_LISTENBRAINZ_TAGS:
            try:
                response = client.get(
                    f"{LISTENBRAINZ_API}/lb-radio/tags",
                    params={
                        "tag": tag,
                        "operator": "OR",
                        "count": 1000,
                        "pop_begin": 0,
                        "pop_end": 100,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("expected a list of tag-radio candidates")
                tag_cache[tag] = payload
                save_cache()
            except Exception as exc:
                source_errors.append(f"tag {tag}: {exc}")
                if tag not in tag_cache:
                    status.warnings.append(
                        f"ListenBrainz video-game tag {tag!r}: {exc}"
                    )

        evidence: dict[str, dict[str, Any]] = {}
        for tag in GAME_LISTENBRAINZ_TAGS:
            values = tag_cache.get(tag) or []
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict):
                    continue
                if str(value.get("source") or "").casefold() != "release-group":
                    continue
                mbid = str(value.get("recording_mbid") or "").strip().casefold()
                if not re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                    r"[0-9a-f]{4}-[0-9a-f]{12}",
                    mbid,
                ):
                    continue
                item = evidence.setdefault(
                    mbid,
                    {"percent": 0.0, "tag_count": 0, "tags": []},
                )
                item["percent"] = max(
                    float(item["percent"]),
                    _safe_float(value.get("percent")) or 0.0,
                )
                item["tag_count"] = max(
                    int(item["tag_count"]),
                    _safe_int(value.get("tag_count")) or 0,
                )
                if tag not in item["tags"]:
                    item["tags"].append(tag)

        missing = [mbid for mbid in evidence if mbid not in metadata_cache]
        for start in range(0, len(missing), 500):
            batch = missing[start:start + 500]
            try:
                response = client.post(
                    f"{LISTENBRAINZ_API}/metadata/recording/",
                    json={
                        "recording_mbids": batch,
                        "inc": "artist release tag",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("expected a recording metadata object")
                for mbid in batch:
                    metadata_cache[mbid] = payload.get(mbid)
                save_cache()
            except Exception as exc:
                source_errors.append(f"metadata batch {start // 500 + 1}: {exc}")
                status.warnings.append(
                    f"ListenBrainz video-game metadata batch {start // 500 + 1}: {exc}"
                )

    rows: list[SongRow] = []
    rejection_counts: dict[str, int] = defaultdict(int)
    for mbid, tag_evidence in evidence.items():
        metadata = metadata_cache.get(mbid)
        if not isinstance(metadata, dict):
            rejection_counts["missing_metadata"] += 1
            continue
        recording = metadata.get("recording") or {}
        artist_data = metadata.get("artist") or {}
        release = metadata.get("release") or {}
        title = str(recording.get("name") or "").strip()
        artist = str(artist_data.get("name") or "").strip()
        album = str(release.get("name") or "").strip()
        combined = f"{album} | {title} | {artist}"
        if not title or not artist or not album:
            rejection_counts["blank_metadata"] += 1
            continue
        if GAME_MEDIA_REJECT.search(combined):
            rejection_counts["non_game_media"] += 1
            continue
        if GAME_ARRANGEMENT_REJECT.search(combined):
            rejection_counts["arrangement_or_cover"] += 1
            continue
        game_title = _game_title_from_explicit_soundtrack_release(album)
        if not game_title:
            rejection_counts["no_explicit_soundtrack_marker"] += 1
            continue

        first_release = str(recording.get("first_release_date") or "").strip()
        isrcs = recording.get("isrcs") or []
        isrc = (
            str(isrcs[0]).strip().upper()
            if isinstance(isrcs, list) and isrcs
            else None
        )
        tags = list(tag_evidence.get("tags") or [])
        percent = float(tag_evidence.get("percent") or 0.0)
        rows.append(
            SongRow(
                title=title,
                main_artist=artist,
                album=album,
                release_date=first_release or None,
                release_year=_safe_int(first_release[:4]) if first_release else None,
                genres=tags,
                languages=["und"],
                screen_work=game_title,
                metric_name="listenbrainz_tag_popularity_percent",
                metric_value=max(percent, 0.01),
                metric_unit="score_0_100",
                overall_popularity_score=max(percent, 0.01),
                musicbrainz_recording_mbid=mbid,
                isrc=isrc,
                source_url=f"https://musicbrainz.org/recording/{mbid}",
                retrieved_at=TODAY,
                source_notes=(
                    "ListenBrainz release-group tag-radio popularity evidence plus "
                    "MusicBrainz recording/release metadata. The score is a popularity "
                    "percentage, not a stream or listen count."
                ),
                extra={
                    "culture_category": "video_game_music",
                    "video_game": game_title,
                    "selection": (
                        "release-group game-music tag plus explicit soundtrack "
                        "release marker"
                    ),
                    "game_association_kind": "listenbrainz_release_group_tag",
                    "listenbrainz_source_scope": "release-group",
                    "listenbrainz_source_tags": tags,
                    "listenbrainz_tag_count": int(
                        tag_evidence.get("tag_count") or 0
                    ),
                    "listenbrainz_tag_popularity_percent": percent,
                    "musicbrainz_release_mbid": release.get("mbid"),
                    "musicbrainz_release_group_mbid": release.get(
                        "release_group_mbid"
                    ),
                    "explicit_soundtrack_release": album,
                },
            )
        )

    rows.sort(
        key=lambda row: (
            float(row.metric_value or 0.0),
            int((row.extra or {}).get("listenbrainz_tag_count") or 0),
        ),
        reverse=True,
    )
    status.sources["listenbrainz_video_game_music"] = {
        "source": "ListenBrainz tag radio + recording metadata API",
        "tags": GAME_LISTENBRAINZ_TAGS,
        "release_group_tagged_recordings": len(evidence),
        "metadata_records": sum(
            1 for value in metadata_cache.values() if isinstance(value, dict)
        ),
        "qualified_rows": len(rows),
        "rejections": dict(sorted(rejection_counts.items())),
        "checkpoint": str(cache_path.relative_to(ROOT)),
        "errors": source_errors,
        "ok": bool(rows),
    }
    return rows


def build_video_game_music(catalog: pd.DataFrame, status: Any) -> list[SongRow]:
    """Build a popularity-ranked list of source-backed video-game soundtrack recordings."""
    target = 1_000
    games = _fetch_top_games(status, target)

    candidates: list[tuple[pd.Series, str, str, bool, str, str]] = []
    token_index: dict[str, set[int]] = defaultdict(set)
    stop = {
        "the", "and", "for", "with", "from", "game", "edition", "remastered",
        "original", "soundtrack", "score", "music", "volume",
    }

    for _, row in catalog.iterrows():
        row_genres = _parse_clean_genres(row.get("genres"))
        genre_hit = _game_genre_hit(row_genres)
        album = str(row.get("album_name") or "")
        title = str(row.get("title") or "")
        text = f"{album} | {title} | {' '.join(row_genres)}"
        if GAME_MEDIA_REJECT.search(text) or GAME_ARRANGEMENT_REJECT.search(text):
            continue
        if GAME_LICENSED_COMPILATION.search(album) and not genre_hit:
            continue
        explicit_title_game = _game_from_track_title(title)
        if not genre_hit and not GAME_SOUNDTRACK_MARKER.search(album) and not explicit_title_game:
            continue

        game_title = _infer_game_title(row)
        if not game_title:
            continue
        association_kind = _game_candidate_kind(row, game_title)

        album_norm = norm(album)
        title_norm = norm(title)
        game_norm = norm(game_title)
        position = len(candidates)
        candidates.append((row, album_norm, title_norm, genre_hit, game_title, association_kind))
        for token in (
            set(re.findall(r"[a-z0-9]{4,}", f"{game_norm} {album_norm} {title_norm}")) - stop
        ):
            token_index[token].add(position)

    selected: list[SongRow] = []
    used_tracks: set[str] = set()
    used_songs: set[tuple[str, str]] = set()
    used_mbids: set[str] = set()
    used_isrcs: set[str] = set()
    per_game: dict[str, int] = defaultdict(int)

    def append_song(
        row: pd.Series,
        game_title: str,
        *,
        selection: str,
        extra: dict[str, Any],
    ) -> bool:
        track_key = _game_track_key(row)
        song_key = _game_song_key(row)
        isrc = str(row.get("isrc") or "").strip().casefold()
        game_key = norm(game_title)
        if (
            not game_key
            or track_key in used_tracks
            or song_key in used_songs
            or bool(isrc and isrc in used_isrcs)
        ):
            return False
        used_tracks.add(track_key)
        used_songs.add(song_key)
        if isrc:
            used_isrcs.add(isrc)
        per_game[game_key] += 1
        song = _catalog_song(
            row,
            extra={
                "culture_category": "video_game_music",
                "video_game": game_title,
                "selection": selection,
                **extra,
            },
        )
        song.screen_work = game_title
        selected.append(song)
        return True

    for game in games:
        game_norm = norm(game["game_title"])
        tokens = [token for token in re.findall(r"[a-z0-9]{4,}", game_norm) if token not in stop]
        pools = [token_index[token] for token in tokens if token in token_index]
        if not pools:
            continue

        pools.sort(key=len)
        pool = set(pools[0])
        for extra_pool in pools[1:3]:
            intersection = pool & extra_pool
            if intersection:
                pool = intersection

        best: tuple[pd.Series, float] | None = None
        best_score = -1.0
        for position in list(pool)[:1_000]:
            row, album_norm, title_norm, genre_hit, inferred_game, association_kind = candidates[position]
            inferred_norm = norm(inferred_game)
            exact = game_norm == inferred_norm
            contained = bool(
                game_norm
                and (
                    game_norm in inferred_norm
                    or inferred_norm in game_norm
                    or game_norm in album_norm
                )
            )
            similarity = max(
                fuzz.ratio(game_norm, inferred_norm) if inferred_norm else 0,
                fuzz.partial_ratio(game_norm, album_norm) if album_norm else 0,
            )
            minimum_similarity = 96 if association_kind == "genre_album" else 88
            if not exact and not contained and similarity < minimum_similarity:
                continue
            combined = (
                _catalog_score(row)
                + similarity * 0.35
                + game["sitelinks"] * 0.002
                + GAME_ASSOCIATION_PRIORITY[association_kind] * 8
            )
            if combined > best_score:
                best_score = combined
                best = (row, float(similarity))

        if best is None:
            continue
        row, best_match = best
        association_kind = _game_candidate_kind(row, game["game_title"])
        append_song(
            row,
            game["game_title"],
            selection="highest-popularity confident soundtrack match for this ranked game",
            extra={
                "game_rank": game["game_rank"],
                "game_popularity_proxy": "wikimedia_sitelinks",
                "game_sitelinks": game["sitelinks"],
                "game_wikidata_id": game["wikidata_id"],
                "game_wikidata_url": game["wikidata_url"],
                "game_soundtrack_match_score": best_match,
                "game_association_kind": association_kind,
            },
        )
        if len(selected) >= target:
            break

    fallback_candidates = sorted(
        candidates,
        key=lambda item: (
            GAME_ASSOCIATION_PRIORITY[item[5]],
            _catalog_score(item[0]),
        ),
        reverse=True,
    )
    fallback_unique = 0
    fallback_additional = 0

    # First preserve breadth using only direct soundtrack/artist/title evidence. A raw
    # genre tag alone is too noisy unless the candidate already matched a ranked game.
    for row, album_norm, title_norm, genre_hit, game_title, association_kind in fallback_candidates:
        if len(selected) >= target:
            break
        game_key = norm(game_title)
        if association_kind == "genre_album":
            continue
        if (
            association_kind == "explicit_track_reference"
            and not genre_hit
            and game_key not in {norm(str(game.get("game_title") or "")) for game in games}
        ):
            continue
        if per_game.get(game_key, 0) >= 1:
            continue
        if append_song(
            row,
            game_title,
            selection="highest-popularity source-backed recording for inferred game",
            extra={
                "catalog_fallback_rank": fallback_unique + 1,
                "wikidata_available": bool(games),
                "fallback_stage": "one_per_game",
                "game_association_kind": association_kind,
            },
        ):
            fallback_unique += 1

    # Then add more recognizable recordings. Genre-only albums are allowed only for a
    # game that was independently established by Wikidata or stronger soundtrack evidence.
    for row, album_norm, title_norm, genre_hit, game_title, association_kind in fallback_candidates:
        if len(selected) >= target:
            break
        game_key = norm(game_title)
        if association_kind == "genre_album" and per_game.get(game_key, 0) == 0:
            continue
        if association_kind == "explicit_track_reference" and not genre_hit and per_game.get(game_key, 0) == 0:
            continue
        if per_game.get(game_key, 0) >= 5:
            continue
        if append_song(
            row,
            game_title,
            selection="additional high-popularity game-associated recording",
            extra={
                "catalog_fallback_rank": fallback_unique + fallback_additional + 1,
                "wikidata_available": bool(games),
                "fallback_stage": "additional_tracks",
                "per_game_track_cap": 5,
                "game_association_kind": association_kind,
            },
        ):
            fallback_additional += 1

    listenbrainz_added = 0
    listenbrainz_candidates = 0
    if len(selected) < target:
        listenbrainz_rows = _listenbrainz_video_game_rows(status)
        listenbrainz_candidates = len(listenbrainz_rows)
        for song in listenbrainz_rows:
            if len(selected) >= target:
                break
            game_key = norm(song.screen_work or "")
            song_key = (norm(song.title), norm(song.main_artist))
            mbid = str(song.musicbrainz_recording_mbid or "").strip().casefold()
            isrc = str(song.isrc or "").strip().casefold()
            if (
                not game_key
                or not all(song_key)
                or per_game.get(game_key, 0) >= 5
                or song_key in used_songs
                or bool(mbid and mbid in used_mbids)
                or bool(isrc and isrc in used_isrcs)
            ):
                continue
            used_songs.add(song_key)
            if mbid:
                used_mbids.add(mbid)
            if isrc:
                used_isrcs.add(isrc)
            per_game[game_key] += 1
            selected.append(song)
            listenbrainz_added += 1

    # The requested list is popularity-ranked. Game rank remains provenance, not the
    # final row ordering criterion.
    selected.sort(
        key=lambda row: (
            float(row.overall_popularity_score or 0.0),
            float(row.metric_value or 0.0),
        ),
        reverse=True,
    )
    for rank, row in enumerate(selected, 1):
        row.rank = rank
    write_rows(selected, DATA / "video_games" / "video_game_music_1000.csv")

    st = status.datasets["video_game_music"]
    st.rows = len(selected)
    st.complete = len(selected) == target
    st.metric_coverage = _metric_counts(selected)
    st.notes = [
        "The direct Wikidata video-game query is attempted first; the last successful top-game ranking is cached for later runs.",
        "Movie/television/anime soundtracks, licensed compilations, tribute releases, piano collections, music-box albums, cover albums, and generic remix albums are rejected.",
        "Each row records whether the game association came from an official soundtrack album, franchise artist, explicit track-title reference, high-confidence Wikidata match, or a ListenBrainz release-group game tag paired with an explicit soundtrack release.",
        "Selection preserves game breadth, then allows up to five source-backed recordings per game; final rows are sorted by popularity evidence.",
        f"ranked_games={len(games)}; catalog_soundtrack_candidates={len(candidates)}; listenbrainz_candidates={listenbrainz_candidates}; listenbrainz_added={listenbrainz_added}; unique_games={len(per_game)}; fallback_unique={fallback_unique}; fallback_additional={fallback_additional}; rows={len(selected)}",
    ]
    status.save()
    return selected

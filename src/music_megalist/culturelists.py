from __future__ import annotations

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
from .io import write_rows
from .models import SongRow

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CACHE = ROOT / ".cache" / "full_build"
TODAY = date.today().isoformat()
LISTENBRAINZ_API = "https://api.listenbrainz.org/1"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_SPARQL_FALLBACK = "https://query.wikidata.org/bigdata/namespace/wdq/sparql"
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
KPOP_REJECT_TITLE = re.compile(
    r"\b(?:reaction|dance practice|dance cover|cover|lyrics?|karaoke|sped up|slowed|"
    r"nightcore|remix|teaser|trailer|shorts?|fanmade|fancam|instrumental)\b",
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
    else:
        quoted = re.match(r"^(.+?)\s+['\"“](.+?)['\"”]\s*$", core)
        if quoted:
            artist, song = quoted.group(1), quoted.group(2)
        else:
            artist = re.sub(r"\s+(?:official|오피셜)$", "", channel_title, flags=re.I).strip()
            song = core or title
    artist = re.sub(r"^\[(?:mv|m/v)\]\s*", "", artist, flags=re.I).strip(" \t-–—|:'\"")
    song = song.strip(" \t-–—|:'\"")
    return song or title, artist or channel_title or "Unknown K-pop artist"


def _youtube_kpop_rows(status: Any, threshold: int) -> list[SongRow]:
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        status.warnings.append("K-pop YouTube discovery skipped: missing YOUTUBE_API_KEY")
        return []

    pages = max(1, min(_safe_int(os.getenv("BEATHIT_KPOP_SEARCH_PAGES")) or 4, 8))
    discovered: dict[str, dict[str, Any]] = {}
    search_calls = 0
    with httpx.Client(
        timeout=60,
        follow_redirects=True,
        headers={"User-Agent": "BeatHit-Dataset/1.0"},
    ) as client:
        for query in KPOP_SEARCH_QUERIES:
            page_token: str | None = None
            for page in range(pages):
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
                if page_token:
                    params["pageToken"] = page_token
                try:
                    response = client.get(f"{YOUTUBE_API}/search", params=params)
                    response.raise_for_status()
                    data = response.json()
                    search_calls += 1
                except Exception as exc:
                    status.warnings.append(f"K-pop YouTube search {query!r} page={page + 1}: {exc}")
                    break
                for item in data.get("items", []) or []:
                    video_id = str((item.get("id") or {}).get("videoId") or "").strip()
                    if video_id:
                        discovered.setdefault(
                            video_id,
                            {"query": query, "search_snippet": item.get("snippet") or {}},
                        )
                page_token = str(data.get("nextPageToken") or "").strip() or None
                if not page_token:
                    break
                time.sleep(0.05)

        rows: list[SongRow] = []
        video_ids = list(discovered)
        for start in range(0, len(video_ids), 50):
            batch = video_ids[start:start + 50]
            try:
                response = client.get(
                    f"{YOUTUBE_API}/videos",
                    params={
                        "part": "snippet,statistics",
                        "id": ",".join(batch),
                        "key": key,
                    },
                )
                response.raise_for_status()
                items = response.json().get("items", []) or []
            except Exception as exc:
                status.warnings.append(f"K-pop YouTube statistics batch={start // 50 + 1}: {exc}")
                continue

            for item in items:
                video_id = str(item.get("id") or "").strip()
                snippet = item.get("snippet") or {}
                statistics = item.get("statistics") or {}
                views = _safe_int(statistics.get("viewCount"))
                if views is None or views <= threshold:
                    continue
                raw_title = html.unescape(str(snippet.get("title") or "")).strip()
                channel_title = html.unescape(str(snippet.get("channelTitle") or "")).strip()
                description = html.unescape(str(snippet.get("description") or ""))
                tags = " ".join(str(value) for value in (snippet.get("tags") or []))
                evidence = f"{raw_title} {channel_title} {description} {tags}"
                if KPOP_REJECT_TITLE.search(raw_title):
                    continue
                official = bool(
                    KPOP_OFFICIAL_MARKER.search(raw_title)
                    or KPOP_LABEL_CHANNEL.search(channel_title)
                )
                kpop_evidence = bool(
                    re.search(r"\b(?:k-?pop|korean pop)\b|[가-힣]", evidence, re.I)
                    or KPOP_LABEL_CHANNEL.search(channel_title)
                )
                if not official or not kpop_evidence:
                    continue

                title, artist = _parse_kpop_video_identity(raw_title, channel_title)
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
                            "Discovered with the official YouTube Data API, filtered to music videos "
                            "with K-pop evidence and official-MV/channel evidence, and qualified by "
                            "an observed view count strictly above 100,000,000."
                        ),
                        extra={
                            "culture_category": "kpop",
                            "youtube_video_id": video_id,
                            "youtube_channel_id": snippet.get("channelId"),
                            "youtube_channel_title": channel_title,
                            "youtube_original_title": raw_title,
                            "youtube_discovery_query": (discovered.get(video_id) or {}).get("query"),
                            "youtube_view_threshold_strictly_greater_than": threshold,
                            "selection": "official YouTube API K-pop discovery fallback",
                        },
                    )
                )

    status.sources["youtube_kpop_discovery"] = {
        "source": "YouTube Data API v3 search.list + videos.list",
        "queries": KPOP_SEARCH_QUERIES,
        "pages_per_query": pages,
        "search_calls": search_calls,
        "unique_video_candidates": len(discovered),
        "qualified_rows": len(rows),
        "threshold": threshold,
        "ok": bool(rows),
    }
    return rows


def build_kpop_youtube_100m(catalog: pd.DataFrame, status: Any) -> list[SongRow]:
    """Build K-pop tracks with observed YouTube views strictly above 100M."""
    threshold = 100_000_000
    rows: list[SongRow] = []
    for _, row in catalog.iterrows():
        row_genres = _parse_genres(row.get("genres"))
        if not _genre_matches(row_genres, ["k-pop", "korean pop", "k-pop boy group", "k-pop girl group"]):
            continue
        views = _safe_int(row.get("youtube_views"))
        if views is None or views <= threshold:
            continue
        url = str(row.get("youtube_views_source_url") or row.get("source_url") or "").strip()
        if not url:
            continue
        rows.append(
            _catalog_song(
                row,
                force_metric=("youtube_views", float(views), "views", url),
                extra={
                    "culture_category": "kpop",
                    "youtube_view_threshold_strictly_greater_than": threshold,
                    "matched_source_genres": row_genres,
                },
            )
        )

    catalog_rows = len(rows)
    rows.extend(_youtube_kpop_rows(status, threshold))
    rows = _dedupe_rank(rows, 10_000)
    write_rows(rows, DATA / "kpop" / "kpop_youtube_over_100m.csv")
    st = status.datasets["kpop"]
    st.rows = len(rows)
    st.complete = False
    st.metric_coverage = _metric_counts(rows)
    st.notes = [
        "Catalog-tagged K-pop candidates are merged with bounded official YouTube Data API discovery.",
        "Every row has an observed YouTube view count strictly above 100,000,000; covers, reactions, lyric videos, remixes, teasers, and dance-practice videos are rejected.",
        f"catalog_qualified={catalog_rows}; final_deduplicated_rows={len(rows)}",
        "Not marked exhaustive because bounded YouTube search cannot prove enumeration of every K-pop upload.",
    ]
    status.save()
    return rows


def _fetch_top_games(status: Any, limit: int = 1_000) -> list[dict[str, Any]]:
    query = f"""
SELECT ?game ?gameLabel ?sitelinks WHERE {{
  ?game wdt:P31/wdt:P279* wd:Q7889 ;
        wikibase:sitelinks ?sitelinks .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
ORDER BY DESC(?sitelinks)
LIMIT {int(limit)}
"""
    bindings: list[dict[str, Any]] = []
    successful_endpoint = ""
    errors: list[str] = []
    attempts = [
        ("POST", WIKIDATA_SPARQL),
        ("POST", WIKIDATA_SPARQL_FALLBACK),
        ("GET", WIKIDATA_SPARQL),
    ]
    with httpx.Client(
        timeout=120,
        follow_redirects=True,
        headers={"User-Agent": "BeatHit-Dataset/1.0 (source-backed music research)"},
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
        status.warnings.append("Wikidata top video games failed: " + " | ".join(errors))
        status.sources["wikidata_top_video_games"] = {
            "url": [WIKIDATA_SPARQL, WIKIDATA_SPARQL_FALLBACK],
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

    status.sources["wikidata_top_video_games"] = {
        "url": successful_endpoint,
        "rows": len(games),
        "ranking_proxy": "wikimedia_sitelinks",
        "ok": bool(games),
    }
    return games


def _infer_game_title(row: pd.Series) -> str | None:
    album = html.unescape(str(row.get("album_name") or "")).strip()
    if not album:
        return None
    candidate = re.sub(
        r"\s*(?:[-–—:]\s*)?(?:the\s+)?(?:original\s+)?"
        r"(?:(?:video\s+)?game\s+)?(?:soundtrack|ost|score)"
        r"(?:\s+(?:expanded|deluxe|complete|remastered|edition|volume|vol\.?)\b.*)?$",
        "",
        album,
        flags=re.I,
    ).strip(" \t-–—:|")
    candidate = re.sub(
        r"^(?:the\s+)?(?:original\s+)?(?:(?:video\s+)?game\s+)?"
        r"(?:soundtrack|ost|score)\s*(?:for|from)?\s*",
        "",
        candidate,
        flags=re.I,
    ).strip(" \t-–—:|")
    if not candidate or norm(candidate) in {"original soundtrack", "soundtrack", "ost", "score"}:
        return None
    return candidate


def build_video_game_music(catalog: pd.DataFrame, status: Any) -> list[SongRow]:
    """Choose one recognizable source-backed soundtrack recording for each ranked game."""
    target = 1_000
    games = _fetch_top_games(status, target)

    candidates: list[tuple[pd.Series, str, str, bool]] = []
    token_index: dict[str, set[int]] = defaultdict(set)
    stop = {"the", "and", "for", "with", "from", "game", "edition", "remastered", "original", "soundtrack", "score"}

    for _, row in catalog.iterrows():
        row_genres = _parse_genres(row.get("genres"))
        genre_hit = _genre_matches(
            row_genres,
            ["video game music", "game soundtrack", "video game soundtrack", "vgm", "chiptune"],
        )
        album_norm = norm(str(row.get("album_name") or ""))
        title_norm = norm(str(row.get("title") or ""))
        text = f"{row.get('album_name', '')} | {row.get('title', '')}"
        soundtrack_hit = bool(
            re.search(r"\b(?:original game soundtrack|video game soundtrack|game soundtrack|ost|score)\b", text, re.I)
        )
        if not genre_hit and not soundtrack_hit:
            continue
        position = len(candidates)
        candidates.append((row, album_norm, title_norm, genre_hit))
        for token in set(re.findall(r"[a-z0-9]{4,}", f"{album_norm} {title_norm}")) - stop:
            token_index[token].add(position)

    selected: list[SongRow] = []
    used_tracks: set[str] = set()
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

        best_row: pd.Series | None = None
        best_score = -1.0
        best_match = 0.0
        for position in list(pool)[:1_000]:
            row, album_norm, title_norm, genre_hit = candidates[position]
            exact = game_norm == album_norm or game_norm == title_norm
            contained = bool(game_norm and (game_norm in album_norm or game_norm in title_norm))
            similarity = max(
                fuzz.ratio(game_norm, album_norm) if album_norm else 0,
                fuzz.ratio(game_norm, title_norm) if title_norm else 0,
                fuzz.partial_ratio(game_norm, album_norm) if album_norm else 0,
            )
            if not exact and not contained and similarity < 88:
                continue
            if not genre_hit and not re.search(
                r"\b(?:soundtrack|ost|score)\b",
                str(row.get("album_name") or ""),
                re.I,
            ):
                continue

            track_key = str(row.get("track_id") or "").strip() or (
                f"{norm(str(row.get('title') or ''))}|{norm(str(row.get('main_artist') or ''))}"
            )
            if track_key in used_tracks:
                continue
            combined = _catalog_score(row) + similarity * 0.35 + game["sitelinks"] * 0.002
            if combined > best_score:
                best_score = combined
                best_row = row
                best_match = float(similarity)

        if best_row is None:
            continue

        track_key = str(best_row.get("track_id") or "").strip() or (
            f"{norm(str(best_row.get('title') or ''))}|{norm(str(best_row.get('main_artist') or ''))}"
        )
        used_tracks.add(track_key)
        song = _catalog_song(
            best_row,
            extra={
                "culture_category": "video_game_music",
                "video_game": game["game_title"],
                "game_rank": game["game_rank"],
                "game_popularity_proxy": "wikimedia_sitelinks",
                "game_sitelinks": game["sitelinks"],
                "game_wikidata_id": game["wikidata_id"],
                "game_wikidata_url": game["wikidata_url"],
                "game_soundtrack_match_score": best_match,
                "selection": "highest-popularity confident soundtrack match for this ranked game",
            },
        )
        song.screen_work = game["game_title"]
        selected.append(song)
        if len(selected) >= target:
            break

    # If Wikidata is unavailable, or confident ranked-game matching leaves gaps, use only
    # catalog rows explicitly tagged as video-game music/game soundtracks. Keep one recording
    # per inferred game/album and rank this fallback by song-level popularity evidence.
    fallback_added = 0
    used_games = {norm(str(row.screen_work or "")) for row in selected if row.screen_work}
    fallback_candidates = sorted(candidates, key=lambda item: _catalog_score(item[0]), reverse=True)
    for row, album_norm, title_norm, genre_hit in fallback_candidates:
        if len(selected) >= target:
            break
        text = f"{row.get('album_name', '')} | {row.get('title', '')}"
        explicit_game_soundtrack = bool(
            genre_hit
            or re.search(r"\b(?:video game soundtrack|game soundtrack|original game soundtrack)\b", text, re.I)
        )
        if not explicit_game_soundtrack:
            continue
        track_key = str(row.get("track_id") or "").strip() or (
            f"{norm(str(row.get('title') or ''))}|{norm(str(row.get('main_artist') or ''))}"
        )
        game_title = _infer_game_title(row)
        game_key = norm(game_title or "")
        if track_key in used_tracks or not game_key or game_key in used_games:
            continue
        used_tracks.add(track_key)
        used_games.add(game_key)
        fallback_added += 1
        song = _catalog_song(
            row,
            extra={
                "culture_category": "video_game_music",
                "video_game": game_title,
                "catalog_fallback_rank": fallback_added,
                "selection": "highest-popularity explicit video-game soundtrack candidate for inferred game",
                "wikidata_available": bool(games),
            },
        )
        song.screen_work = game_title
        selected.append(song)

    selected.sort(key=lambda row: int((row.extra or {}).get("game_rank") or 10**9))
    for rank, row in enumerate(selected, 1):
        row.rank = rank
    write_rows(selected, DATA / "video_games" / "video_game_music_1000.csv")

    st = status.datasets["video_game_music"]
    st.rows = len(selected)
    st.complete = len(selected) == target
    st.metric_coverage = _metric_counts(selected)
    st.notes = [
        "Primary path ranks games by Wikidata Wikimedia-sitelink count and selects one confident soundtrack match per game.",
        "If Wikidata fails or leaves gaps, explicitly tagged video-game soundtrack albums are used as a non-empty source-backed fallback, with one recording per inferred game.",
        "Generic movie/television OST rows are not accepted by the fallback unless source metadata explicitly identifies video-game music.",
        f"ranked_games={len(games)}; soundtrack_candidates={len(candidates)}; fallback_added={fallback_added}; matched_games={len(selected)}",
    ]
    status.save()
    return selected

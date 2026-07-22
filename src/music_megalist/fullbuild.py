from __future__ import annotations

"""Full, source-backed BeatHit dataset build.

This module is intentionally conservative: it never invents stream/view counts and it
never pads a conditional list (for example Vocaloid songs >=10M Spotify streams) with
non-qualifying rows. Fixed-size lists are filled from the best available source pool;
when a source is exhausted, the coverage report records the shortfall instead of
fabricating records.
"""

import ast
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import httpx
import pandas as pd
from rapidfuzz import fuzz

from .dedupe import dedupe, norm
from .io import write_rows
from .models import SongRow

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw" / "full_build"
CACHE = ROOT / ".cache" / "full_build"
REPORT = DATA / "BUILD_REPORT.json"
TODAY = date.today().isoformat()

ZENODO_SPOTIFY_URLS = [
    "https://zenodo.org/records/11453410/files/tracks.csv?download=1",
    "https://zenodo.org/api/records/11453410/files/tracks.csv/content",
]
ZENODO_SPOTIFY_URL = ZENODO_SPOTIFY_URLS[0]
ZENODO_SPOTIFY_MD5 = "c32dffb8f9a62fec8c5892b464d7ea42"
SPOTIFY_114K_URL = (
    "https://raw.githubusercontent.com/szewczakjj/"
    "Almost-Million-Songs-Dataset-2025-16-Features-/main/dataset.txt"
)
SPOTIFY_114K_SOURCE = "https://github.com/szewczakjj/Almost-Million-Songs-Dataset-2025-16-Features-"
SPOTIFY_2024_URL = (
    "https://gist.githubusercontent.com/cooneycw/b4021d5d872ee4a07239f0ea25c23cd7/raw/spotify_data.csv"
)
SPOTIFY_2024_SOURCE = "https://gist.github.com/cooneycw/b4021d5d872ee4a07239f0ea25c23cd7"
MAL_THEME_FALLBACK_URL = (
    "https://gist.githubusercontent.com/Chepubelja/00ed0aae9bdd4d9be5f4fd1032d0d250/raw/AnimeList.csv"
)
MAL_THEME_FALLBACK_SOURCE = "https://gist.github.com/Chepubelja/00ed0aae9bdd4d9be5f4fd1032d0d250"
ANILIST_API = "https://graphql.anilist.co"
ANIMETHEMES_API = "https://api.animethemes.moe/api/anime"
VOCADB_API = "https://vocadb.net/api/songs"
HOLODEX_API = "https://holodex.net/api/v2"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3/videos"
RYD_API = "https://returnyoutubedislikeapi.com/votes"
LISTENBRAINZ_API = "https://api.listenbrainz.org/1"
JIKAN_API = "https://api.jikan.moe/v4"
KWORB_ALLTIME_URL = "https://kworb.net/spotify/songs.html"
KWORB_DAILY_URL = "https://kworb.net/spotify/country/global_daily.html"

# Fixed targets. Vocaloid is conditional and deliberately absent from this mapping.
FIXED_TARGETS = {
    "anime": 10_000,
    "worldwide": 51_000,
    "classical": 10_000,
    "vtuber_original": 10_000,
    "emerging": 10_000,
    "genres": 10_000,
    "screen_soundtracks": 10_000,
    "vtuber_non_original": 10_000,
}

WORLDWIDE_BUCKETS = [
    ("current", None, None, 10_000),
    ("2020s", 2020, 2029, 10_000),
    ("2010s", 2010, 2019, 10_000),
    ("2000s", 2000, 2009, 10_000),
    ("1990s", 1990, 1999, 5_000),
    ("1980s", 1980, 1989, 3_000),
    ("1970s", 1970, 1979, 2_000),
    ("1960s", 1960, 1969, 1_000),
]

CLASSICAL_TERMS = {
    "classical", "baroque", "romantic era", "opera", "operatic", "orchestral", "orchestra",
    "chamber music", "concerto", "symphony", "symphonic", "choral", "choir", "classical piano",
    "classical performance", "early music", "renaissance", "impressionism", "impressionist",
    "contemporary classical", "minimalism", "neoclassical", "string quartet", "classical guitar",
    "harpsichord", "art song", "lied", "oratorio", "cantata", "sonata", "fugue",
}
# Used only as a second, conservative classical signal. Matching a composer name alone is
# insufficient unless it occurs in the title/artist metadata in a form typical of classical releases.
CLASSICAL_COMPOSERS = {
    "bach", "beethoven", "mozart", "vivaldi", "chopin", "tchaikovsky", "debussy", "ravel",
    "handel", "haydn", "schubert", "schumann", "brahms", "mahler", "strauss", "dvorak",
    "dvořák", "rachmaninoff", "prokofiev", "shostakovich", "stravinsky", "mendelssohn",
    "liszt", "verdi", "puccini", "rossini", "wagner", "bizet", "saint-saens", "saint-saëns",
    "grieg", "sibelius", "rimsky-korsakov", "mussorgsky", "borodin", "pachelbel", "corelli",
    "telemann", "scarlatti", "monteverdi", "purcell", "josquin", "palestrina", "vivaldi",
    "fauré", "faure", "satie", "berlioz", "bruckner", "elgar", "holst", "vaughan williams",
    "britten", "copland", "gershwin", "bernstein", "barber", "bartok", "bartók", "janacek",
    "janáček", "smetana", "albeniz", "albéniz", "granados", "de falla", "villa-lobos",
    "ginastera", "messiaen", "poulenc", "milhaud", "boulez", "stockhausen", "ligeti",
    "penderecki", "gorecki", "górecki", "arvo part", "arvo pärt", "philip glass", "steve reich",
    "john adams", "max richter", "caroline shaw", "thomas adès", "kaija saariaho", "john cage",
}
SOUNDTRACK_RE = re.compile(
    r"(?:soundtrack|original motion picture|original film|original series|television|tv series|"
    r"music from (?:the )?(?:film|movie|series)|motion picture|film score|movie score|"
    r"netflix|hbo|disney|season\s+\d+|original score)", re.I,
)

VOICE_SYNTH_MARKERS = {
    "hatsune miku", "初音ミク", "kagamine rin", "鏡音リン", "kagamine len", "鏡音レン",
    "megurine luka", "巡音ルカ", "kaito", "meiko", "gumi", "megpoid", "v flower", "flower",
    "kasane teto", "重音テト", "otomachi una", "音街ウナ", "kafu", "可不", "cevio",
    "synthesizer v", "vocaloid", "utau", "ia", "結月ゆかり", "yuzuki yukari", "kaai yuki",
}


@dataclass
class DatasetStatus:
    target: int | str
    rows: int = 0
    complete: bool = False
    metric_coverage: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class BuildStatus:
    started_at: str
    finished_at: str | None = None
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    datasets: dict[str, DatasetStatus] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def save(self) -> None:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(v: Any) -> int | None:
    if v is None or v == "" or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


def _safe_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", "").strip())
        return None if math.isnan(f) else f
    except (ValueError, TypeError):
        return None


def _parse_year(value: Any) -> int | None:
    if value is None:
        return None
    m = re.search(r"\b(18|19|20)\d{2}\b", str(value))
    return int(m.group(0)) if m else None


def _split_artist_string(value: Any) -> list[str]:
    if value is None:
        return []
    s = str(value).strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            obj = ast.literal_eval(s)
            if isinstance(obj, list):
                return [str(x).strip() for x in obj if str(x).strip()]
        except Exception:
            pass
    # Semicolon is the canonical delimiter in the 114k fallback. Commas are kept when
    # they plausibly belong to one artist name; ampersand/feat remain in main credit.
    if ";" in s:
        return [x.strip() for x in s.split(";") if x.strip()]
    return [s]


def _parse_genres(value: Any) -> list[str]:
    if value is None:
        return []
    s = str(value).strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            obj = ast.literal_eval(s)
            if isinstance(obj, list):
                return sorted({str(x).strip().casefold() for x in obj if str(x).strip()})
        except Exception:
            pass
    parts = re.split(r"[|;,]", s)
    return sorted({re.sub(r"^[\[\]'\" ]+|[\[\]'\" ]+$", "", x).strip().casefold() for x in parts if x.strip()})


def _clean_track_id(value: Any) -> str | None:
    """Return a usable Spotify track ID or None for missing/NaN-like values."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    s = str(value).strip()
    if not s or s.casefold() in {"nan", "none", "null", "<na>"}:
        return None
    return s


def _identity(title: str, artist: str, track_id: str | None = None) -> str:
    """Canonical song-level key used while combining heterogeneous sources.

    We intentionally key primarily by normalized title + main artist rather than by a
    Spotify recording ID. The same song often has multiple Spotify IDs across singles,
    albums, remasters, and regional releases, while some high-value sources expose no ID.
    Recording IDs are still preserved as provenance fields and used by downstream exact
    matching.
    """
    nt, na = norm(title), norm(artist)
    if nt and na:
        return f"text:{nt}:{na}"
    tid = _clean_track_id(track_id)
    return f"spotify:{tid}" if tid else f"text:{nt}:{na}"


def _selection_aliases(r: pd.Series) -> tuple[str, ...]:
    """Return every identity alias that must be unique inside one ranked song list.

    Catalog aggregation is song-level by normalized title + main artist, but imperfect
    upstream metadata can leave the same Spotify recording ID attached to two different
    text labels. Selection must therefore guard both the song text identity and stable
    recording identifiers. Otherwise a fixed-size list can contain duplicate Spotify IDs
    even though its text keys are unique.
    """
    aliases: list[str] = []
    text = _identity(str(r.get("title") or ""), str(r.get("main_artist") or ""))
    if text not in {"text::", "text:"}:
        aliases.append(text)
    tid = _clean_track_id(r.get("track_id"))
    if tid:
        aliases.append(f"spotify:{tid}")
    isrc = _clean_track_id(r.get("isrc"))
    if isrc:
        aliases.append(f"isrc:{isrc.upper()}")
    return tuple(dict.fromkeys(aliases))


def _claim_selection(r: pd.Series, used_aliases: set[str]) -> bool:
    """Atomically reserve a catalog row for a list, rejecting any alias collision."""
    aliases = _selection_aliases(r)
    if not aliases or any(alias in used_aliases for alias in aliases):
        return False
    used_aliases.update(aliases)
    return True


def _md5(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def download_file(url: str, dest: Path, *, md5: str | None = None, timeout: int = 180) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        if md5 is None or _md5(dest) == md5:
            return dest
        dest.unlink()
    tmp = dest.with_suffix(dest.suffix + ".part")
    headers = {"User-Agent": "BeatHit-Dataset/1.0 (+https://github.com/Dummy1-sudo/BeatHit-Dataset)"}
    last: Exception | None = None
    for attempt in range(5):
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=timeout, headers=headers) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes(1024 * 1024):
                        f.write(chunk)
            tmp.replace(dest)
            if md5 and _md5(dest) != md5:
                raise RuntimeError(f"MD5 mismatch for {dest.name}")
            return dest
        except Exception as exc:
            last = exc
            if tmp.exists():
                tmp.unlink()
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"Failed to download {url}: {last}")


def _find_file(root: Path, preferred_names: Iterable[str]) -> Path | None:
    names = {x.casefold() for x in preferred_names}
    for p in root.rglob("*"):
        if p.is_file() and p.name.casefold() in names:
            return p
    return None


def acquire_sources(status: BuildStatus, *, skip_zenodo: bool = False) -> dict[str, Path]:
    """Acquire public source snapshots. Failures degrade to documented fallbacks."""
    RAW.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    def attempt(name: str, url: str, dest: Path, md5: str | None = None) -> None:
        try:
            p = download_file(url, dest, md5=md5)
            paths[name] = p
            status.sources[name] = {"url": url, "path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size, "ok": True}
        except Exception as exc:
            status.sources[name] = {"url": url, "ok": False, "error": str(exc)}
            status.warnings.append(f"{name}: {exc}")

    def attempt_many(name: str, urls: list[str], dest: Path, md5: str | None = None) -> None:
        errors=[]
        for url in urls:
            try:
                p=download_file(url,dest,md5=md5)
                paths[name]=p
                status.sources[name]={"url":url,"mirrors":urls,"path":str(p.relative_to(ROOT)),"bytes":p.stat().st_size,"ok":True}
                return
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        msg=" | ".join(errors)
        status.sources[name]={"urls":urls,"ok":False,"error":msg}
        status.warnings.append(f"{name}: {msg}")

    if not skip_zenodo and os.getenv("BEATHIT_SKIP_ZENODO", "0") != "1":
        attempt_many("spotify_zenodo_0_9m", ZENODO_SPOTIFY_URLS, RAW / "spotify_zenodo_0_9m.csv", ZENODO_SPOTIFY_MD5)
    attempt("spotify_114k", SPOTIFY_114K_URL, RAW / "spotify_114k.csv")
    attempt("spotify_2024_crossplatform", SPOTIFY_2024_URL, RAW / "spotify_2024_crossplatform.csv")
    attempt("mal_theme_fallback", MAL_THEME_FALLBACK_URL, RAW / "mal_anime_theme_fallback.csv")

    # Public Kaggle dataset, used to guarantee dense historical decade coverage. kagglehub
    # supports anonymous downloads for public datasets in normal environments.
    try:
        import kagglehub  # type: ignore
        root = Path(kagglehub.dataset_download("yamaerenay/spotify-dataset-19212020-600k-tracks"))
        p = _find_file(root, ["tracks.csv"])
        if p:
            target = RAW / "spotify_600k_historical.csv"
            if not target.exists():
                try:
                    os.link(p, target)
                except OSError:
                    shutil.copy2(p, target)
            paths["spotify_600k_historical"] = target
            status.sources["spotify_600k_historical"] = {
                "url": "https://www.kaggle.com/datasets/yamaerenay/spotify-dataset-19212020-600k-tracks",
                "path": str(target.relative_to(ROOT)), "bytes": target.stat().st_size, "ok": True,
            }
        else:
            raise FileNotFoundError("tracks.csv not found in Kaggle dataset")
    except Exception as exc:
        status.sources["spotify_600k_historical"] = {"ok": False, "error": str(exc)}
        status.warnings.append(f"spotify_600k_historical: {exc}")

    # A newer ~1M-track feature/genre snapshot improves 2025-era genre and Spotify-popularity
    # metadata even though it does not expose cumulative stream counts.
    try:
        import kagglehub  # type: ignore
        root = Path(kagglehub.dataset_download("anantsinghal786/almost-million-songs-dataset-2025-16-features"))
        candidates=[p for p in root.rglob("*") if p.is_file() and p.suffix.casefold() in {".csv",".txt"}]
        p=max(candidates,key=lambda x:x.stat().st_size) if candidates else None
        if not p: raise FileNotFoundError("No CSV/TXT found in 2025 almost-million-track dataset")
        target=RAW/"spotify_2025_0_9m_features.csv"
        if not target.exists():
            try: os.link(p,target)
            except OSError: shutil.copy2(p,target)
        paths["spotify_2025_0_9m_features"]=target
        status.sources["spotify_2025_0_9m_features"]={
          "url":"https://www.kaggle.com/datasets/anantsinghal786/almost-million-songs-dataset-2025-16-features",
          "path":str(target.relative_to(ROOT)),"bytes":target.stat().st_size,"ok":True}
    except Exception as exc:
        status.sources["spotify_2025_0_9m_features"]={"ok":False,"error":str(exc)}
        status.warnings.append(f"spotify_2025_0_9m_features: {exc}")

    # The user's already bundled 2023 top-streamed snapshot is a useful independent signal.
    bundled = DATA / "raw" / "spotify_top10000_streamed_songs_2023.csv"
    if bundled.exists():
        paths["spotify_top10000_2023"] = bundled
        status.sources["spotify_top10000_2023"] = {
            "path": str(bundled.relative_to(ROOT)), "bytes": bundled.stat().st_size,
            "url": "bundled historical public snapshot; see docs/SOURCES.md", "ok": True,
        }
    status.save()
    return paths


def _csv_columns(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return next(csv.reader(f))


def _choose(cols: list[str], *names: str) -> str | None:
    low = {c.strip().casefold(): c for c in cols}
    for n in names:
        if n.casefold() in low:
            return low[n.casefold()]
    return None


def _read_chunks(path: Path, source_name: str, chunksize: int = 100_000) -> Iterator[pd.DataFrame]:
    """Normalize heterogeneous Spotify snapshots into a stable catalog schema."""
    cols = _csv_columns(path)
    # Map common source column names.
    title = _choose(cols, "name", "track_name", "Song Name", "Track")
    artists = _choose(cols, "track_artists", "artists", "Artist Name", "Artist")
    tid = _choose(cols, "track_id", "id", "Spotify Track Id")
    isrc = _choose(cols, "isrc", "ISRC")
    album = _choose(cols, "album_name", "Album Name", "Album")
    date_col = _choose(cols, "album_release_date", "release_date", "Release Date")
    year_col = _choose(cols, "year", "release_year")
    genres = _choose(cols, "genres", "track_genre", "Genre")
    streams = _choose(cols, "streams", "Spotify Streams", "Total Streams")
    daily_streams = _choose(cols, "daily_streams", "Daily Streams", "Daily")
    popularity = _choose(cols, "popularity", "Spotify Popularity")
    artist_pop = _choose(cols, "artist_popularity")
    followers = _choose(cols, "artist_followers")
    all_time_rank = _choose(cols, "All Time Rank")
    track_score = _choose(cols, "Track Score")
    youtube_views = _choose(cols, "YouTube Views")
    tiktok_views = _choose(cols, "TikTok Views")

    if not title or not artists:
        raise ValueError(f"Unrecognized song source schema for {path}: missing title/artists")
    usecols = [x for x in {title, artists, tid, isrc, album, date_col, year_col, genres, streams, daily_streams, popularity,
                           artist_pop, followers, all_time_rank, track_score, youtube_views, tiktok_views} if x]
    for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunksize, low_memory=False, encoding_errors="replace"):
        out = pd.DataFrame(index=chunk.index)
        out["track_id"] = chunk[tid].fillna("").astype(str) if tid else ""
        out["isrc"] = chunk[isrc].fillna("").astype(str) if isrc else ""
        out["title"] = chunk[title].fillna("").astype(str)
        out["artists"] = chunk[artists].fillna("").astype(str)
        out["main_artist"] = out["artists"].map(lambda x: (_split_artist_string(x) or [x])[0])
        out["album_name"] = chunk[album].fillna("").astype(str) if album else ""
        if date_col:
            out["release_date"] = chunk[date_col].fillna("").astype(str)
            out["release_year"] = out["release_date"].map(_parse_year)
        elif year_col:
            out["release_date"] = ""
            out["release_year"] = pd.to_numeric(chunk[year_col], errors="coerce")
        else:
            out["release_date"] = ""
            out["release_year"] = None
        out["genres"] = chunk[genres].fillna("").astype(str) if genres else ""
        out["streams"] = pd.to_numeric(chunk[streams].astype(str).str.replace(",", "", regex=False), errors="coerce") if streams else None
        # Preserve whether a stream field is a cumulative song counter or a chart-window
        # aggregate. The bundled 2023 chart-history file calls its field ``Total Streams``
        # but those totals reflect the chart snapshot, so it must never be used as a
        # lifetime-count qualification signal (notably for the Vocaloid >=10M rule).
        stream_meta = {
            "spotify_zenodo_0_9m": ("spotify_streams", True, "2024-06-03"),
            "spotify_2024_crossplatform": ("spotify_streams_snapshot", True, "2024"),
            "spotify_top10000_2023": ("spotify_chart_streams_snapshot", False, "2023"),
        }.get(source_name, ("spotify_streams", True, None))
        out["streams_metric_name"] = stream_meta[0] if streams else None
        out["streams_is_cumulative"] = bool(stream_meta[1]) if streams else False
        out["streams_snapshot_date"] = stream_meta[2] if streams else None
        # A stricter count channel is retained for threshold-sensitive classification.
        # The Zenodo snapshot was sampled from Spotify API data; the cross-platform gist is
        # useful ranking evidence but contains known outliers, so it is not the sole basis for
        # a hard >=10M qualification. Live Kworb overlays populate this channel later.
        trusted_stream_source = source_name == "spotify_zenodo_0_9m"
        out["trusted_cumulative_streams"] = out["streams"] if (streams and stream_meta[1] and trusted_stream_source) else None
        out["trusted_streams_snapshot_date"] = stream_meta[2] if (streams and trusted_stream_source) else None
        out["daily_streams"] = pd.to_numeric(chunk[daily_streams].astype(str).str.replace(",", "", regex=False), errors="coerce") if daily_streams else None
        out["popularity"] = pd.to_numeric(chunk[popularity], errors="coerce") if popularity else None
        out["artist_popularity"] = pd.to_numeric(chunk[artist_pop], errors="coerce") if artist_pop else None
        out["artist_followers"] = pd.to_numeric(chunk[followers], errors="coerce") if followers else None
        out["all_time_rank"] = pd.to_numeric(chunk[all_time_rank].astype(str).str.replace(",", "", regex=False), errors="coerce") if all_time_rank else None
        out["track_score"] = pd.to_numeric(chunk[track_score], errors="coerce") if track_score else None
        out["youtube_views"] = pd.to_numeric(chunk[youtube_views].astype(str).str.replace(",", "", regex=False), errors="coerce") if youtube_views else None
        out["tiktok_views"] = pd.to_numeric(chunk[tiktok_views].astype(str).str.replace(",", "", regex=False), errors="coerce") if tiktok_views else None
        out["source_dataset"] = source_name
        out["source_url"] = {
            "spotify_zenodo_0_9m": "https://doi.org/10.5281/zenodo.11453410",
            "spotify_114k": SPOTIFY_114K_SOURCE,
            "spotify_2025_0_9m_features": "https://www.kaggle.com/datasets/anantsinghal786/almost-million-songs-dataset-2025-16-features",
            "spotify_2024_crossplatform": SPOTIFY_2024_SOURCE,
            "spotify_600k_historical": "https://www.kaggle.com/datasets/yamaerenay/spotify-dataset-19212020-600k-tracks",
            "spotify_top10000_2023": "data/raw/spotify_top10000_streamed_songs_2023.csv",
        }.get(source_name, source_name)
        # Metric-specific provenance columns survive cross-source aggregation so a value
        # is never cited to whichever metadata row happened to win the representative-row
        # tie-break.
        out["streams_source_url"] = out["source_url"] if streams else None
        out["trusted_streams_source_url"] = out["source_url"] if (streams and trusted_stream_source) else None
        out["daily_streams_source_url"] = out["source_url"] if daily_streams else None
        out["popularity_source_url"] = out["source_url"] if popularity else None
        out["track_score_source_url"] = out["source_url"] if track_score else None
        out["youtube_views_source_url"] = out["source_url"] if youtube_views else None
        out["all_time_rank_source_url"] = out["source_url"] if all_time_rank else None
        out = out[(out["title"].str.strip() != "") & (out["main_artist"].str.strip() != "")]
        yield out


def build_catalog(sources: dict[str, Path], status: BuildStatus) -> Path:
    """Build a compact normalized Parquet catalog from all available song sources."""
    out_path = CACHE / "catalog.parquet"
    CACHE.mkdir(parents=True, exist_ok=True)
    parts_dir = CACHE / "catalog_parts"
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True)
    part = 0
    total = 0
    for source_name in [
        "spotify_zenodo_0_9m", "spotify_2025_0_9m_features", "spotify_600k_historical", "spotify_114k",
        "spotify_2024_crossplatform", "spotify_top10000_2023",
    ]:
        p = sources.get(source_name)
        if not p or not p.exists():
            continue
        try:
            for df in _read_chunks(p, source_name):
                df["_key"] = [
                    _identity(t, a, _clean_track_id(tid))
                    for t, a, tid in zip(df.title, df.main_artist, df.track_id)
                ]
                df.to_parquet(parts_dir / f"part-{part:05d}.parquet", index=False)
                total += len(df)
                part += 1
        except Exception as exc:
            status.warnings.append(f"catalog source {source_name}: {exc}")
    if not part:
        raise RuntimeError("No usable song catalog sources were acquired")

    # Use DuckDB when available for fast out-of-core deduplication and aggregation.
    try:
        import duckdb  # type: ignore
        con = duckdb.connect(str(CACHE / "catalog.duckdb"))
        glob = str(parts_dir / "*.parquet").replace("'", "''")
        con.execute("DROP TABLE IF EXISTS tracks")
        con.execute(f"""
            CREATE TABLE tracks AS
            WITH x AS (SELECT * FROM read_parquet('{glob}', union_by_name=true)),
            ranked AS (
              SELECT *,
                ROW_NUMBER() OVER (
                  PARTITION BY _key
                  ORDER BY (streams IS NOT NULL AND streams_is_cumulative) DESC,
                           streams DESC NULLS LAST,
                           (source_dataset='spotify_zenodo_0_9m') DESC,
                           popularity DESC NULLS LAST
                ) AS rn
              FROM x
            ), genres_agg AS (
              SELECT _key,
                     max(NULLIF(track_id, '')) AS any_track_id,
                     max(NULLIF(isrc, '')) AS any_isrc,
                     string_agg(DISTINCT genres, '|') FILTER (WHERE genres IS NOT NULL AND genres <> '') AS all_genres,
                     COALESCE(
                       max(streams) FILTER (WHERE streams_is_cumulative),
                       max(streams)
                     ) AS best_streams,
                     COALESCE(
                       arg_max(streams_metric_name, streams) FILTER (WHERE streams_is_cumulative),
                       arg_max(streams_metric_name, streams)
                     ) AS best_streams_metric_name,
                     COALESCE(
                       arg_max(streams_source_url, streams) FILTER (WHERE streams_is_cumulative),
                       arg_max(streams_source_url, streams)
                     ) AS best_streams_source_url,
                     COALESCE(
                       arg_max(streams_snapshot_date, streams) FILTER (WHERE streams_is_cumulative),
                       arg_max(streams_snapshot_date, streams)
                     ) AS best_streams_snapshot_date,
                     COALESCE(
                       bool_or(streams_is_cumulative) FILTER (WHERE streams IS NOT NULL),
                       FALSE
                     ) AS has_cumulative_streams,
                     max(trusted_cumulative_streams) AS trusted_cumulative_streams,
                     arg_max(trusted_streams_source_url, trusted_cumulative_streams) AS trusted_streams_source_url,
                     arg_max(trusted_streams_snapshot_date, trusted_cumulative_streams) AS trusted_streams_snapshot_date,
                     max(daily_streams) AS max_daily_streams,
                     arg_max(daily_streams_source_url, daily_streams) AS daily_streams_source_url,
                     max(popularity) AS max_popularity,
                     arg_max(popularity_source_url, popularity) AS popularity_source_url,
                     max(artist_popularity) AS max_artist_popularity,
                     max(artist_followers) AS max_artist_followers,
                     max(track_score) AS max_track_score,
                     arg_max(track_score_source_url, track_score) AS track_score_source_url,
                     max(youtube_views) AS max_youtube_views,
                     arg_max(youtube_views_source_url, youtube_views) AS youtube_views_source_url,
                     max(tiktok_views) AS max_tiktok_views,
                     min(all_time_rank) AS min_all_time_rank,
                     arg_min(all_time_rank_source_url, all_time_rank) AS all_time_rank_source_url,
                     min(TRY_CAST(release_year AS INTEGER)) FILTER (WHERE TRY_CAST(release_year AS INTEGER) BETWEEN 1800 AND 2100) AS earliest_release_year,
                     string_agg(DISTINCT source_dataset, '|') AS source_datasets
              FROM x GROUP BY _key
            )
            SELECT COALESCE(g.any_track_id, r.track_id) AS track_id, g.any_isrc AS isrc,
                   r.title, r.artists, r.main_artist, r.album_name, r.release_date,
                   COALESCE(g.earliest_release_year, TRY_CAST(r.release_year AS INTEGER)) AS release_year,
                   COALESCE(g.all_genres, r.genres) AS genres,
                   g.best_streams AS streams,
                   g.best_streams_metric_name AS streams_metric_name,
                   g.has_cumulative_streams AS streams_is_cumulative,
                   g.best_streams_snapshot_date AS streams_snapshot_date,
                   g.best_streams_source_url AS streams_source_url,
                   g.trusted_cumulative_streams AS trusted_cumulative_streams,
                   g.trusted_streams_source_url AS trusted_streams_source_url,
                   g.trusted_streams_snapshot_date AS trusted_streams_snapshot_date,
                   g.max_daily_streams AS daily_streams,
                   g.daily_streams_source_url AS daily_streams_source_url,
                   g.max_popularity AS popularity,
                   g.popularity_source_url AS popularity_source_url,
                   g.max_artist_popularity AS artist_popularity,
                   g.max_artist_followers AS artist_followers,
                   g.min_all_time_rank AS all_time_rank,
                   g.all_time_rank_source_url AS all_time_rank_source_url,
                   g.max_track_score AS track_score,
                   g.track_score_source_url AS track_score_source_url,
                   g.max_youtube_views AS youtube_views,
                   g.youtube_views_source_url AS youtube_views_source_url,
                   g.max_tiktok_views AS tiktok_views,
                   r.source_dataset, g.source_datasets, r.source_url, r._key
            FROM ranked r JOIN genres_agg g USING(_key) WHERE r.rn=1
        """)
        con.execute(f"COPY tracks TO '{str(out_path).replace("'", "''")}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        n = con.execute("SELECT count(*) FROM tracks").fetchone()[0]
        con.close()
    except Exception as exc:
        status.warnings.append(f"duckdb catalog aggregation unavailable; pandas fallback used: {exc}")
        frames = [pd.read_parquet(p) for p in sorted(parts_dir.glob("*.parquet"))]
        df = pd.concat(frames, ignore_index=True)

        # Pandas fallback must preserve *independent metric channels* across duplicate
        # source rows.  A higher, less-trusted snapshot may legitimately win the general
        # ``streams`` channel while a lower value from a trusted cumulative source must
        # remain available in ``trusted_cumulative_streams``.  Simply keeping one whole
        # representative row (the old behavior) silently discarded that trusted value.
        #
        # Match the DuckDB path above: choose one representative metadata row, then
        # aggregate each metric/provenance pair independently at song-key level.
        df["_has_cumulative_streams"] = (
            df["streams"].notna() & df["streams_is_cumulative"].fillna(False).astype(bool)
        )
        df["_zenodo_preferred"] = (df["source_dataset"] == "spotify_zenodo_0_9m").astype(int)
        reps = (
            df.sort_values(
                ["_key", "_has_cumulative_streams", "streams", "_zenodo_preferred", "popularity"],
                ascending=[True, False, False, False, False],
                na_position="last",
            )
            .drop_duplicates("_key", keep="first")
            .copy()
        )

        keys_with_cumulative = set(df.loc[df["_has_cumulative_streams"], "_key"].astype(str))
        eligible_streams = df[
            df["_has_cumulative_streams"] | ~df["_key"].astype(str).isin(keys_with_cumulative)
        ]
        best_streams = (
            eligible_streams.dropna(subset=["streams"])
            .sort_values(["_key", "streams"], ascending=[True, False], na_position="last")
            .drop_duplicates("_key", keep="first")
            .set_index("_key")
        )
        best_trusted = (
            df.dropna(subset=["trusted_cumulative_streams"])
            .sort_values(["_key", "trusted_cumulative_streams"], ascending=[True, False], na_position="last")
            .drop_duplicates("_key", keep="first")
            .set_index("_key")
        )

        def _metric_best(value_col: str, *, ascending: bool = False) -> pd.DataFrame:
            if value_col not in df.columns:
                return pd.DataFrame()
            return (
                df.dropna(subset=[value_col])
                .sort_values(["_key", value_col], ascending=[True, ascending], na_position="last")
                .drop_duplicates("_key", keep="first")
                .set_index("_key")
            )

        metric_specs = [
            ("daily_streams", "daily_streams_source_url", False),
            ("popularity", "popularity_source_url", False),
            ("artist_popularity", None, False),
            ("artist_followers", None, False),
            ("track_score", "track_score_source_url", False),
            ("youtube_views", "youtube_views_source_url", False),
            ("tiktok_views", None, False),
            ("all_time_rank", "all_time_rank_source_url", True),
        ]
        metric_best = {col: _metric_best(col, ascending=asc) for col, _, asc in metric_specs}

        reps = reps.set_index("_key", drop=False)
        for key, row in best_streams.iterrows():
            if key not in reps.index:
                continue
            for col in [
                "streams", "streams_metric_name", "streams_is_cumulative",
                "streams_snapshot_date", "streams_source_url",
            ]:
                if col in row.index:
                    reps.at[key, col] = row[col]

        for key, row in best_trusted.iterrows():
            if key not in reps.index:
                continue
            for col in [
                "trusted_cumulative_streams", "trusted_streams_source_url",
                "trusted_streams_snapshot_date",
            ]:
                if col in row.index:
                    reps.at[key, col] = row[col]

        for value_col, source_col, _ in metric_specs:
            best = metric_best[value_col]
            if best.empty:
                continue
            for key, row in best.iterrows():
                if key not in reps.index:
                    continue
                reps.at[key, value_col] = row[value_col]
                if source_col and source_col in row.index:
                    reps.at[key, source_col] = row[source_col]

        # Preserve stable IDs, merged genres/source membership, and the earliest plausible
        # release year instead of inheriting them solely from the representative row.
        def _first_nonempty(series: pd.Series) -> str | None:
            for value in series:
                if value is None:
                    continue
                text = str(value).strip()
                if text and text.casefold() not in {"nan", "none", "null", "<na>"}:
                    return text
            return None

        def _join_unique(series: pd.Series, sep: str = "|") -> str:
            vals: list[str] = []
            seen: set[str] = set()
            for value in series:
                if value is None:
                    continue
                for part in str(value).split("|"):
                    part = part.strip()
                    if not part or part.casefold() in {"nan", "none", "null"}:
                        continue
                    if part not in seen:
                        seen.add(part); vals.append(part)
            return sep.join(vals)

        grouped = df.groupby("_key", sort=False)
        any_track_id = grouped["track_id"].agg(_first_nonempty)
        any_isrc = grouped["isrc"].agg(_first_nonempty)
        all_genres = grouped["genres"].agg(_join_unique)
        source_datasets = grouped["source_dataset"].agg(_join_unique)
        release_year_num = pd.to_numeric(df["release_year"], errors="coerce")
        plausible_year = release_year_num.where(release_year_num.between(1800, 2100))
        earliest_year = plausible_year.groupby(df["_key"]).min()

        for key in reps.index:
            if key in any_track_id.index and any_track_id.loc[key]:
                reps.at[key, "track_id"] = any_track_id.loc[key]
            if key in any_isrc.index and any_isrc.loc[key]:
                reps.at[key, "isrc"] = any_isrc.loc[key]
            if key in all_genres.index and all_genres.loc[key]:
                reps.at[key, "genres"] = all_genres.loc[key]
            if key in source_datasets.index:
                reps.at[key, "source_datasets"] = source_datasets.loc[key]
            if key in earliest_year.index and pd.notna(earliest_year.loc[key]):
                reps.at[key, "release_year"] = int(earliest_year.loc[key])

        df = reps.reset_index(drop=True).drop(
            columns=["_has_cumulative_streams", "_zenodo_preferred"], errors="ignore"
        )
        df.to_parquet(out_path, index=False)
        n = len(df)
    status.sources["normalized_catalog"] = {"path": str(out_path.relative_to(ROOT)), "rows": int(n), "input_rows": int(total), "ok": True}
    status.save()
    return out_path


def load_catalog(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)



def fetch_kworb_global(status: BuildStatus) -> pd.DataFrame:
    """Fetch the live Kworb Spotify all-time table as a freshness overlay.

    Kworb is used only for rows it actually publishes; it does not replace the larger
    catalog snapshots. The raw count is attributed to the live page and retrieval date.
    """
    url = KWORB_ALLTIME_URL
    try:
        from bs4 import BeautifulSoup
        with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent":"BeatHit-Dataset/1.0"}) as c:
            r=c.get(url); r.raise_for_status(); html=r.text
        soup=BeautifulSoup(html,"lxml")
        rows=[]
        for tr in soup.select("table tr"):
            cells=[x.get_text(" ",strip=True) for x in tr.find_all(["td","th"])]
            if len(cells)<2: continue
            # Common layout: [Artist and Title, Streams, Daily] or [rank, Artist and Title, Streams, Daily].
            nums=[]
            for j,cval in enumerate(cells):
                n=re.sub(r"[^0-9]","",cval)
                if n and len(n)>=5: nums.append((j,int(n)))
            if not nums: continue
            stream_idx,streams=max(nums,key=lambda x:x[1])
            text_cells=[(j,x) for j,x in enumerate(cells) if j!=stream_idx and not re.fullmatch(r"[0-9, .]+",x)]
            if not text_cells: continue
            credit=text_cells[0][1]
            if " - " in credit:
                artist,title=credit.split(" - ",1)
            elif len(text_cells)>=2:
                artist,title=text_cells[0][1],text_cells[1][1]
            else:
                continue
            daily=None
            if stream_idx+1<len(cells): daily=_safe_int(cells[stream_idx+1])
            rows.append({"title":title.strip(),"main_artist":artist.strip(),"streams":streams,"daily_streams":daily,"source_url":url})
        df=pd.DataFrame(rows).drop_duplicates(["title","main_artist"]) if rows else pd.DataFrame(columns=["title","main_artist","streams","daily_streams","source_url"])
        status.sources["kworb_live_spotify"]={"url":url,"rows":len(df),"retrieved_at":TODAY,"ok":bool(len(df))}
        return df
    except Exception as exc:
        status.sources["kworb_live_spotify"]={"url":url,"ok":False,"error":str(exc)}
        status.warnings.append(f"Kworb live overlay: {exc}")
        return pd.DataFrame(columns=["title","main_artist","streams","daily_streams","source_url"])


def fetch_kworb_daily(status: BuildStatus) -> pd.DataFrame:
    """Fetch the current Spotify global daily chart as a freshness signal.

    Daily counts are kept distinct from lifetime counts. The chart is especially useful for
    newly released 2026 tracks that have not yet propagated into older bulk snapshots.
    """
    url = KWORB_DAILY_URL
    try:
        tables = pd.read_html(url)
        if not tables:
            raise RuntimeError("no tables found")
        df = max(tables, key=len).copy()
        cols = {str(c).strip().casefold(): c for c in df.columns}
        artist_col = next((c for k,c in cols.items() if "artist" in k), None)
        title_col = next((c for k,c in cols.items() if "title" in k), None)
        streams_col = next((c for k,c in cols.items() if k == "streams" or "streams" in k), None)
        total_col = next((c for k,c in cols.items() if "total" in k and "stream" in k), None)
        if not artist_col or not title_col or not streams_col:
            # Some Kworb table versions combine artist and title in one column.
            first = df.columns[1] if len(df.columns) > 1 else df.columns[0]
            rows=[]
            for _,r in df.iterrows():
                credit=str(r.get(first) or "").strip()
                if " - " not in credit: continue
                artist,title=credit.split(" - ",1)
                daily=_safe_int(r.get(streams_col)) if streams_col else None
                total=_safe_int(r.get(total_col)) if total_col else None
                if daily is not None: rows.append({"title":title,"main_artist":artist,"daily_streams":daily,"streams":total,"source_url":url})
            out=pd.DataFrame(rows)
        else:
            out=pd.DataFrame({
                "title":df[title_col].astype(str).str.strip(),
                "main_artist":df[artist_col].astype(str).str.strip(),
                "daily_streams":df[streams_col].map(_safe_int),
                "streams":df[total_col].map(_safe_int) if total_col else None,
                "source_url":url,
            })
        out=out.dropna(subset=["daily_streams"]).drop_duplicates(["title","main_artist"])
        status.sources["kworb_global_daily"]={"url":url,"rows":len(out),"retrieved_at":TODAY,"ok":bool(len(out))}
        return out
    except Exception as exc:
        status.sources["kworb_global_daily"]={"url":url,"ok":False,"error":str(exc)}
        status.warnings.append(f"Kworb global daily: {exc}")
        return pd.DataFrame(columns=["title","main_artist","daily_streams","streams","source_url"])


def apply_kworb_overlay(catalog: pd.DataFrame, kworb: pd.DataFrame) -> pd.DataFrame:
    if kworb.empty: return catalog
    out=catalog.copy()
    pair_map=defaultdict(list)
    for i,r in out.iterrows(): pair_map[(norm(str(r.title)),norm(str(r.main_artist)))].append(i)
    append=[]
    for _,k in kworb.iterrows():
        key=(norm(str(k.title)),norm(str(k.main_artist))); ids=pair_map.get(key,[])
        if ids:
            for idx in ids:
                incoming_streams=_safe_float(k.get("streams"))
                old=_safe_float(out.at[idx,"streams"]) or 0
                if incoming_streams is not None and incoming_streams>old:
                    out.at[idx,"streams"]=incoming_streams
                    out.at[idx,"streams_metric_name"]="spotify_streams"
                    out.at[idx,"streams_is_cumulative"]=True
                    out.at[idx,"streams_snapshot_date"]=TODAY
                    out.at[idx,"streams_source_url"]=str(k.source_url)
                    out.at[idx,"trusted_cumulative_streams"]=incoming_streams
                    out.at[idx,"trusted_streams_source_url"]=str(k.source_url)
                    out.at[idx,"trusted_streams_snapshot_date"]=TODAY
                    out.at[idx,"source_dataset"]="kworb_spotify_overlay"
                if "daily_streams" in out.columns and _safe_float(k.get("daily_streams")) is not None:
                    out.at[idx,"daily_streams"]=_safe_float(k.get("daily_streams"))
                    out.at[idx,"daily_streams_source_url"]=str(k.source_url)
        else:
            append.append({"track_id":"","isrc":"","title":k.title,"artists":k.main_artist,"main_artist":k.main_artist,"album_name":"",
                           "release_date":"","release_year":None,"genres":"","streams":_safe_float(k.get("streams")),
                           "streams_metric_name":"spotify_streams","streams_is_cumulative":True,"streams_snapshot_date":TODAY,
                           "streams_source_url":k.source_url,"trusted_cumulative_streams":_safe_float(k.get("streams")),
                           "trusted_streams_source_url":k.source_url,"trusted_streams_snapshot_date":TODAY,
                           "daily_streams":_safe_float(k.get("daily_streams")),
                           "daily_streams_source_url":k.source_url,"popularity":None,"popularity_source_url":None,
                           "artist_popularity":None,"artist_followers":None,"all_time_rank":None,"all_time_rank_source_url":None,"track_score":None,
                           "track_score_source_url":None,"youtube_views":None,"youtube_views_source_url":None,"tiktok_views":None,"source_dataset":"kworb_live_spotify_overlay",
                           "source_url":k.source_url,"_key":_identity(k.title,k.main_artist)})
    if append: out=pd.concat([out,pd.DataFrame(append)],ignore_index=True)
    return out

def _best_metric(r: pd.Series) -> tuple[str, float, str]:
    streams = _safe_float(r.get("streams"))
    if streams is not None and streams >= 0:
        metric_name=str(r.get("streams_metric_name") or "spotify_streams").strip()
        if metric_name.casefold() in {"nan","none",""}: metric_name="spotify_streams"
        return metric_name, streams, "streams"
    daily = _safe_float(r.get("daily_streams"))
    if daily is not None and daily >= 0:
        return "spotify_daily_streams", daily, "streams"
    yt = _safe_float(r.get("youtube_views"))
    if yt is not None and yt >= 0:
        return "youtube_views", yt, "views"
    score = _safe_float(r.get("track_score"))
    if score is not None:
        return "cross_platform_track_score", score, "score"
    pop = _safe_float(r.get("popularity"))
    if pop is not None:
        return "spotify_popularity", pop, "score_0_100"
    rank = _safe_float(r.get("all_time_rank"))
    if rank and rank > 0:
        return "cross_platform_all_time_rank_inverse", 1_000_000.0 / rank, "rank_score"
    return "source_rank_score", 0.0, "score"


def _metric_source_url(r: pd.Series, metric_name: str) -> str:
    mapping = {
        "spotify_streams": "streams_source_url",
        "spotify_streams_snapshot": "streams_source_url",
        "spotify_chart_streams_snapshot": "streams_source_url",
        "spotify_daily_streams": "daily_streams_source_url",
        "youtube_views": "youtube_views_source_url",
        "cross_platform_track_score": "track_score_source_url",
        "spotify_popularity": "popularity_source_url",
        "cross_platform_all_time_rank_inverse": "all_time_rank_source_url",
    }
    col=mapping.get(metric_name)
    value=r.get(col) if col else None
    if value is not None and str(value).strip() not in {"", "nan", "None"}:
        return str(value).strip()
    return str(r.get("source_url") or "dataset source")


def _metric_source_note(r: pd.Series, metric_name: str, explicit: str | None) -> str | None:
    notes=[]
    if explicit: notes.append(explicit.strip())
    if metric_name == "spotify_chart_streams_snapshot":
        notes.append("Historical Spotify chart-window stream aggregate from the cited snapshot; not represented as a lifetime Spotify play count.")
    snap=r.get("streams_snapshot_date")
    if metric_name.startswith("spotify_") and snap is not None and str(snap).strip() not in {"", "nan", "None"}:
        notes.append(f"Metric snapshot/date label: {str(snap).strip()}.")
    return " ".join(x for x in notes if x) or None


def _score(r: pd.Series) -> float:
    streams = max(_safe_float(r.get("streams")) or 0.0, 0.0)
    pop = max(_safe_float(r.get("popularity")) or 0.0, 0.0)
    daily = max(_safe_float(r.get("daily_streams")) or 0.0, 0.0)
    track = max(_safe_float(r.get("track_score")) or 0.0, 0.0)
    yt = max(_safe_float(r.get("youtube_views")) or 0.0, 0.0)
    # Monotone composite used only to compare candidates. Raw evidence remains in columns.
    return (30 * min(math.log10(streams + 1) / 10, 1) + 25 * min(pop / 100, 1)
            + 20 * min(track / 1000, 1) + 15 * min(math.log10(yt + 1) / 11, 1)
            + 10 * min(math.log10(daily + 1) / 7, 1))


def _catalog_row_to_song(r: pd.Series, *, rank: int | None = None, extra: dict[str, Any] | None = None,
                         metric_override: tuple[str, float, str] | None = None, source_notes: str | None = None) -> SongRow:
    artists = _split_artist_string(r.get("artists"))
    main = str(r.get("main_artist") or (artists[0] if artists else "Unknown")).strip()
    metric = metric_override or _best_metric(r)
    genres = _parse_genres(r.get("genres"))
    return SongRow(
        rank=rank,
        title=str(r.get("title") or "").strip(),
        main_artist=main,
        featured_artists=artists[1:],
        album=(str(r.get("album_name") or "").strip() or None),
        release_date=(str(r.get("release_date") or "").strip() or None),
        release_year=_parse_year(r.get("release_year")) or _parse_year(r.get("release_date")),
        genres=genres,
        metric_name=metric[0], metric_value=float(metric[1]), metric_unit=metric[2],
        listen_count=(int(metric[1]) if metric[2] in {"streams","listens"} else None),
        listen_source=(("Spotify (daily count)" if metric[0]=="spotify_daily_streams" else "Spotify") if metric[0].startswith("spotify_") and metric[2]=="streams" else ("ListenBrainz" if metric[2]=="listens" else None)),
        view_count=(int(metric[1]) if metric[2]=="views" else None),
        overall_popularity_score=_score(r),
        spotify_track_id=_clean_track_id(r.get("track_id")),
        isrc=(str(r.get("isrc") or "").strip().upper() or None),
        source_url=_metric_source_url(r, metric[0]),
        retrieved_at=TODAY,
        source_notes=_metric_source_note(r, metric[0], source_notes),
        extra={**(extra or {}),
               **({"metric_snapshot_date": str(r.get("streams_snapshot_date"))} if metric[0].startswith("spotify_") and r.get("streams_snapshot_date") is not None and str(r.get("streams_snapshot_date")) not in {"","nan","None"} else {}),
               **({"streams_is_cumulative": bool(r.get("streams_is_cumulative"))} if _safe_float(r.get("streams")) is not None else {}),
               **({"daily_streams": _safe_int(r.get("daily_streams"))} if _safe_int(r.get("daily_streams")) is not None else {}),
               **({"catalog_sources": str(r.get("source_datasets"))} if r.get("source_datasets") is not None and str(r.get("source_datasets")) not in {"","nan","None"} else {})},
    )


def _rank_frame(df: pd.DataFrame, *, current: bool = False) -> pd.DataFrame:
    df = df.copy()
    # Current ranking emphasizes Spotify popularity and cross-platform track score;
    # historical ranking emphasizes cumulative streams.
    if current:
        daily = pd.to_numeric(df.get("daily_streams"), errors="coerce").fillna(0) if "daily_streams" in df.columns else 0
        df["_rank_score"] = (
            pd.to_numeric(df.get("popularity"), errors="coerce").fillna(0) * 1.0
            + pd.to_numeric(df.get("track_score"), errors="coerce").fillna(0).clip(upper=1000) * 0.08
            + (pd.to_numeric(df.get("streams"), errors="coerce").fillna(0) + 1).map(math.log10) * 3
            + ((daily + 1).map(math.log10) * 6 if hasattr(daily, "map") else 0)
        )
    else:
        df["_rank_score"] = (
            (pd.to_numeric(df.get("streams"), errors="coerce").fillna(0) + 1).map(math.log10) * 10
            + pd.to_numeric(df.get("popularity"), errors="coerce").fillna(0) * 0.25
            + pd.to_numeric(df.get("track_score"), errors="coerce").fillna(0) * 0.01
        )
    return df.sort_values(["_rank_score", "streams", "popularity"], ascending=False, na_position="last")


def build_worldwide(catalog: pd.DataFrame, status: BuildStatus) -> list[SongRow]:
    selected: list[SongRow] = []
    bucket_counts: dict[str, int] = {}
    # Duplicates are allowed across buckets (for example, a 2020s song can also be in the
    # current top 10k). This is intentional: the requested 51,000-row allocation is a set of
    # era selections. The final megalist performs global deduplication.
    for label, lo, hi, target in WORLDWIDE_BUCKETS:
        used: set[str] = set()
        if label == "current":
            ranked = _rank_frame(catalog.copy(), current=True)
        else:
            years = pd.to_numeric(catalog["release_year"], errors="coerce")
            ranked = _rank_frame(catalog[(years >= lo) & (years <= hi)].copy())
        bucket_rows: list[SongRow] = []
        for _, r in ranked.iterrows():
            if not _claim_selection(r, used):
                continue
            era_rank=len(bucket_rows)+1
            row=_catalog_row_to_song(r, rank=era_rank, extra={"era_bucket": label, "era_rank": era_rank})
            bucket_rows.append(row)
            if len(bucket_rows) >= target:
                break
        bucket_counts[label] = len(bucket_rows)
        # Each requested era is also materialized independently and is strictly sorted
        # most-popular to least-popular within that era.
        era_name = "current" if label == "current" else label
        write_rows(bucket_rows, DATA / "worldwide" / f"worldwide_{era_name}.csv")
        selected.extend(bucket_rows)

    # The combined 51k file is globally ordered by the same transparent composite evidence;
    # ``extra.era_rank`` retains the within-era rank for reproducibility.
    selected = _sort_and_rank(selected)
    out = DATA / "worldwide" / "worldwide_51000.csv"
    write_rows(selected, out)
    st = DatasetStatus(target=51_000, rows=len(selected), complete=len(selected) == 51_000,
                       notes=[f"bucket counts: {bucket_counts}",
                              "Combined file globally sorted; extra.era_rank preserves each bucket's internal rank."])
    st.metric_coverage = dict(_metric_counts(selected))
    status.datasets["worldwide"] = st
    status.save()
    return selected


def _metric_counts(rows: Iterable[SongRow]) -> Iterable[tuple[str, int]]:
    d: dict[str, int] = defaultdict(int)
    for r in rows:
        d[r.metric_name] += 1
    return sorted(d.items())


def _explode_genres(catalog: pd.DataFrame) -> dict[str, list[int]]:
    mapping: dict[str, list[int]] = defaultdict(list)
    for idx, val in catalog["genres"].items():
        for g in _parse_genres(val):
            if len(g) >= 2 and g not in {"unknown", "none", "nan"}:
                mapping[g].append(idx)
    return mapping


def build_genres(catalog: pd.DataFrame, status: BuildStatus) -> list[SongRow]:
    genre_map = _explode_genres(catalog)
    # Favor genres with both depth and stronger average popularity. Exclude obvious activity/mood
    # buckets when there are enough music genres.
    stop = {"sleep", "study", "work-out", "workout", "party", "chill", "happy", "sad"}
    ranked_genres = sorted(
        ((g, ids) for g, ids in genre_map.items() if len(ids) >= 100 and g not in stop),
        key=lambda kv: (min(len(kv[1]), 5000), kv[0]), reverse=True,
    )
    chosen_genres = [g for g, _ in ranked_genres[:50]]
    if len(chosen_genres) < 50:
        chosen_genres += [g for g, _ in ranked_genres[50:] if g not in chosen_genres][:50-len(chosen_genres)]
    pools: dict[str, list[int]] = {}
    for g in chosen_genres:
        ids = genre_map[g]
        pools[g] = list(_rank_frame(catalog.loc[ids]).index)
    used: set[str] = set(); selected: list[SongRow] = []
    # Round-robin: exactly 200 per genre where possible, then redistribute shortfalls.
    target_each = 200
    genre_counts = defaultdict(int)
    cursors = defaultdict(int)
    for g in chosen_genres:
        while genre_counts[g] < target_each and cursors[g] < len(pools[g]):
            idx = pools[g][cursors[g]]; cursors[g] += 1
            r = catalog.loc[idx]
            if not _claim_selection(r, used): continue
            genre_counts[g] += 1
            selected.append(_catalog_row_to_song(r, extra={"selection_genre": g}))
    # Redistribute any shortfall across the same >=50 genre pool without duplicates.
    for g in chosen_genres:
        while len(selected) < 10_000 and cursors[g] < len(pools[g]):
            idx = pools[g][cursors[g]]; cursors[g] += 1
            r = catalog.loc[idx]
            if not _claim_selection(r, used): continue
            genre_counts[g] += 1
            selected.append(_catalog_row_to_song(r, extra={"selection_genre": g, "redistributed": True}))
    selected = _sort_and_rank(selected)[:10_000]
    write_rows(selected, DATA / "genres" / "genres_10000.csv")
    status.datasets["genres"] = DatasetStatus(target=10_000, rows=len(selected), complete=len(selected)==10_000,
        metric_coverage=dict(_metric_counts(selected)), notes=[f"genres={len(chosen_genres)}", json.dumps(dict(genre_counts), ensure_ascii=False)])
    status.save(); return selected


def build_classical(catalog: pd.DataFrame, status: BuildStatus) -> list[SongRow]:
    def is_classical_row(r: pd.Series) -> bool:
        gs = _parse_genres(r.get("genres"))
        joined = " | ".join(gs).casefold()
        if any(term in joined for term in CLASSICAL_TERMS):
            return True
        text = f"{r.get('title','')} | {r.get('main_artist','')} | {r.get('album_name','')}".casefold()
        # Composer-name fallback is deliberately conservative: require a classical-form token,
        # catalogue marker, or explicit composer-style punctuation in the release metadata.
        composer_hit = any(name in text for name in CLASSICAL_COMPOSERS)
        form_hit = bool(re.search(r"\b(?:symphony|concerto|sonata|quartet|prelude|fugue|etude|étude|nocturne|mass|requiem|cantata|suite|opus|op\.|bwv|k\.|kv|hob\.|d\.\s*\d)\b", text, re.I))
        return composer_hit and form_hit
    mask = catalog.apply(is_classical_row, axis=1)
    cand = _rank_frame(catalog[mask].copy())
    selected: list[SongRow] = []
    used = set()
    for _, r in cand.iterrows():
        if not _claim_selection(r, used): continue
        row = _catalog_row_to_song(r, extra={"classical_filter": "genre_metadata"})
        # In classical metadata the performer is preserved as main_artist; composer is left null
        # unless a source explicitly provides it. We never guess composer from title strings.
        selected.append(row)
        if len(selected) >= 10_000: break
    # Source-backed fallback via ListenBrainz tag radio if catalog does not contain 10k.
    if len(selected) < 10_000:
        selected.extend(_listenbrainz_classical(10_000-len(selected), used, status))
    selected = _sort_and_rank(dedupe(selected))[:10_000]
    write_rows(selected, DATA / "classical" / "classical_10000.csv")
    status.datasets["classical"] = DatasetStatus(target=10_000, rows=len(selected), complete=len(selected)==10_000,
        metric_coverage=dict(_metric_counts(selected)), notes=["Composer is populated only when explicitly supplied by source; performer remains main_artist."])
    status.save(); return selected


def _listenbrainz_classical(limit: int, used: set[str], status: BuildStatus) -> list[SongRow]:
    if limit <= 0: return []
    # Keep fallback tags explicitly classical. Generic instrument tags such as ``piano`` or
    # ``violin`` have large pop/jazz overlap and would materially lower precision.
    tags = ["classical", "baroque", "romantic era", "opera", "orchestral", "chamber music",
            "classical piano", "symphony", "concerto", "choral", "renaissance",
            "contemporary classical", "early music", "classical guitar", "string quartet"]
    out: list[SongRow] = []
    client = httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent":"BeatHit-Dataset/1.0"})
    for tag in tags:
        if len(out) >= limit: break
        try:
            r = client.get(f"{LISTENBRAINZ_API}/lb-radio/tags", params={"tag":tag,"operator":"OR","count":1000,"pop_begin":0,"pop_end":100})
            r.raise_for_status(); data = r.json()
        except Exception as exc:
            status.warnings.append(f"ListenBrainz classical tag {tag}: {exc}"); continue
        payload = data.get("payload", data) if isinstance(data, dict) else data
        tracks = payload.get("jspf", {}).get("playlist", {}).get("track", []) if isinstance(payload, dict) else []
        if not tracks and isinstance(payload, list): tracks = payload
        for pos, x in enumerate(tracks, 1):
            title = x.get("title") or x.get("track_name") or x.get("recording_name")
            artist = x.get("creator") or x.get("artist_name") or x.get("artist_credit_name")
            if not title or not artist: continue
            mbid = x.get("identifier") or x.get("recording_mbid")
            if isinstance(mbid, list): mbid = mbid[0] if mbid else None
            if isinstance(mbid, str) and "/" in mbid: mbid = mbid.rsplit("/",1)[-1]
            aliases=[_identity(title, artist)]
            if mbid: aliases.append(f"mbid:{mbid}")
            if any(alias in used for alias in aliases): continue
            used.update(aliases)
            out.append(SongRow(title=title, main_artist=artist, genres=[tag], metric_name="listenbrainz_tag_radio_rank",
                metric_value=float(max(1,1001-pos)), metric_unit="rank_score", musicbrainz_recording_mbid=mbid,
                source_url="https://listenbrainz.org/", retrieved_at=TODAY,
                source_notes="ListenBrainz tag-radio popularity rank; not a Spotify stream count.", extra={"tag":tag}))
            if len(out)>=limit: break
    client.close(); return out


def build_emerging(catalog: pd.DataFrame, status: BuildStatus) -> list[SongRow]:
    wall_year = date.today().year
    years = pd.to_numeric(catalog["release_year"], errors="coerce")
    observed = years[(years >= 1950) & (years <= wall_year)]
    # Anchor to the freshest year actually present in the acquired catalog. This prevents an
    # older snapshot from silently producing an empty "emerging" list while still preserving
    # a reproducible definition of recent/promising within the source snapshot.
    current_year = int(observed.max()) if len(observed) else wall_year
    pop = pd.to_numeric(catalog["popularity"], errors="coerce").fillna(0)
    streams = pd.to_numeric(catalog["streams"], errors="coerce").fillna(0)
    followers = pd.to_numeric(catalog["artist_followers"], errors="coerce").fillna(0)
    base = catalog[(years >= current_year-3) & ((pop >= 35) | (streams >= 100_000))].copy()
    if len(base) < 10_000:
        base = catalog[(years >= current_year-4) & ((pop >= 25) | (streams >= 50_000))].copy()
    bp = pd.to_numeric(base["popularity"], errors="coerce").fillna(0)
    bs = pd.to_numeric(base["streams"], errors="coerce").fillna(0)
    bf = pd.to_numeric(base["artist_followers"], errors="coerce").fillna(0)
    by = pd.to_numeric(base["release_year"], errors="coerce").fillna(current_year-4)
    base["_emerging_score"] = (
        bp * .45 + (bs + 1).map(math.log10) * 7.0 + (by-(current_year-5)).clip(lower=0) * 4.0
        - (bf + 1).map(math.log10) * 2.2
    )
    # Exclude obvious superstar-scale profiles when follower metadata is available.
    eligible = base[(bf == 0) | (bf <= 5_000_000)].sort_values("_emerging_score", ascending=False)
    if len(eligible) < 10_000:
        eligible = base.sort_values("_emerging_score", ascending=False)
    per_artist = defaultdict(int); selected: list[SongRow] = []; used=set()
    for _, r in eligible.iterrows():
        artist_key = norm(str(r.main_artist))
        if per_artist[artist_key] >= 3: continue
        if not _claim_selection(r, used): continue
        per_artist[artist_key]+=1
        selected.append(_catalog_row_to_song(r, extra={
            "emerging_score": float(r._emerging_score),
            "heuristic": "recent_release + track_momentum - established_artist_penalty; max_3_tracks_per_artist",
        }))
        if len(selected)>=10_000: break
    selected = _sort_and_rank(selected)
    write_rows(selected, DATA/"emerging"/"emerging_10000.csv")
    status.datasets["emerging"] = DatasetStatus(target=10_000, rows=len(selected), complete=len(selected)==10_000,
        metric_coverage=dict(_metric_counts(selected)), notes=[f"Emerging/promising is a documented heuristic, not an objective fact; source_anchor_year={current_year}."])
    status.save(); return selected


def _screen_work_from_album(album: str) -> str | None:
    if not album: return None
    s = re.sub(r"\s*\((?:Original.*?|Music from.*?)\)\s*", "", album, flags=re.I).strip()
    s = re.sub(r"\s*[-:]\s*(?:Original.*(?:Soundtrack|Score)|Soundtrack|Music from.*)$", "", s, flags=re.I).strip()
    return s or album


def build_screen_soundtracks(catalog: pd.DataFrame, status: BuildStatus) -> list[SongRow]:
    # Start with a small curated set of high-profile needle-drop/theme associations that
    # soundtrack-album metadata alone can miss (for example Transformers and Money Heist).
    # These seeds only establish the screen-work association; popularity still comes from the
    # catalog metric for the matched recording.
    title_map, exact = _catalog_match_index(catalog)
    selected: list[SongRow] = []
    used=set()
    seed_path=DATA/'seeds'/'screen_association_seed.csv'
    if seed_path.exists():
        with seed_path.open(encoding='utf-8-sig',newline='') as f:
            for seed in csv.DictReader(f):
                title=(seed.get('title') or '').strip(); artist=(seed.get('main_artist') or '').strip()
                if not title or not artist: continue
                matched,method=_match_song(title,[artist],catalog,title_map,exact)
                if matched is None: continue
                if not _claim_selection(matched, used): continue
                row=_catalog_row_to_song(matched,extra={
                    'association_method':'curated_verified_seed',
                    'association_source_url':seed.get('association_source_url'),
                    'association_note':seed.get('association_note'),
                    'catalog_match':method,
                })
                row.screen_work=(seed.get('screen_work') or '').strip() or None
                # Keep metric provenance as the song source while retaining the independent
                # association source in extra/source_notes.
                row.source_notes=((row.source_notes + ' ') if row.source_notes else '') + \
                    f"Screen association verified separately: {seed.get('association_source_url') or ''}"
                selected.append(row)
    album = catalog["album_name"].fillna("").astype(str)
    genre = catalog["genres"].fillna("").astype(str)
    mask = album.str.contains(SOUNDTRACK_RE, na=False) | genre.str.contains(r"soundtrack|film score|movie", case=False, regex=True, na=False)
    cand = _rank_frame(catalog[mask].copy())
    for _, r in cand.iterrows():
        if not _claim_selection(r, used): continue
        row=_catalog_row_to_song(r, extra={"association_method":"album_or_genre_soundtrack_metadata"})
        row.screen_work=_screen_work_from_album(str(r.album_name or ""))
        selected.append(row)
        if len(selected)>=10_000: break
    selected=_sort_and_rank(dedupe(selected))[:10_000]
    write_rows(selected, DATA/"screen_soundtracks"/"screen_soundtracks_10000.csv")
    status.datasets["screen_soundtracks"] = DatasetStatus(target=10_000, rows=len(selected), complete=len(selected)==10_000,
        metric_coverage=dict(_metric_counts(selected)), notes=["Source-declared soundtrack/score metadata plus a narrowly curated, independently sourced association seed for famous needle-drop/theme cases. No fan-only association is accepted without a cited source."])
    status.save(); return selected


ANILIST_QUERY = r'''query ($page:Int!, $perPage:Int!) {
  Page(page:$page, perPage:$perPage) {
    pageInfo { hasNextPage }
    media(type:ANIME, sort:POPULARITY_DESC, isAdult:false) {
      id idMal popularity averageScore format seasonYear
      title { romaji english native }
    }
  }
}'''


def _http_json(client: httpx.Client, method: str, url: str, **kwargs: Any) -> Any:
    last=None
    for attempt in range(8):
        try:
            r=client.request(method,url,**kwargs)
            if r.status_code == 429:
                retry_after=r.headers.get("Retry-After")
                try: wait=float(retry_after) if retry_after is not None else float(min(60,2**attempt))
                except ValueError: wait=float(min(60,2**attempt))
                last=RuntimeError(f"429 Too Many Requests for {url}; retry after {wait:g}s")
                time.sleep(max(1.0,min(wait,120.0)))
                continue
            r.raise_for_status(); return r.json()
        except httpx.HTTPStatusError as exc:
            last=exc
            # Authentication/permission/not-found errors are not transient. Retrying them only
            # wastes build time and can aggravate source-side rate controls.
            if 400 <= exc.response.status_code < 500:
                break
            time.sleep(min(60,2**attempt))
        except Exception as exc:
            last=exc; time.sleep(min(60,2**attempt))
    raise RuntimeError(f"{method} {url} failed: {last}")


def fetch_anilist_top(limit:int=10_000) -> list[dict[str,Any]]:
    out=[]; page=1
    with httpx.Client(timeout=60,headers={"User-Agent":"BeatHit-Dataset/1.0"}) as c:
        while len(out)<limit:
            d=_http_json(c,"POST",ANILIST_API,json={"query":ANILIST_QUERY,"variables":{"page":page,"perPage":50}})
            block=d["data"]["Page"]; out.extend(block["media"])
            if not block["pageInfo"]["hasNextPage"]: break
            page+=1
            # AniList currently operates under a reduced anonymous rate limit. Stay below it
            # instead of relying on repeated 429 retries from shared GitHub-hosted runner IPs.
            time.sleep(2.1)
    return out[:limit]


def fetch_animethemes_all(status: BuildStatus) -> tuple[dict[int,list[dict]],dict[int,list[dict]]]:
    """Return theme candidates keyed by AniList ID and MAL ID.

    API response parsing is tolerant of both the legacy top-level `anime` array and the
    current paginated shape. If the API changes, the MAL theme fallback still provides
    a source-backed theme string for many older titles.
    """
    by_anilist=defaultdict(list); by_mal=defaultdict(list); page=1
    with httpx.Client(timeout=90,follow_redirects=True,headers={"User-Agent":"BeatHit-Dataset/1.0"}) as c:
        while True:
            params={"include":"animethemes.song.artists,resources","page[size]":"100","page[number]":str(page)}
            try: d=_http_json(c,"GET",ANIMETHEMES_API,params=params)
            except Exception as exc:
                status.warnings.append(f"AnimeThemes page {page}: {exc}"); break
            items=d.get("anime") or d.get("data") or []
            if not items: break
            for a in items:
                candidates=[]
                for th in a.get("animethemes",[]) or a.get("anime_themes",[]):
                    song=th.get("song") or {}
                    artists=[]
                    for ar in song.get("artists",[]) or []:
                        artists.append(ar.get("name") or ar.get("slug") or "")
                    candidates.append({
                        "title":song.get("title") or song.get("name"),
                        "artists":[x for x in artists if x],
                        "type":th.get("type"),"sequence":th.get("sequence"),
                        "animethemes_url":f"https://animethemes.moe/anime/{a.get('slug','')}",
                    })
                for res in a.get("resources",[]) or []:
                    site=(res.get("site") or "").casefold(); ext=_safe_int(res.get("external_id") or res.get("externalId"))
                    if not ext: continue
                    if "anilist" in site: by_anilist[ext].extend(candidates)
                    if "myanimelist" in site or site=="mal": by_mal[ext].extend(candidates)
            links=d.get("links") or {}; meta=d.get("meta") or {}
            if links.get("next"):
                page+=1
            elif meta.get("current_page") and meta.get("last_page") and meta["current_page"]<meta["last_page"]:
                page+=1
            elif len(items)>=100:
                page+=1
            else: break
            if page>1000: break
            time.sleep(.15)
    return dict(by_anilist),dict(by_mal)


def _parse_theme_string(raw: str) -> tuple[str,str] | None:
    # MAL exports often use: '1: "Song" by Artist (eps...)'.
    if not raw or raw in {"[]","nan"}: return None
    m=re.search(r'["“](.+?)["”]\s+by\s+(.+?)(?:\s*\(|$)',raw)
    if m: return m.group(1).strip(),m.group(2).strip()
    m=re.search(r"(?:^|\d+:\s*)(.+?)\s+by\s+(.+?)(?:\s*\(|$)",raw)
    if m: return m.group(1).strip(" '\""),m.group(2).strip(" '\"")
    return None


def _load_mal_theme_fallback(path: Path | None) -> dict[int,list[dict]]:
    out=defaultdict(list)
    if not path or not path.exists(): return {}
    try:
        for chunk in pd.read_csv(path,chunksize=20_000,low_memory=False,encoding_errors="replace"):
            idcol=next((c for c in chunk.columns if c.casefold() in {"anime_id","mal_id"}),None)
            opcol=next((c for c in chunk.columns if "opening" in c.casefold()),None)
            edcol=next((c for c in chunk.columns if "ending" in c.casefold()),None)
            if not idcol: continue
            for _,r in chunk.iterrows():
                mid=_safe_int(r.get(idcol));
                if not mid: continue
                for typ,col in (("OP",opcol),("ED",edcol)):
                    if not col: continue
                    parsed=_parse_theme_string(str(r.get(col,"")))
                    if parsed: out[mid].append({"title":parsed[0],"artists":[parsed[1]],"type":typ,"sequence":1,"animethemes_url":MAL_THEME_FALLBACK_SOURCE})
    except Exception: pass
    return dict(out)


def _catalog_match_index(catalog: pd.DataFrame) -> tuple[dict[str,list[int]],dict[tuple[str,str],list[int]]]:
    title_map=defaultdict(list); exact=defaultdict(list)
    for i,r in catalog.iterrows():
        nt=norm(str(r.title)); na=norm(str(r.main_artist))
        if nt:
            title_map[nt].append(i); exact[(nt,na)].append(i)
    return dict(title_map),dict(exact)


def _match_song(title:str, artists:list[str], catalog:pd.DataFrame, title_map:dict[str,list[int]], exact:dict[tuple[str,str],list[int]]) -> tuple[pd.Series|None,str]:
    nt=norm(title); artist=artists[0] if artists else ""; na=norm(artist)
    ids=exact.get((nt,na),[])
    if ids:
        cand=_rank_frame(catalog.loc[ids]); return cand.iloc[0],"exact_title_artist"
    ids=title_map.get(nt,[])
    if not ids: return None,"unmatched"
    best=None;bestscore=-1
    for i in ids[:100]:
        r=catalog.loc[i]; s=fuzz.ratio(na,norm(str(r.main_artist))) if na else 0
        if s>bestscore: bestscore=s;best=r
    if best is not None and (bestscore>=65 or len(ids)==1): return best,f"exact_title_artist_fuzzy_{bestscore}"
    return None,"unmatched"



def _jikan_theme_candidates(mal_id: int, client: httpx.Client, cache: dict[str, list[dict]], status: BuildStatus) -> list[dict]:
    """Fallback theme metadata from Jikan/MyAnimeList for AnimeThemes gaps.

    This is intentionally lazy and cached: only popular AniList candidates that have no
    AnimeThemes/fallback theme are queried, and the build stops once 10,000 verified-theme
    anime have been collected. Jikan is read-only and rate-limited, so requests are paced.
    """
    key=str(mal_id)
    if key in cache:
        return cache[key]
    try:
        d=_http_json(client,"GET",f"{JIKAN_API}/anime/{mal_id}/themes")
        data=d.get("data") or {}
        out=[]
        for typ,field in (("OP","openings"),("ED","endings")):
            for seq,raw in enumerate(data.get(field,[]) or [],1):
                parsed=_parse_theme_string(str(raw))
                if parsed:
                    out.append({"title":parsed[0],"artists":[parsed[1]],"type":typ,"sequence":seq,
                                "animethemes_url":f"https://myanimelist.net/anime/{mal_id}"})
        cache[key]=out
        # Public Jikan guidance asks clients to use the API responsibly; keep below common
        # anonymous burst limits during this large one-time backfill.
        time.sleep(.36)
        return out
    except Exception as exc:
        status.warnings.append(f"Jikan themes MAL {mal_id}: {exc}")
        cache[key]=[]
        time.sleep(.5)
        return []

def build_anime(catalog: pd.DataFrame, sources:dict[str,Path], status:BuildStatus) -> list[SongRow]:
    anime=fetch_anilist_top(20_000)
    by_al,by_mal=fetch_animethemes_all(status)
    fallback=_load_mal_theme_fallback(sources.get("mal_theme_fallback"))
    title_map,exact=_catalog_match_index(catalog)
    cache_path=CACHE/"jikan_themes.json"
    try:
        jikan_cache=json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        if not isinstance(jikan_cache,dict): jikan_cache={}
    except Exception:
        jikan_cache={}
    jikan_client=httpx.Client(timeout=60,follow_redirects=True,headers={"User-Agent":"BeatHit-Dataset/1.0"})
    rows=[]; jikan_queries=0
    try:
        for anime_rank,a in enumerate(anime,1):
            if len(rows) >= 10_000:
                break
            candidates=list(by_al.get(a.get("id"),[])) or list(by_mal.get(a.get("idMal"),[])) or list(fallback.get(a.get("idMal"),[]))
            # Accuracy/coverage fallback for popular titles absent from AnimeThemes. Jikan exposes
            # MyAnimeList opening/ending strings; no invented song is created when it has none.
            mid=_safe_int(a.get("idMal"))
            if not candidates and mid:
                before=len(jikan_cache)
                candidates=_jikan_theme_candidates(mid,jikan_client,jikan_cache,status)
                if len(jikan_cache)>before: jikan_queries+=1
                if jikan_queries and jikan_queries % 250 == 0:
                    cache_path.parent.mkdir(parents=True,exist_ok=True)
                    cache_path.write_text(json.dumps(jikan_cache,ensure_ascii=False),encoding="utf-8")
            # Deduplicate theme candidates while preserving OP/ED sequence.
            seen=set(); uniq=[]
            for c in candidates:
                if not c.get("title"): continue
                k=(norm(c["title"]),norm((c.get("artists") or [""])[0]))
                if k in seen: continue
                seen.add(k); uniq.append(c)
            scored=[]
            for c in uniq:
                match,method=_match_song(c["title"],c.get("artists") or [],catalog,title_map,exact)
                if match is not None:
                    scored.append((_score(match), 1 if c.get("type")=="OP" else 0, -(c.get("sequence") or 1), c, match, method))
                else:
                    # If no song popularity observation exists, OP1 is the recognizability fallback.
                    scored.append((-1,1 if c.get("type")=="OP" else 0,-(c.get("sequence") or 1),c,None,"theme_order_fallback"))
            if not scored:
                continue
            scored.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True)
            _,_,_,c,match,method=scored[0]
            anime_title=(a.get("title",{}).get("english") or a.get("title",{}).get("romaji") or a.get("title",{}).get("native") or str(a.get("id")))
            if match is not None:
                row=_catalog_row_to_song(match,rank=len(rows)+1,extra={"theme_type":c.get("type"),"theme_sequence":c.get("sequence"),"selection_method":method,"anilist_id":a.get("id"),"mal_id":a.get("idMal"),"anilist_popularity_rank":anime_rank})
            else:
                artists=c.get("artists") or ["Unknown verified theme artist"]
                row=SongRow(rank=len(rows)+1,title=c["title"],main_artist=artists[0],featured_artists=artists[1:],
                    anime_title=anime_title,anime_popularity=a.get("popularity"),metric_name="anime_popularity_proxy",
                    metric_value=float(a.get("popularity") or 0),metric_unit="anilist_users",source_url=c.get("animethemes_url") or "https://animethemes.moe/",
                    retrieved_at=TODAY,source_notes="No trustworthy song-level listen/view count found; AniList anime popularity is explicitly a proxy, not song listens.",
                    extra={"theme_type":c.get("type"),"theme_sequence":c.get("sequence"),"selection_method":method,"anilist_id":a.get("id"),"mal_id":a.get("idMal"),"anilist_popularity_rank":anime_rank})
            row.anime_title=anime_title; row.anime_popularity=a.get("popularity")
            rows.append(row)
    finally:
        jikan_client.close()
        cache_path.parent.mkdir(parents=True,exist_ok=True)
        cache_path.write_text(json.dumps(jikan_cache,ensure_ascii=False),encoding="utf-8")
    rows.sort(key=lambda x:(x.extra or {}).get("anilist_popularity_rank", 10**9))
    for i,r in enumerate(rows,1): r.rank=i
    write_rows(rows,DATA/"anime"/"anime_songs.csv")
    status.sources["jikan_theme_fallback"]={"url":"https://api.jikan.moe/v4","queried":jikan_queries,"cached":len(jikan_cache),"ok":True}
    status.datasets["anime"] = DatasetStatus(target=10_000,rows=len(rows),complete=len(rows)==10_000,
        metric_coverage=dict(_metric_counts(rows)),notes=["Anime ordering uses AniList popularity over up to 20,000 candidates. Theme metadata priority: AnimeThemes external-ID match, bundled MAL fallback, then paced/cached Jikan MyAnimeList themes. Theme choice uses strongest matched song metric; otherwise OP1/ED1 fallback."])
    status.save();return rows


def _vocadb_all(status:BuildStatus,max_songs:int|None=None)->tuple[list[dict],bool]:
    """Fetch a high-recall VocaDB candidate window without abusive full-site crawling.

    VocaDB explicitly warns against excessive thousands of API calls per day. A complete
    enumeration of its hundreds of thousands of songs would require many thousands of
    requests, so the default build scans the top 25k songs by RatingScore (popular songs are
    the plausible >=10M Spotify candidates). Operators can raise the limit deliberately with
    BEATHIT_VOCADB_SCAN_LIMIT, but the result is only marked exhaustive when the API-reported
    total was actually reached.
    """
    if max_songs is None:
        max_songs=max(1_000,min(_safe_int(os.getenv("BEATHIT_VOCADB_SCAN_LIMIT")) or 25_000,250_000))
    out=[];start=0;size=50; exhaustive=False; total_seen=None
    with httpx.Client(timeout=90,headers={"User-Agent":"BeatHit-Dataset/1.0 (contact via GitHub repository)"}) as c:
        while start<max_songs:
            params={"start":start,"maxResults":size,"sort":"RatingScore","fields":"Artists,Names"}
            if start==0: params["getTotalCount"]="true"
            try:d=_http_json(c,"GET",VOCADB_API,params=params)
            except Exception as exc:
                status.warnings.append(f"VocaDB start={start}: {exc}");break
            items=d.get("items",[]) or []
            if total_seen is None: total_seen=_safe_int(d.get("totalCount"))
            if not items:
                exhaustive=True; break
            out.extend(items);start+=len(items)
            if total_seen is not None and start>=total_seen:
                exhaustive=True; break
            if len(items)<size:
                exhaustive=True; break
            time.sleep(.25)
    status.sources["vocadb_candidate_scan"]={
        "url":"https://vocadb.net/api/songs", "rows":len(out), "requested_limit":max_songs,
        "api_reported_total":total_seen, "exhaustive":exhaustive, "sort":"RatingScore", "ok":bool(out),
        "note":"Bounded high-popularity scan to respect VocaDB API usage guidance; qualifying Spotify threshold remains strict."
    }
    return out,exhaustive


def _vocadb_names(item:dict)->list[str]:
    names=[item.get("name"),item.get("defaultName"),item.get("additionalNames")]
    for n in item.get("names",[]) or []: names.append(n.get("value"))
    out=[]
    for x in names:
        if not x:continue
        if isinstance(x,str) and ", " in x and x==item.get("additionalNames"):
            out.extend(x.split(", "))
        else:out.append(str(x))
    return list(dict.fromkeys(x.strip() for x in out if x and x.strip()))


def build_vocaloid(catalog:pd.DataFrame,status:BuildStatus)->list[SongRow]:
    from .io import read_rows
    voca,vocadb_exhaustive=_vocadb_all(status)
    title_map,_=_catalog_match_index(catalog)
    # Preserve manually verified qualifying seeds as a resilience floor, then merge the
    # exhaustive VocaDB/catalog classification results on top.
    seed_path=DATA/"seeds"/"vocaloid_verified_seed.csv"
    out=[]
    if seed_path.exists():
        try:
            seeds=[r for r in read_rows(seed_path) if r.listen_count is not None and r.listen_count>=10_000_000]
            # Refresh manually verified seeds from the acquired catalog whenever the same Spotify ID
            # or exact normalized title+artist has a newer/higher cumulative stream observation.
            by_tid={}
            for _,cr in catalog.iterrows():
                tid=_clean_track_id(cr.get("track_id"))
                if tid: by_tid[tid]=cr
            for seed in seeds:
                match=by_tid.get(seed.spotify_track_id or "")
                if match is None:
                    ids=title_map.get(norm(seed.title),[])
                    for idx in ids:
                        cr=catalog.loc[idx]
                        if norm(str(cr.main_artist))==norm(seed.main_artist):
                            match=cr; break
                if match is not None:
                    streams=_safe_float(match.get("trusted_cumulative_streams"))
                    trusted_url=str(match.get("trusted_streams_source_url") or "")
                    if streams is not None and streams>=10_000_000 and streams>float(seed.metric_value):
                        refreshed=_catalog_row_to_song(match,metric_override=("spotify_streams_snapshot" if "zenodo" in trusted_url else "spotify_streams",streams,"streams"),
                            extra={**(seed.extra or {}),"verified_seed":True,"trusted_stream_snapshot_date":str(match.get("trusted_streams_snapshot_date") or "")},
                            source_notes="Manually verified voice-synth seed; stream count refreshed from stricter cumulative Spotify evidence.")
                        if trusted_url: refreshed.source_url=trusted_url
                        # Preserve the verified canonical display credit/title from the seed.
                        refreshed.title=seed.title; refreshed.main_artist=seed.main_artist; refreshed.featured_artists=seed.featured_artists
                        seed=refreshed
                out.append(seed)
        except Exception as exc:
            status.warnings.append(f"vocaloid seed read: {exc}")
    seen={_identity(r.title,r.main_artist,r.spotify_track_id) for r in out}
    # Direct high-precision classification from explicit voice-synth credits/genres. This catches
    # qualifying tracks that may sit outside the bounded VocaDB popularity scan without requiring
    # an abusive full-database crawl.
    for _,cr in catalog.iterrows():
        streams=_safe_float(cr.get("trusted_cumulative_streams"))
        if streams is None or streams < 10_000_000:
            continue
        trusted_url=str(cr.get("trusted_streams_source_url") or "")
        stream_metric="spotify_streams_snapshot" if "zenodo" in trusted_url else "spotify_streams"
        credit_text=f"{cr.get('title','')} | {cr.get('artists','')} | {cr.get('genres','')}".casefold()
        explicit_marker=any(marker.casefold() in credit_text for marker in VOICE_SYNTH_MARKERS)
        if not explicit_marker:
            continue
        k=_identity(str(cr.title),str(cr.main_artist),_clean_track_id(cr.get("track_id")))
        if k in seen: continue
        seen.add(k)
        row=_catalog_row_to_song(cr,metric_override=(stream_metric,float(streams),"streams"),
            extra={"classification":"explicit_voice_synth_credit_or_genre","trusted_stream_snapshot_date":str(cr.get("trusted_streams_snapshot_date") or "")},
            source_notes="Voice-synth classification from explicit track credit/genre marker; >=10M qualification uses the stricter cumulative Spotify evidence channel (Zenodo Spotify-API snapshot or live Kworb overlay).")
        if trusted_url: row.source_url=trusted_url
        out.append(row)
    for item in voca:
        names=_vocadb_names(item)
        cand_ids=[]
        for name in names:
            cand_ids.extend(title_map.get(norm(name),[]))
        cand_ids=list(dict.fromkeys(cand_ids))
        if not cand_ids:continue
        voca_artists=[]
        for ar in item.get("artists",[]) or []:
            name=(ar.get("artist") or {}).get("name") or ar.get("name")
            if name:voca_artists.append(name)
        artist_norms=[norm(x) for x in voca_artists]
        best=None;best_score=-1
        for idx in cand_ids[:200]:
            r=catalog.loc[idx];streams=_safe_float(r.get("trusted_cumulative_streams"))
            # The hard threshold uses only the stricter cumulative channel (Zenodo Spotify-API
            # snapshot or live Kworb overlay). Chart-window totals and noisy cross-platform
            # snapshot outliers remain ranking evidence but cannot qualify a track by themselves.
            if streams is None or streams<10_000_000:
                continue
            trusted_url=str(r.get("trusted_streams_source_url") or "")
            stream_metric="spotify_streams_snapshot" if "zenodo" in trusted_url else "spotify_streams"
            ar=norm(str(r.main_artist))
            similarity=max([fuzz.ratio(ar,x) for x in artist_norms],default=0)
            # Accuracy-first entity resolution: a title collision alone is not enough. VocaDB
            # normally exposes producer/artist credits, so require reasonable credit similarity
            # whenever those credits exist. This deliberately accepts false negatives rather than
            # attaching a mainstream same-title Spotify hit to a voice-synth song.
            if artist_norms and similarity < 45:
                continue
            score=streams + similarity*1_000_000
            if score>best_score:best_score=score;best=r
        if best is None:continue
        k=_identity(str(best.title),str(best.main_artist),_clean_track_id(best.track_id))
        if k in seen:continue
        seen.add(k)
        trusted_value=float(best.get("trusted_cumulative_streams"))
        trusted_url=str(best.get("trusted_streams_source_url") or "")
        trusted_metric="spotify_streams_snapshot" if "zenodo" in trusted_url else "spotify_streams"
        row=_catalog_row_to_song(best,metric_override=(trusted_metric,trusted_value,"streams"),
            extra={"vocadb_id":item.get("id"),"vocadb_url":f"https://vocadb.net/S/{item.get('id')}","vocadb_artists":voca_artists,"match":"exact_normalized_title","trusted_stream_snapshot_date":str(best.get("trusted_streams_snapshot_date") or "")},
            source_notes="Qualified only when stricter cumulative Spotify evidence reports >=10,000,000 streams and the title/artist resolves to a VocaDB voice-synth song entry.")
        if trusted_url: row.source_url=trusted_url
        out.append(row)
    # Every qualifying row uses the same platform/unit, so exact cumulative Spotify
    # streams are the ranking key. Do not let a secondary composite score reorder the list.
    out=dedupe(out)
    out.sort(key=lambda r: (r.listen_count if r.listen_count is not None else int(r.metric_value)), reverse=True)
    for i,r in enumerate(out[:10_000],1): r.rank=i
    out=out[:10_000]
    write_rows(out,DATA/"vocaloid"/"vocaloid_spotify_10m.csv")
    status.datasets["vocaloid"] = DatasetStatus(target="all verified >=10,000,000 Spotify streams; cap 10,000",rows=len(out),complete=(vocadb_exhaustive and bool(status.sources.get("spotify_zenodo_0_9m",{}).get("ok"))),
        metric_coverage=dict(_metric_counts(out)),notes=["Conditional list: never padded. Completeness requires exhaustive VocaDB enumeration and remains bounded by acquired Spotify stream-snapshot coverage.", f"vocadb_exhaustive={vocadb_exhaustive}"])
    status.save();return out


def _holodex_headers()->dict[str,str]:
    h={"User-Agent":"BeatHit-Dataset/1.0"};key=os.getenv("HOLODEX_API_KEY","").strip()
    if key:h["X-APIKEY"]=key
    return h


def _fetch_holodex_topic(topic:str,status:BuildStatus,max_items:int=50_000)->list[dict]:
    out=[];offset=0;limit=50
    with httpx.Client(timeout=90,follow_redirects=True,headers=_holodex_headers()) as c:
        while len(out)<max_items:
            params={"topic":topic,"status":"past","limit":limit,"offset":offset,"paginated":"1","include":"channel_stats,mentions"}
            try:d=_http_json(c,"GET",f"{HOLODEX_API}/videos",params=params)
            except Exception as exc:
                status.warnings.append(f"Holodex {topic}: {exc}");break
            items=d.get("items",[]) if isinstance(d,dict) else d
            if not items:break
            out.extend(items);offset+=len(items)
            total=_safe_int(d.get("total")) if isinstance(d,dict) else None
            if total is not None and offset>=total:break
            if len(items)<limit:break
            time.sleep(.15)
    return out[:max_items]


def _youtube_views(ids:list[str],status:BuildStatus)->dict[str,int]:
    """Return best available video-level view counts.

    Priority: official YouTube Data API -> Return YouTube Dislike cached YouTube viewCount ->
    optional yt-dlp fallback for unresolved IDs. The fallback source is recorded so its provenance
    is not confused with an official API response.
    """
    ids=list(dict.fromkeys(x for x in ids if x))
    key=os.getenv("YOUTUBE_API_KEY","").strip();out={}
    if key:
        with httpx.Client(timeout=60) as c:
            for i in range(0,len(ids),50):
                try:
                    d=_http_json(c,"GET",YOUTUBE_API,params={"part":"statistics","id":",".join(ids[i:i+50]),"key":key})
                    for x in d.get("items",[]):
                        v=_safe_int((x.get("statistics") or {}).get("viewCount"))
                        if v is not None:out[x["id"]]=v
                except Exception as exc:status.warnings.append(f"YouTube API batch {i}: {exc}")
        status.sources["youtube_view_counts"]={"source":"YouTube Data API v3","requested":len(ids),"resolved":len(out),"ok":bool(out)}
        return out

    # High-throughput no-key fallback. RYD returns a cached viewCount field obtained for the
    # referenced YouTube video. It is third-party provenance and may lag the live counter.
    if os.getenv("BEATHIT_RYD_FALLBACK","1")=="1" and ids:
        workers=max(1,min(_safe_int(os.getenv("BEATHIT_RYD_WORKERS")) or 20,32))
        timeout=max(5,min(_safe_int(os.getenv("BEATHIT_RYD_TIMEOUT")) or 15,60))
        def one(vid:str)->tuple[str,int|None]:
            try:
                with httpx.Client(timeout=timeout,follow_redirects=True,headers={"User-Agent":"BeatHit-Dataset/1.0"}) as c:
                    r=c.get(RYD_API,params={"videoId":vid})
                    if r.status_code==404:return vid,None
                    r.raise_for_status();v=_safe_int(r.json().get("viewCount"));return vid,v
            except Exception:return vid,None
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs=[ex.submit(one,x) for x in ids]
                for f in as_completed(futs):
                    vid,v=f.result()
                    if v is not None:out[vid]=v
            status.sources["youtube_view_counts"]={"source":"Return YouTube Dislike API viewCount fallback","url":RYD_API,
                "requested":len(ids),"resolved":len(out),"ok":bool(out),"note":"third-party cached viewCount; official YouTube API preferred when key supplied"}
        except Exception as exc:
            status.warnings.append(f"Return YouTube Dislike view fallback: {exc}")

    missing=[x for x in ids if x not in out]
    # yt-dlp is a last resort and deliberately capped unless explicitly overridden, preventing a
    # tens-of-thousands-video fallback from consuming the entire CI runtime.
    if missing and os.getenv("BEATHIT_YTDLP_FALLBACK","0")=="1" and shutil.which("yt-dlp"):
        cap=max(0,_safe_int(os.getenv("BEATHIT_YTDLP_MAX")) or 2000)
        missing=missing[:cap]
        CACHE.mkdir(parents=True,exist_ok=True);f=CACHE/"youtube_ids.txt";f.write_text("\n".join(f"https://www.youtube.com/watch?v={x}" for x in missing),encoding="utf-8")
        cmd=["yt-dlp","--ignore-errors","--skip-download","--no-warnings","--print","%(id)s\t%(view_count)s","-a",str(f)]
        try:
            p=subprocess.run(cmd,capture_output=True,text=True,timeout=2*60*60,check=False)
            for line in p.stdout.splitlines():
                parts=line.rsplit("\t",1)
                if len(parts)==2 and parts[1].isdigit():out[parts[0]]=int(parts[1])
            if p.returncode and not out: status.warnings.append("yt-dlp YouTube view fallback failed; set YOUTUBE_API_KEY for exact VTuber ranking.")
        except Exception as exc:status.warnings.append(f"yt-dlp YouTube view fallback: {exc}")
    elif missing and not out:
        status.warnings.append("No official YouTube API key and no view-count fallback resolved; VTuber rows use Spotify counts/proxies where available.")
    return out


def _clean_vtuber_title(title:str)->str:
    s=re.sub(r"[【\[].*?(?:cover|original|歌ってみた|オリジナル).*?[】\]]","",title,flags=re.I)
    s=re.sub(r"\s*[|｜].*$","",s).strip()
    return s or title.strip()




def _fetch_holostats_rows(*, original: bool, status: BuildStatus) -> list[SongRow]:
    """Best-effort Hololive-only fallback/augmentation with published YouTube view counts.

    HoloStats is not a substitute for the cross-agency Holodex corpus; it is used only when
    its server-rendered table is available and every row is labeled with its own provenance.
    """
    typ="original" if original else "cover"
    url=f"https://www.holostats.com/songs?type={typ}&sort=total_views"
    try:
        tables=pd.read_html(url)
        if not tables: return []
        df=max(tables,key=len)
        cols={str(c).strip().casefold():c for c in df.columns}
        song_col=next((c for k,c in cols.items() if k=="song" or "song" in k),None)
        artist_col=next((c for k,c in cols.items() if "artist" in k),None)
        views_col=next((c for k,c in cols.items() if "view" in k and "gain" not in k),None)
        pub_col=next((c for k,c in cols.items() if "published" in k or "date" in k),None)
        if song_col is None or artist_col is None or views_col is None: return []
        rows=[]
        for _,r in df.iterrows():
            title=_clean_vtuber_title(str(r.get(song_col) or "").strip())
            artist=str(r.get(artist_col) or "").strip()
            views=_safe_int(r.get(views_col))
            if not title or not artist or views is None: continue
            rd=str(r.get(pub_col) or "").strip() if pub_col else ""
            rows.append(SongRow(title=title,main_artist=artist,vtuber=artist,is_original=original,
                release_date=(rd or None),release_year=_parse_year(rd),metric_name="youtube_views",metric_value=float(views),
                metric_unit="views",view_count=views,source_url=url,retrieved_at=TODAY,
                source_notes="HoloStats published YouTube view-count ranking; Hololive-only fallback/augmentation.",
                extra={"source":"HoloStats","type":typ}))
        status.sources[f"holostats_{typ}"]={"url":url,"rows":len(rows),"ok":bool(rows)}
        return rows
    except Exception as exc:
        status.warnings.append(f"HoloStats {typ}: {exc}")
        status.sources[f"holostats_{typ}"]={"url":url,"ok":False,"error":str(exc)}
        return []

def build_vtuber(catalog:pd.DataFrame,status:BuildStatus,*,original:bool)->list[SongRow]:
    topic="Original_Song" if original else "Music_Cover"
    videos=_fetch_holodex_topic(topic,status,max_items=60_000)
    if not videos:
        rows=_fetch_holostats_rows(original=original,status=status)
        rows=dedupe(rows)
        rows.sort(key=lambda r:r.metric_value,reverse=True)
        for i,r in enumerate(rows[:10_000],1):r.rank=i
        rows=rows[:10_000]
    else:
        ids=[x.get("id") for x in videos if x.get("id")]
        views=_youtube_views(ids,status)
        title_map,exact=_catalog_match_index(catalog)
        rows=[]
        for pos,v in enumerate(videos,1):
            ch={}
            if isinstance(v.get("channel_stats"),dict): ch.update(v.get("channel_stats") or {})
            if isinstance(v.get("channel"),dict): ch.update(v.get("channel") or {})
            mentions=v.get("mentions") or []
            # Prefer the single identified VTuber mention when available; uploader channels can be
            # labels/group channels rather than the performer. Otherwise preserve uploader identity.
            mention_names=[]
            for m in mentions:
                if not isinstance(m,dict): continue
                nm=m.get("english_name") or m.get("name")
                if nm: mention_names.append(str(nm))
            artist=(mention_names[0] if len(mention_names)==1 else None) or ch.get("english_name") or ch.get("name") or v.get("channel_name") or v.get("channel_id") or "Unknown VTuber"
            title=_clean_vtuber_title(str(v.get("title") or "Untitled"))
            view=views.get(v.get("id"))
            matched,method=_match_song(title,[artist],catalog,title_map,exact)
            if view is not None:
                metric=("youtube_views",float(view),"views")
            elif matched is not None and _safe_float(matched.get("streams")) is not None:
                metric=("spotify_streams",float(matched.streams),"streams")
            else:
                subs=_safe_int(ch.get("subscriber_count") or ch.get("subscribers") or ch.get("subscriberCount"))
                if subs is not None:
                    metric=("vtuber_channel_subscribers_proxy",float(subs),"subscribers_proxy")
                else:
                    # Holodex source ordering is not itself a popularity count. Keep a transparent
                    # rank score so the row remains sortable without pretending it is views/listens.
                    metric=("holodex_source_rank",float(max(1,len(videos)-pos+1)),"rank_score")
            if matched is not None:
                if metric[0]=="youtube_views":
                    metric_note=("Classified by Holodex; view count from official YouTube Data API."
                                 if os.getenv("YOUTUBE_API_KEY","").strip() else
                                 "Classified by Holodex; view count from Return YouTube Dislike API cached viewCount fallback.")
                elif metric[0]=="spotify_streams":
                    metric_note="Classified by Holodex; Spotify cumulative stream count from matched catalog recording."
                else:
                    metric_note="Classified by Holodex; no song-level view/listen count resolved, so metric is an explicitly labeled proxy."
                row=_catalog_row_to_song(matched,metric_override=metric,source_notes=metric_note,extra={"holodex_video_id":v.get("id"),"holodex_topic":topic,"catalog_match":method,"mentions":mention_names})
                row.title=title or row.title;row.main_artist=artist
                if not row.release_date and v.get("published_at"):
                    row.release_date=str(v.get("published_at"))[:10]; row.release_year=_parse_year(v.get("published_at"))
            else:
                row=SongRow(title=title,main_artist=artist,metric_name=metric[0],metric_value=metric[1],metric_unit=metric[2],
                    listen_count=(int(metric[1]) if metric[2] in {"streams","listens"} else None), listen_source=("Spotify" if metric[0]=="spotify_streams" else None),
                    view_count=(int(metric[1]) if metric[2]=="views" else None),
                    source_url=f"https://www.youtube.com/watch?v={v.get('id')}",retrieved_at=TODAY,
                    source_notes=("Classified by Holodex topic; no song-level view/listen count resolved, so metric is an explicitly labeled proxy." if view is None else
                                  ("Classified by Holodex; view count from official YouTube Data API." if os.getenv("YOUTUBE_API_KEY","").strip() else "Classified by Holodex; view count from Return YouTube Dislike API cached viewCount fallback.")),
                    release_date=(str(v.get("published_at"))[:10] if v.get("published_at") else None), release_year=_parse_year(v.get("published_at")),
                    extra={"holodex_video_id":v.get("id"),"holodex_topic":topic,"mentions":mention_names})
            row.vtuber=artist;row.is_original=original
            rows.append(row)
        # Augment with HoloStats where available; dedupe keeps the stronger row by metric.
        rows.extend(_fetch_holostats_rows(original=original,status=status))
        rows=dedupe(rows)
        rows.sort(key=lambda r:(0 if r.metric_name in {"holodex_source_rank","vtuber_channel_subscribers_proxy"} else 1,r.metric_value),reverse=True)
        for i,r in enumerate(rows[:10_000],1):r.rank=i
        rows=rows[:10_000]
    folder="vtuber_original" if original else "vtuber_non_original"
    filename="vtuber_original_10000.csv" if original else "vtuber_non_original_10000.csv"
    write_rows(rows,DATA/folder/filename)
    key="vtuber_original" if original else "vtuber_non_original"
    status.datasets[key]=DatasetStatus(target=10_000,rows=len(rows),complete=len(rows)==10_000,
        metric_coverage=dict(_metric_counts(rows)),notes=["Holodex topic classification with mentions/channel metadata; exact YouTube views used when available, Spotify streams used on confident catalog matches, subscriber counts only as an explicitly labeled proxy. Finite verified corpus is never padded."])
    status.save();return rows


def _sort_and_rank(rows:list[SongRow])->list[SongRow]:
    """Sort heterogeneous rows by a transparent overall-popularity evidence score.

    Catalog rows already carry the 0-100 composite produced by ``_score``. Specialist rows
    (manual verified seeds, Holodex/HoloStats, ListenBrainz fallbacks) may not, so derive a
    conservative score on the same rough scale from their direct metric instead of either
    pushing them to the bottom or letting one raw unit dominate every other platform.
    """
    direct_priority={
        "spotify_streams":6, "spotify_streams_snapshot":6,
        "youtube_views":5, "listenbrainz_listens":4,
        "spotify_regional_chart_streams_sum":4, "spotify_country_chart_streams":4,
        "spotify_daily_streams":3,
    }
    def fallback_score(r:SongRow)->float:
        value=max(float(r.metric_value),0.0)
        if r.metric_name in {"spotify_streams","spotify_streams_snapshot"}:
            return 30*min(math.log10(value+1)/10,1)
        if r.metric_name=="youtube_views":
            return 15*min(math.log10(value+1)/11,1)
        if r.metric_name in {"spotify_regional_chart_streams_sum", "spotify_country_chart_streams"}:
            return 18*min(math.log10(value+1)/10,1)
        if r.metric_name=="spotify_daily_streams":
            return 10*min(math.log10(value+1)/7,1)
        if r.metric_name=="spotify_popularity":
            return 25*min(value/100,1)
        if r.metric_unit=="listens":
            return 15*min(math.log10(value+1)/9,1)
        if r.metric_unit in {"streams","views"}:
            return 12*min(math.log10(value+1)/10,1)
        # Proxies/ranks remain intentionally weak compared with direct song-level counters.
        return min(math.log10(value+1)*2,12)
    def k(r:SongRow)->tuple[float,float,float]:
        score=float(r.overall_popularity_score) if r.overall_popularity_score is not None else fallback_score(r)
        p=float(direct_priority.get(r.metric_name, 2 if r.metric_unit in {"streams","views","listens"} else 1))
        return (score,p,float(r.metric_value))
    rows.sort(key=k,reverse=True)
    for i,r in enumerate(rows,1):r.rank=i
    return rows


def build_megalist(all_rows:dict[str,list[SongRow]],status:BuildStatus)->list[SongRow]:
    merged=[]
    for name, rows in all_rows.items():
        if name == "countries":
            # Country lists deliberately contain the same hits in many markets. Collapse them
            # first so the megalist keeps one song plus a compact map of all country evidence.
            from .countries import collapse_country_rows_for_megalist
            merged.extend(collapse_country_rows_for_megalist(rows))
        else:
            merged.extend(rows)
    out=_sort_and_rank(dedupe(merged))
    path=DATA/"megalist"/"megalist.csv"
    write_rows(out,path)
    # GitHub rejects individual files above 100 MiB. Keep the canonical plain CSV when it is
    # comfortably below that limit; otherwise replace it with gzip plus <=50k-row CSV parts.
    notes=[]
    if path.exists() and path.stat().st_size > 90*1024*1024:
        import gzip
        gz=path.with_suffix('.csv.gz')
        with path.open('rb') as src, gzip.open(gz,'wb',compresslevel=9) as dst:
            shutil.copyfileobj(src,dst)
        # Chunked plain CSVs remain easy to inspect/use without requiring gzip support.
        for old in path.parent.glob('megalist_part_*.csv'): old.unlink()
        for part_no,start in enumerate(range(0,len(out),50_000),1):
            write_rows(out[start:start+50_000],path.parent/f'megalist_part_{part_no:03d}.csv')
        path.unlink()
        notes.append(f"Canonical megalist compressed to {gz.name} and split into 50k-row CSV parts to stay below GitHub's per-file size limit.")
    # The union itself is mechanically complete only when every upstream requested list is
    # complete. Otherwise it is still a valid deduplicated union of what was retrieved, but must
    # not be presented as the finished user-requested megalist.
    upstream_keys=["anime","vocaloid","worldwide","classical","vtuber_original","emerging","genres","screen_soundtracks","vtuber_non_original","countries"]
    upstream_complete=all(status.datasets.get(k) is not None and status.datasets[k].complete for k in upstream_keys)
    if not upstream_complete:
        notes.append("Megalist is a deduplicated union of materialized rows; completeness is false because one or more upstream requested lists are incomplete.")
    status.datasets["megalist"]=DatasetStatus(target="deduplicated union of all requested lists",rows=len(out),complete=upstream_complete,metric_coverage=dict(_metric_counts(out)),notes=notes)
    status.save();return out


def write_coverage(status:BuildStatus)->None:
    rows=[]
    for name,st in status.datasets.items():
        rows.append({"dataset":name,"target":st.target,"rows":st.rows,"complete":st.complete,"metric_coverage":json.dumps(st.metric_coverage,ensure_ascii=False),"notes":" | ".join(st.notes)})
    p=DATA/"coverage_report.csv";p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["dataset","target","rows","complete","metric_coverage","notes"]);w.writeheader();w.writerows(rows)



def write_status_json(status: BuildStatus) -> None:
    fixed_complete=all(status.datasets.get(k) is not None and status.datasets[k].complete for k in FIXED_TARGETS)
    vocaloid_complete=bool(status.datasets.get("vocaloid") and status.datasets["vocaloid"].complete)
    countries_complete=bool(status.datasets.get("countries") and status.datasets["countries"].complete)
    payload={
      "schema_version":6,
      "requested_at":"2026-07-20",
      "last_build_started_at":status.started_at,
      "last_build_finished_at":status.finished_at,
      "delivery_mode":"github_ready_manual_push_with_network_backed_full_build",
      "accuracy_policy":"No fabricated songs, artists, counts, associations, or quota padding. Exact counters are separated from scores/proxies; every row preserves provenance and snapshot/retrieval timing.",
      "completion_summary":{
        "all_fixed_size_targets_complete":fixed_complete,
        "vocaloid_conditional_corpus_marked_complete":vocaloid_complete,
        "spotify_country_lists_complete":countries_complete,
        "all_requested_lists_complete":fixed_complete and vocaloid_complete and countries_complete,
      },
      "datasets":{k:{"target":v.target,"materialized_rows":v.rows,"complete":v.complete,"metric_coverage":v.metric_coverage,"notes":v.notes} for k,v in status.datasets.items()},
      "source_status":status.sources,
      "warnings":status.warnings,
      "completion_rule":"Do not treat this repository as fully complete unless completion_summary.all_requested_lists_complete is true and validation/target checks pass. Conditional/finite corpora are never padded.",
    }
    (ROOT/"STATUS.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def full_build(*,skip_zenodo:bool=False,only:list[str]|None=None)->BuildStatus:
    status=BuildStatus(started_at=_now())
    for name,target in FIXED_TARGETS.items():status.datasets[name]=DatasetStatus(target=target)
    status.datasets["vocaloid"]=DatasetStatus(target="all verified >=10,000,000 Spotify streams; cap 10,000")
    status.datasets["countries"]=DatasetStatus(target="top 1,000 unique songs for every detected Spotify regional country/territory chart")
    status.save()
    sources=acquire_sources(status,skip_zenodo=skip_zenodo)
    catalog_path=build_catalog(sources,status)
    catalog=load_catalog(catalog_path)
    # Overlay current cumulative Spotify counts from Kworb where exact title+artist matches exist.
    # This materially improves 2026 freshness for globally popular tracks without pretending the
    # entire historical catalog has live counters.
    catalog=apply_kworb_overlay(catalog,fetch_kworb_global(status))
    catalog=apply_kworb_overlay(catalog,fetch_kworb_daily(status))
    wanted=set(only or ["worldwide","genres","classical","emerging","screen_soundtracks","anime","vocaloid","vtuber_original","vtuber_non_original","countries"])
    built:dict[str,list[SongRow]]={}
    def run(name:str, fn):
        if name not in wanted: return
        try:
            built[name]=fn()
        except Exception as exc:
            status.warnings.append(f"{name} build failed: {type(exc).__name__}: {exc}")
            st=status.datasets.get(name)
            if st: st.notes.append(f"build failed: {type(exc).__name__}: {exc}")
            status.save()
    run("worldwide",lambda:build_worldwide(catalog,status))
    run("genres",lambda:build_genres(catalog,status))
    run("classical",lambda:build_classical(catalog,status))
    run("emerging",lambda:build_emerging(catalog,status))
    run("screen_soundtracks",lambda:build_screen_soundtracks(catalog,status))
    run("anime",lambda:build_anime(catalog,sources,status))
    run("vocaloid",lambda:build_vocaloid(catalog,status))
    run("vtuber_original",lambda:build_vtuber(catalog,status,original=True))
    run("vtuber_non_original",lambda:build_vtuber(catalog,status,original=False))
    if "countries" in wanted:
        try:
            from .countries import build_country_lists, country_completion
            built["countries"] = build_country_lists(status, data_dir=DATA)
            complete, rows, detected, complete_markets, notes = country_completion(DATA)
            status.datasets["countries"] = DatasetStatus(
                target=f"1,000 per detected Spotify regional market ({detected} detected this run)",
                rows=rows, complete=complete, metric_coverage=dict(_metric_counts(built["countries"])), notes=notes
            )
            status.save()
        except Exception as exc:
            status.warnings.append(f"countries build failed: {type(exc).__name__}: {exc}")
            status.datasets["countries"].notes.append(f"build failed: {type(exc).__name__}: {exc}")
            status.save()

    # Include existing materialized categories not rebuilt in partial runs.
    paths={
      "anime":DATA/"anime"/"anime_songs.csv","vocaloid":DATA/"vocaloid"/"vocaloid_spotify_10m.csv",
      "worldwide":DATA/"worldwide"/"worldwide_51000.csv","classical":DATA/"classical"/"classical_10000.csv",
      "vtuber_original":DATA/"vtuber_original"/"vtuber_original_10000.csv","emerging":DATA/"emerging"/"emerging_10000.csv",
      "genres":DATA/"genres"/"genres_10000.csv","screen_soundtracks":DATA/"screen_soundtracks"/"screen_soundtracks_10000.csv",
      "vtuber_non_original":DATA/"vtuber_non_original"/"vtuber_non_original_10000.csv",
    }
    from .io import read_rows
    for k,p in paths.items():
        if k not in built and p.exists():
            try:built[k]=read_rows(p)
            except Exception as exc:status.warnings.append(f"existing {k}: {exc}")
    if "countries" not in built:
        try:
            from .countries import load_existing_country_rows, country_completion
            existing_country_rows = load_existing_country_rows(DATA)
            if existing_country_rows:
                built["countries"] = existing_country_rows
                complete, rows, detected, complete_markets, notes = country_completion(DATA)
                status.datasets["countries"] = DatasetStatus(
                    target=f"1,000 per detected Spotify regional market ({detected} detected in index)",
                    rows=rows, complete=complete, metric_coverage=dict(_metric_counts(existing_country_rows)), notes=notes
                )
        except Exception as exc:
            status.warnings.append(f"existing countries: {exc}")
    build_megalist(built,status)
    status.finished_at=_now();write_coverage(status);status.save();write_status_json(status)
    return status


def main(argv:list[str]|None=None)->int:
    import argparse
    ap=argparse.ArgumentParser(description="Build all BeatHit datasets from public source snapshots/APIs")
    ap.add_argument("--skip-zenodo",action="store_true",help="Skip the 931MB 0.9M-track source and use smaller fallbacks")
    ap.add_argument("--only",nargs="*",choices=list(FIXED_TARGETS)+["vocaloid","countries"],help="Build only selected categories")
    a=ap.parse_args(argv)
    st=full_build(skip_zenodo=a.skip_zenodo,only=a.only)
    print(REPORT)
    incomplete=[k for k,v in st.datasets.items() if k!="megalist" and not v.complete]
    if incomplete:
        print("Source-backed shortfalls (not padded):",", ".join(incomplete),file=sys.stderr)
    return 0


if __name__=="__main__":raise SystemExit(main())

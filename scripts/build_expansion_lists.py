#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import httpx
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_megalist.dedupe import norm
from music_megalist.io import read_rows, write_rows
from music_megalist.models import SongRow

DATA = ROOT / "data"
CACHE = ROOT / ".cache" / "beathit-expansion"
TODAY = date.today().isoformat()
MB_API = "https://musicbrainz.org/ws/2"
VOCADB_API = "https://vocadb.net/api/songs"
KH_INDEX = "https://raw.githubusercontent.com/marcus-crane/khinsider-index/main/index.json"
KH_ROOT = "https://downloads.khinsider.com"
PART_SIZE = 10_000
LANGUAGE_TARGET = 100_000

LANGUAGE_COVERS = {
    "russian": ("Russian", "rus", "ru"),
    "japanese": ("Japanese", "jpn", "ja"),
    "german": ("German", "deu", "de"),
    "afrikaans": ("Afrikaans", "afr", "af"),
    "korean": ("Korean", "kor", "ko"),
    "chinese": ("Chinese", "zho", "zh"),
    "ukrainian": ("Ukrainian", "ukr", "uk"),
    "swedish": ("Swedish", "swe", "sv"),
    "norwegian": ("Norwegian", "nor", "no"),
    "indonesian": ("Indonesian", "ind", "id"),
}

BAD_ALBUM = re.compile(
    r"\b(?:tribute|cover album|piano cover|music box|lullaby|karaoke|fan made|fanmade)\b",
    re.I,
)
ARRANGEMENT_ALBUM = re.compile(
    r"\b(?:arrange(?:d|ment)?|remix|orchestral|piano|jazz|lo-?fi|reimagined)\b",
    re.I,
)
OST_ALBUM = re.compile(
    r"\b(?:original(?:\s+video\s+game|\s+game)?\s+soundtrack|soundtrack|ost|gamerip|game\s+score|original\s+score)\b",
    re.I,
)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _artist_credit(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    out: list[str] = []
    for part in value:
        if not isinstance(part, dict):
            continue
        name = str(part.get("name") or (part.get("artist") or {}).get("name") or "").strip()
        if name:
            out.append(name)
        join = str(part.get("joinphrase") or "")
        if join and out:
            out[-1] += join
    return "".join(out).strip()


def _part_files(folder: Path, stem: str) -> list[Path]:
    return sorted(folder.glob(f"{stem}_part_*.csv"))


def _load_existing_parts(folder: Path, stem: str) -> tuple[list[SongRow], set[str]]:
    rows: list[SongRow] = []
    ids: set[str] = set()
    for path in _part_files(folder, stem):
        for row in read_rows(path):
            rows.append(row)
            extra = row.extra or {}
            for key in ("musicbrainz_recording_mbid", "vocadb_id"):
                value = str(extra.get(key) or "").strip().casefold()
                if value:
                    ids.add(value)
            if row.musicbrainz_recording_mbid:
                ids.add(str(row.musicbrainz_recording_mbid).casefold())
    return rows, ids


def _write_partitioned(rows: list[SongRow], folder: Path, stem: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for old in _part_files(folder, stem):
        old.unlink()
    for start in range(0, len(rows), PART_SIZE):
        part = rows[start:start + PART_SIZE]
        for rank, row in enumerate(part, 1):
            row.rank = rank
        write_rows(part, folder / f"{stem}_part_{start // PART_SIZE + 1:03d}.csv")


class MusicBrainzClient:
    def __init__(self) -> None:
        self.client = httpx.Client(
            timeout=60,
            follow_redirects=True,
            headers={"User-Agent": "BeatHit-Dataset/1.0 (+https://github.com/Dummy1-sudo/BeatHit-Dataset)"},
        )
        self.last_request = 0.0

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        # MusicBrainz asks clients to remain at roughly one request/second.
        delay = 1.05 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        last: Exception | None = None
        for attempt in range(4):
            try:
                response = self.client.get(MB_API + path, params={**params, "fmt": "json"})
                self.last_request = time.monotonic()
                if response.status_code == 503 or response.status_code == 429:
                    time.sleep(min(30, 2 ** attempt))
                    continue
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, dict) else {}
            except Exception as exc:
                last = exc
                time.sleep(min(20, 2 ** attempt))
        raise RuntimeError(f"MusicBrainz request failed: {last}")

    def close(self) -> None:
        self.client.close()


def _language_cover_row(recording: dict[str, Any], *, lang_name: str, lang2: str, evidence: str,
                        work_id: str | None = None, score: float = 100.0) -> SongRow | None:
    rid = str(recording.get("id") or "").strip()
    title = str(recording.get("title") or recording.get("name") or "").strip()
    artist = _artist_credit(recording.get("artist-credit"))
    if not rid or not title or not artist:
        return None
    first_date = str(recording.get("first-release-date") or "")
    year = int(first_date[:4]) if re.match(r"^\d{4}", first_date) else None
    return SongRow(
        title=title,
        main_artist=artist,
        release_year=year,
        genres=["cover"],
        languages=[lang2],
        musicbrainz_recording_mbid=rid,
        metric_name="musicbrainz_cover_evidence_score",
        metric_value=float(max(0.0, min(100.0, score))),
        metric_unit="score_0_100",
        overall_popularity_score=float(max(0.0, min(100.0, score))),
        source_url=f"https://musicbrainz.org/recording/{rid}",
        retrieved_at=TODAY,
        source_notes=(
            "Source-backed cover candidate from MusicBrainz. Language is the MusicBrainz work/release "
            "language, not a title-script guess."
        ),
        extra={
            "culture_category": "language_cover",
            "cover_language": lang_name,
            "musicbrainz_recording_mbid": rid,
            "musicbrainz_work_mbid": work_id,
            "cover_evidence": evidence,
        },
    )


def build_language_cover(slug: str, deadline: float, mb: MusicBrainzClient, per_language_requests: int) -> dict[str, Any]:
    lang_name, lang3, lang2 = LANGUAGE_COVERS[slug]
    folder = DATA / "language_covers" / slug
    stem = f"{slug}_covers"
    rows, seen = _load_existing_parts(folder, stem)
    state_path = CACHE / f"language_cover_{slug}.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        state = {}
    state.setdefault("phase", "tag")
    state.setdefault("tag_index", 0)
    state.setdefault("tag_offset", 0)
    state.setdefault("work_offset", 0)
    state.setdefault("source_exhausted", False)
    requests = 0
    changed = False

    tag_queries = [
        f'tag:cover AND lang:{lang3}',
        f'tag:"cover song" AND lang:{lang3}',
    ]

    while len(rows) < LANGUAGE_TARGET and time.monotonic() < deadline and requests < per_language_requests:
        if state["phase"] == "tag":
            tag_index = int(state.get("tag_index") or 0)
            if tag_index >= len(tag_queries):
                state["phase"] = "work"
                state["work_offset"] = int(state.get("work_offset") or 0)
                continue
            offset = int(state.get("tag_offset") or 0)
            data = mb.get("/recording", {"query": tag_queries[tag_index], "limit": 100, "offset": offset})
            requests += 1
            recordings = data.get("recordings") or []
            total = int(data.get("count") or 0)
            for rec in recordings:
                rid = str(rec.get("id") or "").casefold()
                if not rid or rid in seen:
                    continue
                row = _language_cover_row(
                    rec,
                    lang_name=lang_name,
                    lang2=lang2,
                    evidence="musicbrainz recording tag explicitly marks cover",
                    score=_safe_float(rec.get("score"), 80.0),
                )
                if row:
                    rows.append(row)
                    seen.add(rid)
                    changed = True
                    if len(rows) >= LANGUAGE_TARGET:
                        break
            offset += len(recordings)
            if not recordings or offset >= total:
                state["tag_index"] = tag_index + 1
                state["tag_offset"] = 0
            else:
                state["tag_offset"] = offset
            _atomic_json(state_path, state)
            continue

        # Exhaust the work language corpus next. If a song work has recordings by multiple
        # distinct artists, all recordings not belonging to the earliest known artist credit
        # are retained as cover versions. This is stronger evidence than title matching.
        offset = int(state.get("work_offset") or 0)
        query = f"lang:{lang3} AND type:song"
        data = mb.get("/work", {"query": query, "limit": 100, "offset": offset})
        requests += 1
        works = data.get("works") or []
        total = int(data.get("count") or 0)
        if not works:
            state["source_exhausted"] = True
            break
        for work in works:
            if len(rows) >= LANGUAGE_TARGET or time.monotonic() >= deadline or requests >= per_language_requests:
                break
            wid = str(work.get("id") or "").strip()
            if not wid:
                continue
            try:
                rec_data = mb.get(
                    "/recording",
                    {"work": wid, "inc": "artist-credits+releases", "limit": 100, "offset": 0},
                )
                requests += 1
            except Exception:
                continue
            recs = rec_data.get("recordings") or []
            if len(recs) < 2:
                continue
            parsed: list[tuple[str, str, dict[str, Any]]] = []
            for rec in recs:
                artist = _artist_credit(rec.get("artist-credit"))
                date_value = str(rec.get("first-release-date") or "9999-99-99")
                if artist:
                    parsed.append((date_value, norm(artist), rec))
            artist_keys = {artist for _, artist, _ in parsed if artist}
            if len(artist_keys) < 2:
                continue
            parsed.sort(key=lambda item: item[0])
            original_artist = parsed[0][1]
            for _, artist_key, rec in parsed:
                rid = str(rec.get("id") or "").casefold()
                if not rid or rid in seen or artist_key == original_artist:
                    continue
                row = _language_cover_row(
                    rec,
                    lang_name=lang_name,
                    lang2=lang2,
                    evidence="distinct-artist recording of the same MusicBrainz song work",
                    work_id=wid,
                    score=85.0,
                )
                if row:
                    rows.append(row)
                    seen.add(rid)
                    changed = True
                    if len(rows) >= LANGUAGE_TARGET:
                        break
        offset += len(works)
        state["work_offset"] = offset
        if offset >= total:
            state["source_exhausted"] = True
        _atomic_json(state_path, state)
        if state.get("source_exhausted"):
            break

    if changed or not _part_files(folder, stem):
        _write_partitioned(rows[:LANGUAGE_TARGET], folder, stem)
    state["rows"] = min(len(rows), LANGUAGE_TARGET)
    state["complete"] = len(rows) >= LANGUAGE_TARGET
    _atomic_json(state_path, state)
    return {
        "target": LANGUAGE_TARGET,
        "rows": min(len(rows), LANGUAGE_TARGET),
        "complete": len(rows) >= LANGUAGE_TARGET,
        "source_exhausted": bool(state.get("source_exhausted")),
        "requests_this_run": requests,
    }


def _vocadb_artist(item: dict[str, Any]) -> str:
    value = str(item.get("artistString") or "").strip()
    if value:
        return value
    names = []
    for artist in item.get("artists") or []:
        name = str((artist.get("artist") or {}).get("name") or artist.get("name") or "").strip()
        if name:
            names.append(name)
    return ", ".join(names) or "Unknown credited artist"


def build_vocaloid_covers(deadline: float, max_requests: int) -> dict[str, Any]:
    folder = DATA / "vocaloid_covers"
    stem = "vocaloid_covers"
    rows, seen = _load_existing_parts(folder, stem)
    state_path = CACHE / "vocaloid_covers.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        state = {}
    offset = int(state.get("offset") or 0)
    total = state.get("api_total")
    exhaustive = bool(state.get("exhaustive"))
    requests = 0
    changed = False
    headers = {"User-Agent": "BeatHit-Dataset/1.0 (+https://github.com/Dummy1-sudo/BeatHit-Dataset)"}
    with httpx.Client(timeout=60, follow_redirects=True, headers=headers) as client:
        while not exhaustive and time.monotonic() < deadline and requests < max_requests:
            params = {
                "songTypes": "Cover",
                "start": offset,
                "maxResults": 50,
                "sort": "RatingScore",
                "fields": "Artists,Names,PVs",
                "lang": "Default",
            }
            if total is None:
                params["getTotalCount"] = "true"
            response = client.get(VOCADB_API, params=params)
            requests += 1
            response.raise_for_status()
            data = response.json()
            if total is None:
                total = int(data.get("totalCount") or 0)
            items = data.get("items") or []
            if not items:
                exhaustive = True
                break
            for item in items:
                sid = str(item.get("id") or "").strip()
                if not sid or sid.casefold() in seen:
                    continue
                if str(item.get("songType") or "").casefold() != "cover":
                    continue
                title = str(item.get("name") or item.get("defaultName") or "").strip()
                if not title:
                    continue
                pvs = []
                for pv in item.get("pvs") or []:
                    if not isinstance(pv, dict) or bool(pv.get("disabled")):
                        continue
                    pvs.append({
                        "service": pv.get("service"),
                        "pvType": pv.get("pvType"),
                        "pvId": pv.get("pvId"),
                        "url": pv.get("url"),
                    })
                rating = _safe_float(item.get("ratingScore"), 0.0)
                rows.append(SongRow(
                    title=title,
                    main_artist=_vocadb_artist(item),
                    genres=["vocaloid cover", "voice synth cover"],
                    languages=["und"],
                    metric_name="vocadb_rating_score",
                    metric_value=rating,
                    metric_unit="score",
                    overall_popularity_score=rating,
                    source_url=f"https://vocadb.net/S/{sid}",
                    retrieved_at=TODAY,
                    source_notes="VocaDB song entry explicitly classified as Cover; no language guessed from title text.",
                    extra={
                        "culture_category": "vocaloid_cover",
                        "vocadb_id": sid,
                        "vocadb_song_type": "Cover",
                        "pvs": pvs,
                    },
                ))
                seen.add(sid.casefold())
                changed = True
            offset += len(items)
            if total is not None and offset >= int(total):
                exhaustive = True
            state = {"offset": offset, "api_total": total, "exhaustive": exhaustive, "rows": len(rows)}
            _atomic_json(state_path, state)
            time.sleep(0.2)
    if changed or not _part_files(folder, stem):
        rows.sort(key=lambda row: float(row.metric_value or 0.0), reverse=True)
        _write_partitioned(rows, folder, stem)
    state = {"offset": offset, "api_total": total, "exhaustive": exhaustive, "rows": len(rows)}
    _atomic_json(state_path, state)
    return {
        "target": "every VocaDB song explicitly classified Cover",
        "rows": len(rows),
        "complete": exhaustive and total is not None and offset >= int(total),
        "api_total": total,
        "requests_this_run": requests,
    }


def _ascii_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = re.sub(r"\([^)]*\)$", "", value)
    value = re.sub(r"\b(?:original|official|video game|game)?\s*(?:soundtrack|ost|gamerip|score)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _album_class(name: str) -> tuple[str, float]:
    if BAD_ALBUM.search(name):
        return "fan_or_cover", 20.0
    if ARRANGEMENT_ALBUM.search(name):
        return "arrangement", 60.0
    if OST_ALBUM.search(name):
        return "official_or_gamerip", 100.0
    return "unclassified_game_album", 80.0


def _load_kh_index() -> dict[str, str]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE / "khinsider_index.json"
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 7 * 86400:
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            entries = data.get("entries") if isinstance(data, dict) else None
            if isinstance(entries, dict) and entries:
                return {str(k): str(v) for k, v in entries.items()}
        except Exception:
            pass
    response = httpx.get(KH_INDEX, timeout=120, follow_redirects=True, headers={"User-Agent": "BeatHit-Dataset/1.0"})
    response.raise_for_status()
    data = response.json()
    cache_path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    entries = data.get("entries") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in (entries or {}).items()}


def _match_albums(game: str, entries: dict[str, str], max_albums: int = 12) -> list[tuple[float, str, str, str, float]]:
    game_key = _ascii_key(game)
    if not game_key:
        return []
    first = game_key.split()[0]
    candidates = []
    for display, path in entries.items():
        album_key = _ascii_key(display)
        if not album_key:
            continue
        if first not in album_key and game_key not in album_key:
            continue
        score = max(fuzz.ratio(game_key, album_key), fuzz.token_set_ratio(game_key, album_key))
        contained = game_key in album_key or album_key in game_key
        threshold = 82 if contained else 90
        if score < threshold:
            continue
        kind, kind_score = _album_class(display)
        final = score * 0.75 + kind_score * 0.25
        candidates.append((final, display, path, kind, score))
    candidates.sort(reverse=True, key=lambda item: item[0])
    return candidates[:max_albums]


def _album_artist(soup: BeautifulSoup) -> str:
    text = soup.get_text("\n", strip=True)
    for label in ("Artists:", "Artist:", "Composers:", "Composer:"):
        match = re.search(re.escape(label) + r"\s*([^\n]{2,200})", text, re.I)
        if match:
            return match.group(1).strip()
    return "Unknown credited artist"


def _album_tracks(html_text: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html_text, "lxml")
    artist = _album_artist(soup)
    tracks: list[str] = []
    table = soup.select_one("table#songlist") or soup.find("table")
    if table is None:
        return artist, tracks
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if not cells:
            continue
        links = [a for a in tr.find_all("a", href=True) if "/game-soundtracks/album/" in str(a.get("href"))]
        title = ""
        for a in links:
            candidate = a.get_text(" ", strip=True)
            if candidate and not re.fullmatch(r"(?:MP3|FLAC|Download)", candidate, re.I):
                title = candidate
                break
        if not title:
            for cell in cells:
                candidate = re.sub(r"^\s*\d+[.\-)]?\s*", "", cell).strip()
                if candidate and not re.fullmatch(r"\d+(?::\d+)+|MP3|FLAC", candidate, re.I):
                    title = candidate
                    break
        title = title.strip()
        if title and title.casefold() not in {"song name", "track", "title"} and title not in tracks:
            tracks.append(title)
    return artist, tracks


def _slug(value: str) -> str:
    value = _ascii_key(value).replace(" ", "-")
    return value[:100] or "game"


def _existing_game_file(game_rank: int, game: str) -> Path:
    return DATA / "video_games" / "per_game" / f"{game_rank:04d}_{_slug(game)}.csv"


def _build_game_rows(game: str, game_rank: int, entries: dict[str, str], client: httpx.Client) -> list[SongRow]:
    rows: list[SongRow] = []
    seen_titles: set[str] = set()
    for _, display, path, album_kind, match_score in _match_albums(game, entries):
        cache_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", path.strip("/"))[-180:] + ".html"
        cache_path = CACHE / "kh_albums" / cache_name
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists():
            html_text = cache_path.read_text(encoding="utf-8", errors="replace")
        else:
            response = client.get(KH_ROOT + path)
            response.raise_for_status()
            html_text = response.text
            cache_path.write_text(html_text, encoding="utf-8")
            time.sleep(0.15)
        artist, tracks = _album_tracks(html_text)
        for position, title in enumerate(tracks, 1):
            key = norm(title)
            if not key or key in seen_titles:
                continue
            seen_titles.add(key)
            relevance = min(100.0, match_score * 0.85 + _album_class(display)[1] * 0.15)
            game_popularity = max(0.0, 100.0 - math.log10(max(1, game_rank)) * 25.0)
            prominence = max(0.0, 100.0 - min(position - 1, 99) * 0.7)
            popularity = game_popularity * 0.7 + prominence * 0.3
            combined = relevance * 0.6 + popularity * 0.4
            rows.append(SongRow(
                title=title,
                main_artist=artist,
                album=display,
                screen_work=game,
                genres=["video game soundtrack"],
                languages=["und"],
                metric_name="khinsider_track_prominence_proxy",
                metric_value=popularity,
                metric_unit="score_0_100",
                overall_popularity_score=combined,
                source_url=KH_ROOT + path,
                retrieved_at=TODAY,
                source_notes=(
                    "Track title is taken from KHInsider's soundtrack/gamerip album index. "
                    "Popularity is an explicitly labelled proxy based on BeatHit game rank and track prominence."
                ),
                extra={
                    "culture_category": "video_game_music",
                    "video_game": game,
                    "game_rank": game_rank,
                    "game_association_kind": "khinsider_game_soundtrack",
                    "khinsider_album_url": KH_ROOT + path,
                    "khinsider_album_title": display,
                    "khinsider_album_kind": album_kind,
                    "khinsider_album_match_score": match_score,
                    "relevance_score_100": relevance,
                    "popularity_score_100": popularity,
                    "score_100": combined,
                    "popularity_basis": "BeatHit master-list game rank plus track position proxy",
                },
            ))
    rows.sort(key=lambda row: float(row.overall_popularity_score or 0.0), reverse=True)
    for rank, row in enumerate(rows, 1):
        row.rank = rank
    return rows


def _refresh_general_vgm() -> int:
    path = DATA / "video_games" / "video_game_music_10000.csv"
    candidates: list[SongRow] = []
    if path.exists():
        try:
            candidates.extend(read_rows(path))
        except Exception:
            pass
    for file in sorted((DATA / "video_games" / "per_game").glob("*.csv")):
        for row in read_rows(file):
            extra = row.extra or {}
            if extra.get("game_association_kind") != "khinsider_game_soundtrack":
                continue
            if extra.get("khinsider_album_kind") in {"fan_or_cover", "arrangement"}:
                continue
            if float(extra.get("khinsider_album_match_score") or 0) < 88:
                continue
            candidates.append(row)
    candidates.sort(
        key=lambda row: (float(row.overall_popularity_score or 0.0), float(row.metric_value or 0.0)),
        reverse=True,
    )
    out: list[SongRow] = []
    seen_text: set[tuple[str, str]] = set()
    seen_mbid: set[str] = set()
    seen_isrc: set[str] = set()
    seen_spotify: set[str] = set()
    for row in candidates:
        key = (norm(row.title), norm(row.main_artist))
        mbid = str(row.musicbrainz_recording_mbid or "").casefold()
        isrc = str(row.isrc or "").casefold()
        spotify = str(row.spotify_track_id or "")
        if not all(key) or key in seen_text or (mbid and mbid in seen_mbid) or (isrc and isrc in seen_isrc) or (spotify and spotify in seen_spotify):
            continue
        seen_text.add(key)
        if mbid:
            seen_mbid.add(mbid)
        if isrc:
            seen_isrc.add(isrc)
        if spotify:
            seen_spotify.add(spotify)
        out.append(row)
        if len(out) >= 10_000:
            break
    for rank, row in enumerate(out, 1):
        row.rank = rank
    if len(out) > (len(read_rows(path)) if path.exists() else 0):
        write_rows(out, path)
    return len(out)


def build_per_game_vgm(deadline: float, max_games: int) -> dict[str, Any]:
    master = DATA / "seeds" / "beathit_video_games_master_list.txt"
    games = [line.strip() for line in master.read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = _load_kh_index()
    state_path = CACHE / "video_game_per_game.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        state = {}
    next_index = int(state.get("next_index") or 0)
    processed_this_run = 0
    total_tracks = 0
    for path in (DATA / "video_games" / "per_game").glob("*.csv"):
        try:
            total_tracks += len(read_rows(path))
        except Exception:
            pass
    headers = {"User-Agent": "BeatHit-Dataset/1.0 (+https://github.com/Dummy1-sudo/BeatHit-Dataset)"}
    with httpx.Client(timeout=60, follow_redirects=True, headers=headers) as client:
        while next_index < len(games) and processed_this_run < max_games and time.monotonic() < deadline:
            rank = next_index + 1
            game = games[next_index]
            output = _existing_game_file(rank, game)
            if not output.exists():
                try:
                    rows = _build_game_rows(game, rank, entries, client)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    if rows:
                        write_rows(rows, output)
                        total_tracks += len(rows)
                    else:
                        # A zero-byte marker would be invalid CSV; track unmatched games in state instead.
                        state.setdefault("unmatched", []).append({"rank": rank, "game": game})
                except Exception as exc:
                    state.setdefault("errors", []).append({"rank": rank, "game": game, "error": str(exc)[:500]})
                    _atomic_json(state_path, state)
                    break
            next_index += 1
            processed_this_run += 1
            state["next_index"] = next_index
            state["master_games"] = len(games)
            _atomic_json(state_path, state)
    general_path = DATA / "video_games" / "video_game_music_10000.csv"
    try:
        general_rows = len(read_rows(general_path)) if general_path.exists() else 0
    except Exception:
        general_rows = 0
    state["next_index"] = next_index
    state["master_games"] = len(games)
    state["complete"] = next_index >= len(games)
    _atomic_json(state_path, state)
    # Recount because this run may have added files.
    total_tracks = 0
    materialized_games = 0
    for path in (DATA / "video_games" / "per_game").glob("*.csv"):
        try:
            total_tracks += len(read_rows(path))
            materialized_games += 1
        except Exception:
            pass
    return {
        "target": f"all {len(games)} BeatHit master-list games scanned; one separate track list per game when soundtrack evidence exists",
        "rows": total_tracks,
        "complete": next_index >= len(games),
        "games_scanned": next_index,
        "master_games": len(games),
        "materialized_game_lists": materialized_games,
        "unmatched_games": len(state.get("unmatched") or []),
        "general_vgm_rows_preserved": general_rows,
        "processed_this_run": processed_this_run,
    }


def _update_status(results: dict[str, dict[str, Any]]) -> None:
    path = ROOT / "STATUS.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        payload = {}
    datasets = payload.setdefault("datasets", {})
    for name, result in results.items():
        datasets[name] = {
            "target": result.get("target"),
            "materialized_rows": int(result.get("rows") or 0),
            "complete": bool(result.get("complete")),
            "builder_revision": "expansion-v1",
            "metric_coverage": {},
            "notes": [json.dumps({k: v for k, v in result.items() if k not in {"target", "rows", "complete"}}, ensure_ascii=False)],
        }
    summary = payload.setdefault("completion_summary", {})
    core_complete = bool(summary.get("core_lists_complete", summary.get("all_requested_lists_complete", False)))
    expansion_names = [f"cover_{slug}" for slug in LANGUAGE_COVERS] + ["vocaloid_covers", "video_game_per_game"]
    expansion_complete = all(bool((datasets.get(name) or {}).get("complete")) for name in expansion_names)
    summary["core_lists_complete"] = core_complete
    summary["expansion_lists_complete"] = expansion_complete
    summary["all_requested_lists_complete"] = core_complete and expansion_complete
    _atomic_json(path, payload)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build BeatHit expansion lists without replacing the original lists")
    ap.add_argument("--max-seconds", type=int, default=int(os.getenv("BEATHIT_EXPANSION_MAX_SECONDS", "19000")))
    ap.add_argument("--musicbrainz-requests-per-language", type=int, default=int(os.getenv("BEATHIT_COVER_MB_REQUESTS_PER_LANGUAGE", "1200")))
    ap.add_argument("--vocadb-requests", type=int, default=int(os.getenv("BEATHIT_VOCALOID_COVER_REQUESTS", "4000")))
    ap.add_argument("--max-games", type=int, default=int(os.getenv("BEATHIT_PER_GAME_MAX_GAMES", "3162")))
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    overall_deadline = time.monotonic() + max(60, args.max_seconds)
    results: dict[str, dict[str, Any]] = {}

    # Reserve approximately half of the run for language covers, then build VocaDB covers,
    # then spend the remaining time on the game-by-game catalog. All three are resumable.
    mb = MusicBrainzClient()
    try:
        cover_deadline = min(overall_deadline, time.monotonic() + args.max_seconds * 0.50)
        for slug in LANGUAGE_COVERS:
            if time.monotonic() >= cover_deadline:
                break
            per_language_deadline = min(
                cover_deadline,
                time.monotonic() + max(120, (cover_deadline - time.monotonic()) / max(1, len(LANGUAGE_COVERS))),
            )
            results[f"cover_{slug}"] = build_language_cover(
                slug,
                per_language_deadline,
                mb,
                args.musicbrainz_requests_per_language,
            )
    finally:
        mb.close()

    # Preserve status for languages not touched because a prior run consumed its budget.
    for slug in LANGUAGE_COVERS:
        name = f"cover_{slug}"
        if name not in results:
            rows, _ = _load_existing_parts(DATA / "language_covers" / slug, f"{slug}_covers")
            state_path = CACHE / f"language_cover_{slug}.json"
            try:
                state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            except Exception:
                state = {}
            results[name] = {
                "target": LANGUAGE_TARGET,
                "rows": len(rows),
                "complete": len(rows) >= LANGUAGE_TARGET,
                "source_exhausted": bool(state.get("source_exhausted")),
                "requests_this_run": 0,
            }

    if time.monotonic() < overall_deadline:
        vocaloid_deadline = min(overall_deadline, time.monotonic() + args.max_seconds * 0.12)
        results["vocaloid_covers"] = build_vocaloid_covers(vocaloid_deadline, args.vocadb_requests)
    else:
        rows, _ = _load_existing_parts(DATA / "vocaloid_covers", "vocaloid_covers")
        results["vocaloid_covers"] = {"target": "every VocaDB song explicitly classified Cover", "rows": len(rows), "complete": False}

    if time.monotonic() < overall_deadline:
        results["video_game_per_game"] = build_per_game_vgm(overall_deadline, args.max_games)
    else:
        state_path = CACHE / "video_game_per_game.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        except Exception:
            state = {}
        results["video_game_per_game"] = {
            "target": "all 3162 BeatHit master-list games scanned",
            "rows": 0,
            "complete": bool(state.get("complete")),
            "games_scanned": int(state.get("next_index") or 0),
            "master_games": 3162,
        }

    _update_status(results)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

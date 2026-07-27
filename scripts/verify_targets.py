#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_megalist.dedupe import norm
from music_megalist.io import open_text

DATA = ROOT / "data"

FIXED = {
    "anime/anime_songs.csv": 1_000,
    "worldwide/worldwide_51000.csv": 51_000,
    "classical/classical_10000.csv": 10_000,
    "vtuber_original/vtuber_original_10000.csv": 1_000,
    "emerging/emerging_10000.csv": 10_000,
    "genres/genres_10000.csv": 10_000,
    "screen_soundtracks/screen_soundtracks_10000.csv": 10_000,
    "vtuber_non_original/vtuber_non_original_10000.csv": 1_000,
    "video_games/video_game_music_1000.csv": 1_000,
    "internet_native/internet_native_1000.csv": 1_000,
    "electronic_subcultures/electronic_subcultures_1000.csv": 1_000,
    "alternative_extreme/alternative_extreme_1000.csv": 1_000,
    "jazz_depth/jazz_depth_1000.csv": 1_000,
    "children_childhood/children_childhood_100.csv": 100,
    "unserious/unserious_1000.csv": 1_000,
    "special_required/special_required.csv": 4,
}
WORLDWIDE_BUCKETS = {
    "current": 10_000,
    "2020s": 10_000,
    "2010s": 10_000,
    "2000s": 10_000,
    "1990s": 5_000,
    "1980s": 3_000,
    "1970s": 2_000,
    "1960s": 1_000,
}


def read_csv(rel: str) -> list[dict[str, str]]:
    path = DATA / rel
    if not path.exists():
        compressed = path.with_name(path.name + ".gz")
        if not compressed.exists():
            return []
        path = compressed
    with open_text(path, "r", newline="") as f:
        return list(csv.DictReader(f))


def extra(row: dict[str, str]) -> dict:
    try:
        value = json.loads(row.get("extra") or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def song_key(row: dict[str, str]) -> tuple[str, str]:
    return norm(row.get("title") or ""), norm(row.get("main_artist") or "")


def main() -> None:
    report: dict[str, object] = {"fixed_targets": {}, "semantic_checks": {}, "overall_complete": False}
    all_fixed = True
    for rel, target in FIXED.items():
        rows = read_csv(rel)
        ok = len(rows) == target
        all_fixed &= ok
        report["fixed_targets"][rel] = {"target": target, "rows": len(rows), "complete": ok}

    worldwide = read_csv("worldwide/worldwide_51000.csv")
    buckets = Counter(str(extra(r).get("era_bucket") or "") for r in worldwide)
    bucket_result = {
        b: {"target": target, "rows": buckets.get(b, 0), "complete": buckets.get(b, 0) == target}
        for b, target in WORLDWIDE_BUCKETS.items()
    }
    report["semantic_checks"]["worldwide_buckets"] = bucket_result

    genres = read_csv("genres/genres_10000.csv")
    selected_genres = {str(extra(r).get("selection_genre") or "").strip() for r in genres}
    selected_genres.discard("")
    report["semantic_checks"]["genre_diversity"] = {
        "required_genres": 50,
        "observed_genres": len(selected_genres),
        "complete": len(genres) == 10_000 and len(selected_genres) >= 50,
    }

    country_index_path = DATA / "countries" / "index.json"
    country_check = {"detected_markets": 0, "complete_markets": 0, "total_rows": 0, "target_per_country": 1000, "complete": False}
    if country_index_path.exists():
        try:
            ci = json.loads(country_index_path.read_text(encoding="utf-8"))
            detected = int(ci.get("detected_country_markets") or 0)
            complete_markets = int(ci.get("complete_markets") or 0)
            total_rows = int(ci.get("total_materialized_rows") or 0)
            failures = ci.get("failures") or []
            markets = ci.get("markets") or []
            files_ok = all((DATA / "countries" / str(m.get("file") or "")).exists() for m in markets)
            counts_ok = all(int(m.get("unique_songs") or 0) == 1000 for m in markets)
            exhausted = [m for m in markets if bool(m.get("source_exhausted_below_target"))]
            unsupported = ci.get("unsupported_markets") or []
            country_check = {
                "detected_markets": detected, "complete_markets": complete_markets,
                "total_rows": total_rows, "target_per_country": 1000,
                "source_exhausted_markets": len(exhausted),
                "unsupported_index_links": len(unsupported),
                "failures": len(failures), "files_ok": files_ok,
                "complete": detected > 0 and complete_markets == detected and len(markets) == detected
                            and total_rows == detected * 1000 and not failures and not unsupported
                            and files_ok and counts_ok,
            }
        except Exception as exc:
            country_check["error"] = str(exc)
    report["semantic_checks"]["spotify_country_top1000"] = country_check

    vocaloid = read_csv("vocaloid/vocaloid_originals_youtube_views.csv")
    invalid_vocaloid = []
    vocaloid_ids = []
    for i, r in enumerate(vocaloid, 1):
        try:
            value = float(r.get("metric_value") or 0)
            view_count = int(float(r.get("view_count") or 0))
        except Exception:
            value = 0
            view_count = 0
        e = extra(r)
        try:
            official_total = int(e.get("official_youtube_total_views") or 0)
            resolved_pvs = int(e.get("official_youtube_resolved_pv_count") or 0)
            highest_individual = int(e.get("highest_individual_official_pv_views") or 0)
        except Exception:
            official_total = 0
            resolved_pvs = 0
            highest_individual = 0
        voice_synths = e.get("voice_synth_vocalists")
        voice_synth_types = e.get("voice_synth_types")
        official_pvs = e.get("official_youtube_pvs")
        pv_ids = []
        pv_sum = 0
        selected_pv_views = 0
        selected_video_id = str(e.get("youtube_video_id") or "").strip()
        vocadb_id = str(e.get("vocadb_id") or "").strip()
        if vocadb_id:
            vocaloid_ids.append(vocadb_id)
        if isinstance(official_pvs, list):
            for pv in official_pvs:
                if not isinstance(pv, dict):
                    continue
                video_id = str(pv.get("video_id") or "").strip()
                try:
                    views = int(pv.get("views") or 0)
                except Exception:
                    views = 0
                if video_id:
                    pv_ids.append(video_id)
                    pv_sum += views
                    if video_id == selected_video_id:
                        selected_pv_views = views
        valid = (
            r.get("metric_name") == "youtube_views"
            and r.get("metric_unit") == "views"
            and value >= 0
            and view_count == int(value)
            and highest_individual == int(value)
            and selected_pv_views == int(value)
            and official_total == pv_sum
            and resolved_pvs == len(pv_ids)
            and len(pv_ids) == len(set(pv_ids))
            and isinstance(voice_synths, list)
            and bool(voice_synths)
            and isinstance(voice_synth_types, dict)
            and set(voice_synths).issubset(set(voice_synth_types))
            and str(e.get("vocadb_song_type") or "").casefold() == "original"
            and str(e.get("youtube_pv_type") or "").casefold() == "original"
            and str(e.get("youtube_pv_service") or "").casefold() == "youtube"
            and bool(vocadb_id)
            and bool(selected_video_id)
            and str(e.get("qualification_method") or "") == "official_original_youtube_pv"
        )
        if not valid:
            invalid_vocaloid.append(i)
    duplicate_vocaloid_ids = len(vocaloid_ids) - len(set(vocaloid_ids))
    report["semantic_checks"]["vocaloid_original_youtube_corpus"] = {
        "target": "every VocaDB Original voice-synth song with a resolved official Original YouTube PV, ordered by views",
        "rows": len(vocaloid),
        "invalid_rows": invalid_vocaloid[:100],
        "duplicate_vocadb_ids": duplicate_vocaloid_ids,
        "row_validity": not invalid_vocaloid and duplicate_vocaloid_ids == 0,
        "corpus_completeness": "Read STATUS.json datasets.vocaloid.complete; row validity does not prove exhaustive source coverage.",
    }

    kpop = read_csv("kpop/kpop_youtube_over_100m.csv")
    invalid_kpop = []
    kpop_keys = []
    for i, r in enumerate(kpop, 1):
        try:
            value = float(r.get("metric_value") or 0)
            view_count = int(float(r.get("view_count") or 0))
        except Exception:
            value = 0
            view_count = 0
        e = extra(r)
        verified_artist = str(e.get("verified_kpop_artist") or "").strip()
        if (
            r.get("metric_name") != "youtube_views"
            or r.get("metric_unit") != "views"
            or value <= 100_000_000
            or view_count != int(value)
            or not bool(e.get("kpop_artist_verified"))
            or not verified_artist
            or norm(r.get("main_artist") or "") != norm(verified_artist)
            or not str(r.get("source_url") or "").strip()
        ):
            invalid_kpop.append(i)
        kpop_keys.append((norm(r.get("title") or ""), norm(verified_artist or r.get("main_artist") or "")))
    duplicate_kpop = len(kpop_keys) - len(set(kpop_keys))
    report["semantic_checks"]["kpop_youtube_threshold"] = {
        "target": "official videos above 100,000,000 views found by the fully scanned audited K-pop artist registry",
        "rows": len(kpop),
        "invalid_rows": invalid_kpop[:100],
        "duplicate_title_artist_keys": duplicate_kpop,
        "threshold_valid": not invalid_kpop and duplicate_kpop == 0,
        "corpus_completeness": "Read STATUS.json datasets.kpop.complete; completion is scoped to the audited artist registry, not every internet upload labeled K-pop.",
    }

    video_games = read_csv("video_games/video_game_music_1000.csv")
    invalid_video_games = []
    reject_game_text = re.compile(
        r"\b(?:motion picture|film soundtrack|movie soundtrack|television soundtrack|"
        r"original tv series|anime score|anime soundtrack|music from the video game|"
        r"piano collections?|for piano solo|soundtrack for piano|tribute|music box|"
        r"cover album|played by|remix album)\b",
        re.I,
    )
    reject_screen_work = re.compile(
        r"\b(?:soundtrack|original score|volume|vol\.?|music collection|for piano|played by)\b",
        re.I,
    )
    allowed_associations = {
        "official_soundtrack_album",
        "franchise_artist",
        "explicit_track_reference",
        "genre_album",
        "listenbrainz_release_group_tag",
    }
    seen_game_songs = set()
    for i, r in enumerate(video_games, 1):
        e = extra(r)
        screen_work = str(r.get("screen_work") or "").strip()
        evidence = f"{r.get('album') or ''} | {screen_work} | {r.get('genres') or ''}"
        association = str(e.get("game_association_kind") or "")
        game_song_key = (norm(screen_work), norm(r.get("title") or ""), norm(r.get("main_artist") or ""))
        listenbrainz_evidence_valid = bool(
            association != "listenbrainz_release_group_tag"
            or (
                str(r.get("musicbrainz_recording_mbid") or "").strip()
                and str(e.get("listenbrainz_source_scope") or "") == "release-group"
                and bool(e.get("listenbrainz_source_tags"))
                and str(e.get("explicit_soundtrack_release") or "").strip()
            )
        )
        invalid = (
            not screen_work
            or str(e.get("culture_category") or "") != "video_game_music"
            or association not in allowed_associations
            or reject_game_text.search(evidence)
            or reject_screen_work.search(screen_work)
            or (association == "genre_album" and not str(e.get("game_wikidata_id") or "").strip())
            or not listenbrainz_evidence_valid
            or game_song_key in seen_game_songs
        )
        if invalid:
            invalid_video_games.append(i)
        seen_game_songs.add(game_song_key)
    report["semantic_checks"]["video_game_music_classification"] = {
        "rows": len(video_games),
        "invalid_rows": invalid_video_games[:100],
        "complete": len(video_games) == 1_000 and not invalid_video_games,
    }

    special = read_csv("special_required/special_required.csv")
    required_special = {
        "Beethoven Virus",
        "The Pi Song (100 Digits of π)",
        "SpongeBob SquarePants Theme",
        "Pink Fluffy Unicorns Dancing on Rainbows",
    }
    observed_special = {str(r.get("title") or "") for r in special}
    report["semantic_checks"]["special_required"] = {
        "required": sorted(required_special),
        "missing": sorted(required_special - observed_special),
        "complete": required_special.issubset(observed_special),
    }

    for rel, expected in [
        ("vtuber_original/vtuber_original_10000.csv", True),
        ("vtuber_non_original/vtuber_non_original_10000.csv", False),
    ]:
        rows = read_csv(rel)
        wrong = [i for i, r in enumerate(rows, 1) if str(r.get("is_original") or "").strip().casefold() not in ({"true", "1"} if expected else {"false", "0"})]
        report["semantic_checks"][f"vtuber_{'original' if expected else 'cover'}_classification"] = {
            "rows": len(rows), "invalid_rows": wrong[:100], "complete": not wrong
        }

    mega = read_csv("megalist/megalist.csv")
    if not mega:
        # The canonical union can be compressed/split when large.
        parts = sorted((DATA / "megalist").glob("megalist_part_*.csv"))
        for p in parts:
            with open_text(p, "r", newline="") as f:
                mega.extend(csv.DictReader(f))
    invalid_languages = []
    for i, r in enumerate(mega, 1):
        try:
            languages = json.loads(r.get("languages") or "[]")
            if not isinstance(languages, list) or not languages or any(not str(x).strip() for x in languages):
                invalid_languages.append(i)
        except Exception:
            invalid_languages.append(i)
    report["semantic_checks"]["megalist_languages"] = {
        "rows": len(mega),
        "invalid_rows": invalid_languages[:100],
        "complete": bool(mega) and not invalid_languages,
    }

    keys = [song_key(r) for r in mega if all(song_key(r))]
    dupes = len(keys) - len(set(keys))
    report["semantic_checks"]["megalist_deduplication"] = {
        "rows": len(mega), "duplicate_title_main_artist_keys": dupes, "complete": dupes == 0
    }

    status_path = ROOT / "STATUS.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    status_complete = bool((status.get("completion_summary") or {}).get("all_requested_lists_complete"))
    semantic_ok = (
        all(x["complete"] for x in bucket_result.values())
        and len(genres) == 10_000 and len(selected_genres) >= 50
        and not invalid_vocaloid
        and duplicate_vocaloid_ids == 0
        and not invalid_kpop
        and duplicate_kpop == 0
        and len(video_games) == 1_000
        and not invalid_video_games
        and required_special.issubset(observed_special)
        and not invalid_languages
        and bool(country_check.get("complete"))
        and dupes == 0
    )
    report["overall_complete"] = bool(all_fixed and semantic_ok and status_complete)
    report["status_claims_all_requested_complete"] = status_complete
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

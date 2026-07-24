#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_megalist.dedupe import norm

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
        return []
    with path.open(encoding="utf-8", newline="") as f:
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

    vocaloid = read_csv("vocaloid/vocaloid_youtube_100m.csv")
    invalid_vocaloid = []
    for i, r in enumerate(vocaloid, 1):
        try:
            value = float(r.get("metric_value") or 0)
            view_count = int(float(r.get("view_count") or 0))
        except Exception:
            value = 0
            view_count = 0
        e = extra(r)
        valid = (
            r.get("metric_name") == "youtube_views"
            and r.get("metric_unit") == "views"
            and value >= 100_000_000
            and view_count == int(value)
            and str(e.get("vocadb_song_type") or "").casefold() == "original"
            and str(e.get("youtube_pv_type") or "").casefold() == "original"
            and str(e.get("youtube_pv_service") or "").casefold() == "youtube"
            and bool(str(e.get("youtube_video_id") or "").strip())
        )
        if not valid:
            invalid_vocaloid.append(i)
    report["semantic_checks"]["vocaloid_threshold"] = {
        "target": "VocaDB Original voice-synth songs with one official Original YouTube PV >=100,000,000 views; cap 10,000",
        "rows": len(vocaloid),
        "invalid_rows": invalid_vocaloid[:100],
        "threshold_valid": not invalid_vocaloid and len(vocaloid) <= 10_000,
        "corpus_completeness": "Read STATUS.json datasets.vocaloid.complete; threshold validity does not prove exhaustive source coverage.",
    }

    kpop = read_csv("kpop/kpop_youtube_over_100m.csv")
    invalid_kpop = []
    for i, r in enumerate(kpop, 1):
        try:
            value = float(r.get("metric_value") or 0)
            view_count = int(float(r.get("view_count") or 0))
        except Exception:
            value = 0
            view_count = 0
        if (
            r.get("metric_name") != "youtube_views"
            or r.get("metric_unit") != "views"
            or value <= 100_000_000
            or view_count != int(value)
        ):
            invalid_kpop.append(i)
    report["semantic_checks"]["kpop_youtube_threshold"] = {
        "target": "all materialized source-tagged K-pop songs strictly above 100,000,000 observed YouTube views",
        "rows": len(kpop),
        "invalid_rows": invalid_kpop[:100],
        "threshold_valid": not invalid_kpop,
        "corpus_completeness": "Read STATUS.json datasets.kpop.complete; current source catalog is not claimed exhaustive.",
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
            with p.open(encoding="utf-8", newline="") as f:
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
        and not invalid_kpop
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

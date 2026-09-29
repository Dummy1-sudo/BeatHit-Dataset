#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_megalist.io import open_text

DATA = ROOT / "data"
TEN_K = [
    "anime/anime_songs.csv",
    "classical/classical_10000.csv",
    "vtuber_original/vtuber_original_10000.csv",
    "emerging/emerging_10000.csv",
    "genres/genres_10000.csv",
    "screen_soundtracks/screen_soundtracks_10000.csv",
    "vtuber_non_original/vtuber_non_original_10000.csv",
    "video_games/video_game_music_10000.csv",
    "internet_native/internet_native_10000.csv",
    "electronic_subcultures/electronic_subcultures_10000.csv",
    "alternative_extreme/alternative_extreme_10000.csv",
    "jazz_depth/jazz_depth_10000.csv",
    "children_childhood/children_childhood_10000.csv",
    "unserious/unserious_10000.csv",
]
LANGS = ["russian","japanese","german","afrikaans","korean","chinese","ukrainian","swedish","norwegian","indonesian"]


def count_csv(path: Path) -> int:
    if not path.exists():
        return 0
    with open_text(path, "r", newline="") as f:
        return max(0, sum(1 for _ in csv.reader(f)) - 1)


def count_parts(folder: Path, prefix: str) -> int:
    return sum(count_csv(p) for p in sorted(folder.glob(prefix + "_part_*.csv")))


def main() -> int:
    result: dict[str, object] = {"core": {}, "expansion": {}}
    ok = True
    for rel in TEN_K:
        n = count_csv(DATA / rel)
        complete = n > 9_000
        result["core"][rel] = {"rows": n, "complete": complete, "policy": ">9000"}
        ok &= complete

    worldwide = count_csv(DATA / "worldwide" / "worldwide_51000.csv")
    result["core"]["worldwide/worldwide_51000.csv"] = {"rows": worldwide, "complete": worldwide >= 51_000}
    ok &= worldwide >= 51_000

    country_index = DATA / "countries" / "index.json"
    country_complete = False
    if country_index.exists():
        ci = json.loads(country_index.read_text(encoding="utf-8"))
        markets = ci.get("markets") or []
        detected = int(ci.get("detected_country_markets") or 0)
        country_complete = bool(detected and len(markets) == detected and all(int(m.get("unique_songs") or 0) >= 1000 for m in markets))
        result["core"]["countries"] = {
            "detected": detected,
            "built": len(markets),
            "complete": country_complete,
            "short": [m.get("country_code") for m in markets if int(m.get("unique_songs") or 0) < 1000],
        }
    else:
        result["core"]["countries"] = {"complete": False, "reason": "missing index"}
    ok &= country_complete

    for lang in LANGS:
        n = count_parts(DATA / "language_covers" / lang, f"{lang}_covers")
        complete = n >= 100_000
        result["expansion"][f"cover_{lang}"] = {"rows": n, "target": 100_000, "complete": complete}
        ok &= complete

    v = count_parts(DATA / "vocaloid_covers", "vocaloid_covers")
    status = json.loads((ROOT / "STATUS.json").read_text(encoding="utf-8")) if (ROOT / "STATUS.json").exists() else {}
    v_complete = bool(((status.get("datasets") or {}).get("vocaloid_covers") or {}).get("complete"))
    result["expansion"]["vocaloid_covers"] = {"rows": v, "complete": v_complete}
    ok &= v_complete

    game_state = ROOT / ".cache" / "beathit-expansion" / "video_game_per_game.json"
    per_game_status = ((status.get("datasets") or {}).get("video_game_per_game") or {})
    scanned = int(per_game_status.get("notes") is not None and per_game_status.get("materialized_rows") or 0)
    game_complete = bool(per_game_status.get("complete"))
    result["expansion"]["video_game_per_game"] = {
        "complete": game_complete,
        "materialized_track_rows": int(per_game_status.get("materialized_rows") or 0),
    }
    ok &= game_complete

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

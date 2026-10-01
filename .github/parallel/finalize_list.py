#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ALL_TARGETS, DATA, iter_csvs, read_json, status_path, target_paths, write_json
from music_megalist.dedupe import norm
from music_megalist.io import read_rows, write_rows
from music_megalist.validate import _generic_csv_errors

PART_SIZE = 10_000


def _aliases(row, *, vocaloid_ids_only: bool = False, anime_scope: bool = False):
    extra = row.extra or {}
    if vocaloid_ids_only:
        vid = str(extra.get("vocadb_id") or "").casefold()
        return ("vocadb", vid) if vid else ("fallback", norm(row.title), norm(row.main_artist))

    scope = ()
    if anime_scope:
        anime_id = extra.get("mal_id") or extra.get("anilist_id") or extra.get("anime_title") or ""
        scope = (str(anime_id),)

    ids = []
    if row.spotify_track_id:
        ids.append(("spotify", str(row.spotify_track_id).casefold()))
    if row.musicbrainz_recording_mbid:
        ids.append(("mbid", str(row.musicbrainz_recording_mbid).casefold()))
    if row.isrc:
        ids.append(("isrc", str(row.isrc).casefold()))
    vocadb = str(extra.get("vocadb_id") or "").casefold()
    if vocadb:
        ids.append(("vocadb", vocadb))
    text = ("text", norm(row.title), norm(row.main_artist))
    return scope, ids, text


def _dedupe_rows(rows, *, vocaloid_ids_only: bool = False, anime_scope: bool = False):
    out = []
    seen_ids = set()
    seen_text = set()
    seen_vocadb = set()
    for row in rows:
        if vocaloid_ids_only:
            key = _aliases(row, vocaloid_ids_only=True)
            if key in seen_vocadb:
                continue
            seen_vocadb.add(key)
            out.append(row)
            continue
        scope, ids, text = _aliases(row, anime_scope=anime_scope)
        scoped_ids = [(scope, *item) for item in ids]
        scoped_text = (scope, *text)
        if any(key in seen_ids for key in scoped_ids) or scoped_text in seen_text:
            continue
        seen_ids.update(scoped_ids)
        seen_text.add(scoped_text)
        out.append(row)
    return out


def _rewrite_one(path: Path, *, target: str) -> int:
    rows = read_rows(path)
    rows = _dedupe_rows(
        rows,
        vocaloid_ids_only=(target == "vocaloid"),
        anime_scope=(target == "anime"),
    )
    for rank, row in enumerate(rows, 1):
        row.rank = rank
    write_rows(rows, path)
    return len(rows)


def _rewrite_partitioned(folder: Path, stem: str) -> int:
    files = sorted(folder.glob(f"{stem}_part_*.csv"))
    rows = []
    for path in files:
        rows.extend(read_rows(path))
    rows = _dedupe_rows(rows)
    for old in files:
        old.unlink()
    for start in range(0, len(rows), PART_SIZE):
        part = rows[start:start + PART_SIZE]
        for rank, row in enumerate(part, 1):
            row.rank = rank
        write_rows(part, folder / f"{stem}_part_{start // PART_SIZE + 1:03d}.csv")
    return len(rows)


def _finalize(target: str) -> int:
    if target == "vocaloid_covers":
        return _rewrite_partitioned(DATA / "vocaloid_covers", "vocaloid_covers")
    if target.startswith("cover_"):
        slug = target.removeprefix("cover_")
        return _rewrite_partitioned(DATA / "language_covers" / slug, f"{slug}_covers")
    if target == "per_game_vgm":
        total = 0
        folder = DATA / "video_games" / "per_game"
        for path in sorted(folder.glob("*.csv")):
            total += _rewrite_one(path, target=target)
        return total
    if target == "countries":
        total = 0
        for path in sorted((DATA / "countries").glob("*.csv")):
            total += _rewrite_one(path, target=target)
        return total
    paths = [p for p in target_paths(target) if p.is_file()]
    if not paths:
        return 0
    return sum(_rewrite_one(path, target=target) for path in paths)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ALL_TARGETS:
        return 2
    target = sys.argv[1]
    fragment_path = status_path(target)
    fragment = read_json(fragment_path)
    rows = _finalize(target)

    errors = []
    for path in iter_csvs(target):
        errors.extend(_generic_csv_errors(path))

    dataset = fragment.setdefault("dataset", {})
    dataset["materialized_rows"] = rows
    if dataset.get("target") == 10_000 and rows > 9_000:
        dataset["complete"] = True
    if target.startswith("cover_"):
        dataset["complete"] = rows >= 100_000
    fragment["deduplicated_rows"] = rows
    fragment["validation_errors"] = errors[:200]
    write_json(fragment_path, fragment)

    if errors:
        for error in errors[:100]:
            print(error, file=sys.stderr)
        return 1
    print(json.dumps({"target": target, "rows": rows, "validation_errors": 0}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

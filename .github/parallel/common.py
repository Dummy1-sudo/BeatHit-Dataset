from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
PARALLEL = ROOT / ".parallel"
STATUS_DIR = PARALLEL / "status"

CORE_TARGETS = {"anime", "vocaloid", "video_game_music", "children_childhood", "countries"}
EXPANSION_TARGETS = {
    "per_game_vgm", "vocaloid_covers",
    "cover_russian", "cover_japanese", "cover_german", "cover_afrikaans",
    "cover_korean", "cover_chinese", "cover_ukrainian", "cover_swedish",
    "cover_norwegian", "cover_indonesian",
}
ALL_TARGETS = CORE_TARGETS | EXPANSION_TARGETS


def status_path(target: str) -> Path:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    return STATUS_DIR / f"{target}.json"


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def dataset_name(target: str) -> str:
    if target == "per_game_vgm":
        return "video_game_per_game"
    return target


def target_paths(target: str) -> list[Path]:
    if target == "anime":
        return [DATA / "anime" / "anime_songs.csv"]
    if target == "vocaloid":
        gz = DATA / "vocaloid" / "vocaloid_originals_youtube_views.csv.gz"
        plain = DATA / "vocaloid" / "vocaloid_originals_youtube_views.csv"
        return [gz if gz.exists() else plain]
    if target == "video_game_music":
        return [DATA / "video_games" / "video_game_music_10000.csv"]
    if target == "children_childhood":
        return [DATA / "children_childhood" / "children_childhood_10000.csv"]
    if target == "countries":
        return [DATA / "countries"]
    if target == "per_game_vgm":
        return [DATA / "video_games" / "per_game"]
    if target == "vocaloid_covers":
        return [DATA / "vocaloid_covers"]
    if target.startswith("cover_"):
        return [DATA / "language_covers" / target.removeprefix("cover_")]
    raise KeyError(target)


def iter_csvs(target: str) -> Iterable[Path]:
    for path in target_paths(target):
        if path.is_file() and (path.name.endswith(".csv") or path.name.endswith(".csv.gz")):
            yield path
        elif path.is_dir():
            yield from sorted(path.rglob("*.csv"))
            yield from sorted(path.rglob("*.csv.gz"))


def copy_target_output(target: str, destination_root: Path) -> None:
    for src in target_paths(target):
        if not src.exists():
            continue
        rel = src.relative_to(ROOT)
        dst = destination_root / rel
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

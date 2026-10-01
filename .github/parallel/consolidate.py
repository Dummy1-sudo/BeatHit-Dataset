#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import read_json, write_json
from music_megalist import fullbuild

EXPANSION = [
    "cover_russian", "cover_japanese", "cover_german", "cover_afrikaans",
    "cover_korean", "cover_chinese", "cover_ukrainian", "cover_swedish",
    "cover_norwegian", "cover_indonesian", "vocaloid_covers", "video_game_per_game",
]


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)


def main() -> int:
    incoming = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "incoming"
    status = read_json(ROOT / "STATUS.json")
    datasets = status.setdefault("datasets", {})
    successful = []
    failed = []

    for artifact in sorted(incoming.glob("result-*")):
        fragments = list((artifact / "status").glob("*.json"))
        if not fragments:
            continue
        fragment = read_json(fragments[0])
        target = str(fragment.get("target_name") or fragments[0].stem)
        if not fragment.get("worker_success"):
            failed.append(target)
            continue
        _copy_tree(artifact / "data", ROOT / "data")
        name = str(fragment.get("dataset_name") or target)
        dataset = dict(fragment.get("dataset") or {})
        dataset["builder_revision"] = "parallel-workers-v1"
        datasets[name] = dataset
        successful.append(target)

    # Per-game soundtrack evidence is merged only after every producer is finished, so the
    # aggregate VGM worker never races the per-game worker.
    if "per_game_vgm" in successful:
        try:
            import build_remaining_core
            merged_rows = build_remaining_core._merge_per_game_into_general_vgm()
            ds = datasets.setdefault("video_game_music", {})
            ds["materialized_rows"] = merged_rows
            if merged_rows > 9_000:
                ds["complete"] = True
        except Exception as exc:
            failed.append(f"aggregate_vgm_merge:{type(exc).__name__}:{exc}")

    for name, target in fullbuild.FIXED_TARGETS.items():
        ds = datasets.get(name) or {}
        rows = int(ds.get("materialized_rows") or 0)
        if target == 10_000 and rows > 9_000:
            ds["complete"] = True
        datasets[name] = ds

    summary = status.setdefault("completion_summary", {})
    fixed_complete = all(bool((datasets.get(name) or {}).get("complete")) for name in fullbuild.FIXED_TARGETS)
    core_complete = (
        fixed_complete
        and bool((datasets.get("vocaloid") or {}).get("complete"))
        and bool((datasets.get("kpop") or {}).get("complete"))
        and bool((datasets.get("countries") or {}).get("complete"))
    )
    expansion_complete = all(bool((datasets.get(name) or {}).get("complete")) for name in EXPANSION)
    summary["all_fixed_size_targets_complete"] = fixed_complete
    summary["core_lists_complete"] = core_complete
    summary["expansion_lists_complete"] = expansion_complete
    summary["all_requested_lists_complete"] = core_complete and expansion_complete
    summary["last_parallel_successes"] = successful
    summary["last_parallel_failures"] = failed
    write_json(ROOT / "STATUS.json", status)
    write_json(ROOT / ".parallel" / "consolidation.json", {"successful": successful, "failed": failed})
    print(json.dumps({"successful": successful, "failed": failed}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

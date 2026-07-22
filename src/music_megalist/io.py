from __future__ import annotations
import csv, json
from pathlib import Path
from typing import Iterable
from .models import SongRow

FIELDS = list(SongRow.model_fields)


def append_row(row: SongRow, path: str | Path) -> int:
    """Append one row immediately and flush it to disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".jsonl":
        with path.open("a", encoding="utf-8") as f:
            f.write(row.model_dump_json() + "\n")
            f.flush()
        return 1
    needs_header = not path.exists() or path.stat().st_size == 0
    d = row.model_dump()
    for k, v in d.items():
        if isinstance(v, (list, dict)):
            d[k] = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if needs_header:
            w.writeheader()
        w.writerow(d)
        f.flush()
    return 1


def write_rows(rows: Iterable[SongRow], path: str | Path) -> int:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    if path.suffix == ".jsonl":
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(row.model_dump_json() + "\n")
    else:
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for row in rows:
                d = row.model_dump()
                for k, v in d.items():
                    if isinstance(v, (list, dict)):
                        d[k] = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
                w.writerow(d)
    return len(rows)

def read_rows(path: str | Path) -> list[SongRow]:
    path = Path(path)
    out: list[SongRow] = []
    if path.suffix == ".jsonl":
        for line in path.read_text("utf-8").splitlines():
            if line.strip(): out.append(SongRow.model_validate_json(line))
        return out
    with path.open(encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            for k in ("featured_artists", "genres"):
                d[k] = json.loads(d[k] or "[]")
            d["extra"] = json.loads(d.get("extra") or "{}")
            for k in ("rank", "release_year", "anime_popularity", "listen_count", "view_count"):
                d[k] = int(d[k]) if d.get(k) else None
            for k in ("metric_value", "overall_popularity_score"):
                d[k] = float(d[k]) if d.get(k) else None
            if d.get("is_original") in ("True","False"):
                d["is_original"] = d["is_original"] == "True"
            elif not d.get("is_original"): d["is_original"] = None
            out.append(SongRow.model_validate(d))
    return out

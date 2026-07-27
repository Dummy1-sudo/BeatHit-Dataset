from __future__ import annotations
import csv, gzip, io, json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, TextIO
from .models import SongRow

FIELDS = list(SongRow.model_fields)


@contextmanager
def open_text(
    path: str | Path,
    mode: str,
    *,
    newline: str | None = None,
) -> Iterator[TextIO]:
    """Open UTF-8 text, transparently handling deterministic gzip files."""
    path = Path(path)
    if path.suffix != ".gz":
        with path.open(mode, encoding="utf-8", newline=newline) as handle:
            yield handle
        return
    if mode == "r":
        with gzip.open(path, "rt", encoding="utf-8", newline=newline) as handle:
            yield handle
        return
    if mode not in {"w", "a"}:
        raise ValueError(f"Unsupported text mode for gzip file: {mode}")
    binary_mode = f"{mode}b"
    with path.open(binary_mode) as raw:
        # Empty filename and mtime=0 make identical datasets byte-for-byte stable.
        with gzip.GzipFile(
            filename="",
            mode=binary_mode,
            fileobj=raw,
            compresslevel=9,
            mtime=0,
        ) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline=newline) as handle:
                yield handle


def append_row(row: SongRow, path: str | Path) -> int:
    """Append one row immediately and flush it to disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz"):
        with open_text(path, "a") as f:
            f.write(row.model_dump_json() + "\n")
            f.flush()
        return 1
    needs_header = not path.exists() or path.stat().st_size == 0
    d = row.model_dump()
    for k, v in d.items():
        if isinstance(v, (list, dict)):
            d[k] = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    with open_text(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        if needs_header:
            w.writeheader()
        w.writerow(d)
        f.flush()
    return 1


def write_rows(rows: Iterable[SongRow], path: str | Path) -> int:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    if path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz"):
        with open_text(path, "w") as f:
            for row in rows:
                f.write(row.model_dump_json() + "\n")
    else:
        with open_text(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
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
    if path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz"):
        with open_text(path, "r") as f:
            for line in f:
                if line.strip(): out.append(SongRow.model_validate_json(line))
        return out
    with open_text(path, "r", newline="") as f:
        for d in csv.DictReader(f):
            for k in ("featured_artists", "genres"):
                d[k] = json.loads(d[k] or "[]")
            # Backward compatibility for materialized CSVs created before languages existed.
            if "languages" in d:
                d["languages"] = json.loads(d.get("languages") or '["und"]')
            else:
                d["languages"] = ["und"]
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

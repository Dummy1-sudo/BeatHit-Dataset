from pathlib import Path

from music_megalist.io import read_rows, write_rows
from music_megalist.models import SongRow


def _row(rank: int = 1) -> SongRow:
    return SongRow(
        rank=rank,
        title=f"Song {rank}",
        main_artist="Producer",
        languages=["ja"],
        metric_name="youtube_views",
        metric_value=float(1000 - rank),
        metric_unit="views",
        view_count=1000 - rank,
        source_url=f"https://www.youtube.com/watch?v=video{rank}",
        retrieved_at="2026-07-26",
        extra={"vocadb_id": rank},
    )


def test_csv_gzip_round_trip_is_deterministic(tmp_path: Path):
    path = tmp_path / "rows.csv.gz"
    rows = [_row(1), _row(2)]

    assert write_rows(rows, path) == 2
    first_bytes = path.read_bytes()
    assert first_bytes.startswith(b"\x1f\x8b")
    restored = read_rows(path)
    assert [row.title for row in restored] == [row.title for row in rows]
    assert [row.view_count for row in restored] == [row.view_count for row in rows]
    assert [row.extra["vocadb_id"] for row in restored] == [1, 2]

    assert write_rows(rows, path) == 2
    assert path.read_bytes() == first_bytes

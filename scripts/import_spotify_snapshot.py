#!/usr/bin/env python3
from __future__ import annotations
import csv
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/raw/spotify_top10000_streamed_songs_2023.csv"
DST = ROOT / "data/worldwide/worldwide_spotify_top10000_snapshot.csv"
SOURCE_URL = "https://www.kaggle.com/datasets/rakkesharv/spotify-top-10000-streamed-songs"

def main() -> None:
    df = pd.read_csv(SRC)
    df["Total Streams"] = pd.to_numeric(df["Total Streams"], errors="coerce").fillna(0).astype("int64")
    df["Position"] = pd.to_numeric(df["Position"], errors="coerce")
    df = df.dropna(subset=["Artist Name", "Song Name"])
    df["Artist Name"] = df["Artist Name"].astype(str).str.strip()
    df["Song Name"] = df["Song Name"].astype(str).str.strip()
    df = (df.sort_values(["Total Streams", "Position"], ascending=[False, True])
            .drop_duplicates(["Artist Name", "Song Name"], keep="first")
            .head(10000).copy())
    out = pd.DataFrame({
        "rank": range(1, len(df) + 1),
        "title": df["Song Name"],
        "main_artist": df["Artist Name"],
        "metric_name": "spotify_chart_streams_snapshot",
        "metric_value": df["Total Streams"],
        "metric_unit": "streams",
        "listen_count": df["Total Streams"],
        "listen_source": "Spotify chart-history dataset",
        "source_url": SOURCE_URL,
        "retrieved_at": "2026-07-20",
        "source_notes": "Historical Spotify chart-history stream aggregate snapshot published circa 2023; this is not a lifetime Spotify counter and not a live 2026 total.",
        "snapshot_position": df["Position"].astype("Int64"),
        "days": df["Days"],
        "peak_streams": df["Peak Streams"],
    })
    DST.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(DST, index=False, quoting=csv.QUOTE_MINIMAL)
    print(f"wrote {len(out):,} rows -> {DST}")

if __name__ == "__main__":
    main()

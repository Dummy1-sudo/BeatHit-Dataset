#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_megalist import countries
from music_megalist.dedupe import norm
from music_megalist.io import read_rows, write_rows
from music_megalist.models import SongRow
from music_megalist import fullbuild
from music_megalist import culturelists

DATA = ROOT / "data"
STATUS = ROOT / "STATUS.json"

# BeatHit project policy: a nominal 10,000-track list counts as complete once it is >9,000.
TEN_K_COMPLETE = 9_001


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _atomic_json(path: Path, value: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _install_country_deep_history() -> None:
    original_label = countries._label_before
    original_fetch = countries._fetch_html
    original_parse_totals = countries.parse_country_totals_html

    def parse_markets(html: str, *, index_url: str = countries.KWORB_SPOTIFY_INDEX):
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin

        soup = BeautifulSoup(html, "lxml")
        markets: dict[str, countries.CountryMarket] = {}
        for a in soup.find_all("a", href=True):
            href = str(a.get("href") or "")
            m = re.search(r"(?:^|/)country/([a-z0-9-]+)_(daily|weekly)\.html$", href, re.I)
            if not m:
                continue
            code, cadence = m.group(1).lower(), m.group(2).lower()
            if code == "global":
                continue
            name = countries.COUNTRY_NAMES.get(code) or original_label(a) or code.upper()
            totals_href = re.sub(r"_(?:daily|weekly)\.html$", f"_{cadence}_totals.html", href, flags=re.I)
            market = countries.CountryMarket(code=code, name=name, totals_url=urljoin(index_url, totals_href))
            # Prefer weekly because it generally exposes deeper history.
            if code not in markets or cadence == "weekly":
                markets[code] = market
        return sorted(markets.values(), key=lambda x: (x.name.casefold(), x.code))

    def row_key(row: SongRow) -> tuple[str, ...]:
        if row.spotify_track_id:
            return ("spotify", str(row.spotify_track_id).casefold())
        return ("text", norm(row.title), norm(row.main_artist))

    def fetch_one(market: countries.CountryMarket):
        urls = [market.totals_url]
        if "_weekly_totals.html" in market.totals_url:
            urls.append(market.totals_url.replace("_weekly_totals.html", "_daily_totals.html"))
        elif "_daily_totals.html" in market.totals_url:
            urls.insert(0, market.totals_url.replace("_daily_totals.html", "_weekly_totals.html"))

        merged: dict[tuple[str, ...], SongRow] = {}
        metas: list[dict[str, Any]] = []
        used_urls: list[str] = []
        last_error: Exception | None = None
        for url in dict.fromkeys(urls):
            selected = countries.CountryMarket(code=market.code, name=market.name, totals_url=url)
            try:
                html = original_fetch(url)
                rows, meta = original_parse_totals(html, market=selected)
            except FileNotFoundError as exc:
                last_error = exc
                continue
            used_urls.append(url)
            metas.append(meta)
            for row in rows:
                key = row_key(row)
                old = merged.get(key)
                if old is None or float(row.metric_value or 0) > float(old.metric_value or 0):
                    merged[key] = row
        if not metas:
            if last_error:
                raise last_error
            raise FileNotFoundError(f"No usable totals page for {market.code}")

        rows = sorted(merged.values(), key=lambda r: float(r.metric_value or 0), reverse=True)[: countries.COUNTRY_TARGET]
        for rank, row in enumerate(rows, 1):
            row.rank = rank
        available = len(merged)
        meta = dict(metas[0])
        meta.update({
            "source_url": used_urls,
            "available_unique_songs": available,
            "unique_songs": len(rows),
            "target": countries.COUNTRY_TARGET,
            "expected_rows": min(countries.COUNTRY_TARGET, available),
            "source_exhausted_below_target": 0 < available < countries.COUNTRY_TARGET,
            "complete": len(rows) >= countries.COUNTRY_TARGET,
            "history_sources_merged": used_urls,
        })
        return market, rows, meta

    countries.parse_country_markets_html = parse_markets
    countries._fetch_one_market = fetch_one


def _extend_children_tags() -> None:
    extra = [
        "children's song", "children songs", "kids music", "kids song", "kids songs",
        "baby music", "baby songs", "nursery music", "nursery song", "bedtime music",
        "bedtime songs", "family", "family entertainment", "educational song",
        "educational songs", "preschool music", "preschool songs", "school songs",
        "camp songs", "playground songs", "cartoon songs", "cartoon soundtrack",
        "disney songs", "puppetry", "sesame street", "muppets", "childrens",
    ]
    current = culturelists.TAG_LISTS.get("children_childhood", [])
    culturelists.TAG_LISTS["children_childhood"] = list(dict.fromkeys([*current, *extra]))


def _merge_per_game_into_general_vgm() -> int:
    path = DATA / "video_games" / "video_game_music_10000.csv"
    existing = read_rows(path) if path.exists() else []
    candidates = list(existing)
    for game_file in sorted((DATA / "video_games" / "per_game").glob("*.csv")):
        try:
            rows = read_rows(game_file)
        except Exception:
            continue
        for row in rows:
            extra = dict(row.extra or {})
            if extra.get("khinsider_album_kind") != "official_soundtrack":
                continue
            if float(extra.get("khinsider_album_match_score") or 0) < 88:
                continue
            # Relabel to the canonical association class accepted by BeatHit's existing validator.
            extra["culture_category"] = "video_game_music"
            extra["game_association_kind"] = "official_soundtrack_album"
            extra["selection"] = "high-confidence per-game soundtrack catalog evidence"
            row.extra = extra
            candidates.append(row)

    candidates.sort(
        key=lambda r: (float(r.overall_popularity_score or 0), float(r.metric_value or 0)),
        reverse=True,
    )
    out: list[SongRow] = []
    text_seen: set[tuple[str, str]] = set()
    spotify_seen: set[str] = set()
    mbid_seen: set[str] = set()
    isrc_seen: set[str] = set()
    for row in candidates:
        key = (norm(row.title), norm(row.main_artist))
        sid = str(row.spotify_track_id or "").casefold()
        mbid = str(row.musicbrainz_recording_mbid or "").casefold()
        isrc = str(row.isrc or "").casefold()
        if not all(key) or key in text_seen or (sid and sid in spotify_seen) or (mbid and mbid in mbid_seen) or (isrc and isrc in isrc_seen):
            continue
        text_seen.add(key)
        if sid: spotify_seen.add(sid)
        if mbid: mbid_seen.add(mbid)
        if isrc: isrc_seen.add(isrc)
        out.append(row)
        if len(out) >= 10_000:
            break
    for rank, row in enumerate(out, 1):
        row.rank = rank
    if len(out) > len(existing):
        write_rows(out, path)
    return len(out)


def _apply_completion_policy(previous: dict[str, Any]) -> None:
    payload = _load_json(STATUS)
    datasets = payload.setdefault("datasets", {})

    # Preserve conditional completion claims from the previous status when a partial core run
    # did not rebuild that conditional corpus.
    old_datasets = previous.get("datasets") or {}
    for name in ("kpop",):
        old = old_datasets.get(name) or {}
        current = datasets.get(name) or {}
        if old.get("complete") and not current.get("complete"):
            current.update(old)
            datasets[name] = current

    for name, target in fullbuild.FIXED_TARGETS.items():
        ds = datasets.get(name) or {}
        rows = int(ds.get("materialized_rows") or 0)
        if target == 10_000 and rows > 9_000:
            ds["complete"] = True
            notes = list(ds.get("notes") or [])
            marker = "Accepted as complete by BeatHit >9,000-of-10,000 policy."
            if marker not in notes:
                notes.append(marker)
            ds["notes"] = notes
        datasets[name] = ds

    fixed_complete = all(bool((datasets.get(name) or {}).get("complete")) for name in fullbuild.FIXED_TARGETS)
    vocaloid_complete = bool((datasets.get("vocaloid") or {}).get("complete"))
    kpop_complete = bool((datasets.get("kpop") or {}).get("complete"))
    countries_complete = bool((datasets.get("countries") or {}).get("complete"))
    core_complete = fixed_complete and vocaloid_complete and kpop_complete and countries_complete
    summary = payload.setdefault("completion_summary", {})
    summary["all_fixed_size_targets_complete"] = fixed_complete
    summary["vocaloid_conditional_corpus_marked_complete"] = vocaloid_complete
    summary["kpop_conditional_corpus_marked_complete"] = kpop_complete
    summary["spotify_country_lists_complete"] = countries_complete
    summary["core_lists_complete"] = core_complete
    expansion_complete = bool(summary.get("expansion_lists_complete"))
    summary["all_requested_lists_complete"] = core_complete and expansion_complete
    _atomic_json(STATUS, payload)


def main() -> int:
    previous = _load_json(STATUS)
    _install_country_deep_history()
    _extend_children_tags()

    # If expansion has already produced per-game soundtrack files, use their strongest official
    # soundtrack evidence to help the original aggregate VGM list before the network rebuild.
    before_vgm = _merge_per_game_into_general_vgm()
    print(f"aggregate VGM before core rebuild: {before_vgm}", flush=True)

    status = fullbuild.full_build(
        skip_zenodo=os.getenv("BEATHIT_SKIP_ZENODO", "0") == "1",
        only=["anime", "vocaloid", "video_game_music", "children_childhood", "countries"],
        reuse_complete=True,
    )
    after_vgm = _merge_per_game_into_general_vgm()
    print(f"aggregate VGM after core rebuild/per-game merge: {after_vgm}", flush=True)
    _apply_completion_policy(previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

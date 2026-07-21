from __future__ import annotations

"""Country-level Spotify chart lists.

The country lists are built from Kworb's aggregates of Spotify's regional daily
Top 200 charts.  Each per-country list ranks unique songs by the exact sum of
streams observed while the recording was inside that country's Spotify daily
chart.  This is deliberately *not* labeled as a lifetime Spotify stream count:
streams earned while a song was outside the Top 200 are not present in the
source aggregate.
"""

import copy
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, NavigableString

from .dedupe import dedupe, norm
from .io import read_rows, write_rows
from .models import SongRow

KWORB_SPOTIFY_INDEX = "https://kworb.net/spotify/"
COUNTRY_TARGET = 1_000
TODAY = date.today().isoformat()


@dataclass(frozen=True)
class CountryMarket:
    code: str
    name: str
    totals_url: str


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    text = re.sub(r"[^0-9-]", "", str(value))
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _label_before(anchor: Any) -> str:
    """Recover the country label immediately preceding a ``Daily`` link."""
    parts: list[str] = []
    sib = anchor.previous_sibling
    while sib is not None:
        if getattr(sib, "name", None) == "br":
            break
        if isinstance(sib, NavigableString):
            text = str(sib).strip()
        else:
            text = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else ""
        if text:
            parts.append(text)
        sib = sib.previous_sibling
    label = " ".join(reversed(parts)).strip()
    # The index sometimes leaves separators from a previous link in the same text node.
    label = re.sub(r"^.*?\|\s*", "", label).strip(" ()|\t\r\n")
    return label


def parse_country_markets_html(html: str, *, index_url: str = KWORB_SPOTIFY_INDEX) -> list[CountryMarket]:
    """Discover every country/territory for which Kworb exposes Spotify daily totals."""
    soup = BeautifulSoup(html, "lxml")
    markets: dict[str, CountryMarket] = {}
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        m = re.search(r"(?:^|/)country/([a-z0-9-]+)_daily\.html$", href, re.I)
        if not m:
            continue
        code = m.group(1).lower()
        if code == "global":
            continue
        name = _label_before(a) or code.upper()
        totals_href = re.sub(r"_daily\.html$", "_daily_totals.html", href, flags=re.I)
        markets[code] = CountryMarket(code=code, name=name, totals_url=urljoin(index_url, totals_href))
    return sorted(markets.values(), key=lambda m: (m.name.casefold(), m.code))


def _coverage_dates(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Covers charts from\s+(\d{4}/\d{2}/\d{2})\s+to\s+(\d{4}/\d{2}/\d{2})", text, re.I)
    if not m:
        return None, None
    return m.group(1).replace("/", "-"), m.group(2).replace("/", "-")


def parse_country_totals_html(
    html: str,
    *,
    market: CountryMarket,
    limit: int = COUNTRY_TARGET,
    retrieved_at: str = TODAY,
) -> tuple[list[SongRow], dict[str, Any]]:
    """Parse one Kworb country totals page into a unique ranked song list."""
    soup = BeautifulSoup(html, "lxml")
    coverage_start, coverage_end = _coverage_dates(soup)
    raw_rows: list[SongRow] = []

    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        credit = tds[0]
        track_link = next(
            (a for a in credit.find_all("a", href=True) if re.search(r"(?:^|/)(?:spotify/)?track/[A-Za-z0-9]+\.html$", str(a.get("href")))),
            None,
        )
        if track_link is None:
            continue
        artist_link = next(
            (a for a in credit.find_all("a", href=True) if re.search(r"(?:^|/)(?:spotify/)?artist/", str(a.get("href")))),
            None,
        )
        title = track_link.get_text(" ", strip=True)
        artist = artist_link.get_text(" ", strip=True) if artist_link is not None else ""
        if not title or not artist:
            # Conservative fallback for table variants where only combined credit text is present.
            combined = credit.get_text(" ", strip=True)
            if " - " in combined:
                artist, title = combined.split(" - ", 1)
        artist, title = artist.strip(), title.strip()
        if not artist or not title:
            continue

        href = str(track_link.get("href") or "")
        tid_match = re.search(r"(?:^|/)(?:spotify/)?track/([A-Za-z0-9]+)\.html$", href)
        track_id = tid_match.group(1) if tid_match else None

        total_streams = _safe_int(tds[-1].get_text(" ", strip=True))
        if total_streams is None:
            continue
        peak_streams = _safe_int(tds[-2].get_text(" ", strip=True))
        days = _safe_int(tds[1].get_text(" ", strip=True)) if len(tds) > 1 else None
        top10_days = _safe_int(tds[2].get_text(" ", strip=True)) if len(tds) > 2 else None
        peak_text = " ".join(td.get_text(" ", strip=True) for td in tds[3:-2]) if len(tds) > 5 else (tds[3].get_text(" ", strip=True) if len(tds) > 3 else "")
        peak_match = re.search(r"\b(\d{1,3})\b", peak_text)
        peak_rank = int(peak_match.group(1)) if peak_match else None
        occ_match = re.search(r"x\s*(\d+)", peak_text, re.I)
        peak_occurrences = int(occ_match.group(1)) if occ_match else None

        source_note = (
            f"Exact aggregate of streams observed while this recording was inside Spotify's daily Top 200 "
            f"for {market.name}, as aggregated by Kworb from Spotify chart data. "
            f"Streams earned outside the chart are not included."
        )
        if coverage_start and coverage_end:
            source_note += f" Chart coverage on the source page: {coverage_start} through {coverage_end}."

        raw_rows.append(
            SongRow(
                title=title,
                main_artist=artist,
                metric_name="spotify_country_chart_streams",
                metric_value=float(total_streams),
                metric_unit="streams",
                listen_count=total_streams,
                listen_source="Spotify regional daily chart (Kworb aggregate)",
                spotify_track_id=track_id,
                source_url=market.totals_url,
                retrieved_at=retrieved_at,
                source_notes=source_note,
                extra={
                    "country_code": market.code.upper(),
                    "country_name": market.name,
                    "chart_coverage_start": coverage_start,
                    "chart_coverage_end": coverage_end,
                    "days_on_chart": days,
                    "top10_days": top10_days,
                    "peak_rank": peak_rank,
                    "peak_occurrences": peak_occurrences,
                    "peak_daily_streams": peak_streams,
                    "metric_scope": "country-specific Spotify daily Top 200 chart-attributed streams",
                },
            )
        )

    # A song can have multiple Spotify recording IDs/reissues. Keep one unique song entry per
    # country conservatively (the strongest observed chart total), rather than summing editions.
    unique = dedupe(raw_rows)
    unique.sort(key=lambda r: (r.metric_value, r.listen_count or 0), reverse=True)
    unique = unique[:limit]
    for i, row in enumerate(unique, 1):
        row.rank = i
        row.extra["country_rank"] = i

    meta = {
        "country_code": market.code.upper(),
        "country_name": market.name,
        "source_url": market.totals_url,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "raw_chart_recordings": len(raw_rows),
        "unique_songs": len(unique),
        "target": limit,
        "complete": len(unique) == limit,
    }
    return unique, meta


def _fetch_html(url: str, *, timeout: int = 120) -> str:
    headers = {"User-Agent": "BeatHit-Dataset/1.0 (+https://github.com/Dummy1-sudo/BeatHit-Dataset)"}
    last: Exception | None = None
    for attempt in range(5):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
                r = client.get(url)
                r.raise_for_status()
                return r.text
        except Exception as exc:  # pragma: no cover - network path
            last = exc
            time.sleep(min(20, 2 ** attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def _fetch_one_market(market: CountryMarket) -> tuple[CountryMarket, list[SongRow], dict[str, Any]]:
    html = _fetch_html(market.totals_url)
    rows, meta = parse_country_totals_html(html, market=market)
    return market, rows, meta


def build_country_lists(status: Any, *, data_dir: Path) -> list[SongRow]:
    """Build one top-1000 CSV per detected Spotify regional country chart."""
    out_dir = data_dir / "countries"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_html = _fetch_html(KWORB_SPOTIFY_INDEX)
    markets = parse_country_markets_html(index_html)
    if not markets:
        raise RuntimeError("No Spotify country markets discovered from Kworb index")
    # Remove stale generated market files so a removed/renamed source market cannot survive
    # silently from an older build. Documentation and .gitkeep are preserved.
    for old in out_dir.glob("*_top1000.csv"):
        old.unlink()

    workers = max(1, min(int(os.getenv("BEATHIT_COUNTRY_WORKERS", "4")), 8))
    all_rows: list[SongRow] = []
    market_meta: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one_market, market): market for market in markets}
        for future in as_completed(futures):
            market = futures[future]
            try:
                _, rows, meta = future.result()
                filename = f"{market.code}_{re.sub(r'[^a-z0-9]+', '_', market.name.casefold()).strip('_')}_top1000.csv"
                write_rows(rows, out_dir / filename)
                meta["file"] = filename
                market_meta.append(meta)
                all_rows.extend(rows)
            except Exception as exc:  # pragma: no cover - network path
                failures.append({"country_code": market.code.upper(), "country_name": market.name, "error": str(exc)})

    market_meta.sort(key=lambda x: (str(x.get("country_name", "")).casefold(), str(x.get("country_code", ""))))
    complete_markets = sum(1 for m in market_meta if m.get("complete"))
    index_payload = {
        "schema_version": 1,
        "retrieved_at": TODAY,
        "source_index_url": KWORB_SPOTIFY_INDEX,
        "metric_name": "spotify_country_chart_streams",
        "metric_definition": "Sum of Spotify daily Top 200 streams observed in that country while a song was charting; streams outside the Top 200 are excluded.",
        "ranking": "Descending by country-specific chart-attributed stream total after conservative song-level deduplication.",
        "target_per_country": COUNTRY_TARGET,
        "detected_country_markets": len(markets),
        "successfully_built_markets": len(market_meta),
        "complete_markets": complete_markets,
        "total_materialized_rows": len(all_rows),
        "markets": market_meta,
        "failures": failures,
    }
    (out_dir / "index.json").write_text(json.dumps(index_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if hasattr(status, "sources"):
        status.sources["spotify_country_charts_kworb"] = {
            "url": KWORB_SPOTIFY_INDEX,
            "retrieved_at": TODAY,
            "detected_markets": len(markets),
            "built_markets": len(market_meta),
            "complete_markets": complete_markets,
            "rows": len(all_rows),
            "ok": not failures and complete_markets == len(markets),
            "failures": failures,
        }
    if failures and hasattr(status, "warnings"):
        status.warnings.append(f"Spotify country charts: {len(failures)} market fetch/build failures; see data/countries/index.json")

    return all_rows


def load_existing_country_rows(data_dir: Path) -> list[SongRow]:
    out: list[SongRow] = []
    folder = data_dir / "countries"
    if not folder.exists():
        return out
    for p in sorted(folder.glob("*_top1000.csv")):
        out.extend(read_rows(p))
    return out


def country_completion(data_dir: Path) -> tuple[bool, int, int, int, list[str]]:
    """Return complete, rows, detected markets, complete markets, notes."""
    idx = data_dir / "countries" / "index.json"
    if not idx.exists():
        return False, 0, 0, 0, ["Country index has not been materialized yet."]
    try:
        payload = json.loads(idx.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, 0, 0, 0, [f"Country index unreadable: {exc}"]
    detected = int(payload.get("detected_country_markets") or 0)
    complete_markets = int(payload.get("complete_markets") or 0)
    rows = int(payload.get("total_materialized_rows") or 0)
    failures = payload.get("failures") or []
    complete = detected > 0 and complete_markets == detected and not failures and rows == detected * COUNTRY_TARGET
    notes = [
        f"Detected {detected} country/territory Spotify chart markets from the source index; {complete_markets} have exactly {COUNTRY_TARGET} ranked unique songs.",
        "Country chart counts are exact chart-attributed streams, not lifetime Spotify totals; streams outside the daily Top 200 are excluded.",
    ]
    return complete, rows, detected, complete_markets, notes


def collapse_country_rows_for_megalist(rows: Iterable[SongRow]) -> list[SongRow]:
    """Collapse country-specific rows into one song row while preserving all market evidence.

    Country stream totals are disjoint by market, so summing one deduplicated observation per
    country yields a defensible *regional-chart-attributed* aggregate. It remains explicitly
    labeled as chart coverage, not a lifetime Spotify counter.
    """
    groups: dict[tuple[str, str], list[SongRow]] = {}
    for row in rows:
        key = (norm(row.title), norm(row.main_artist))
        if not all(key):
            continue
        groups.setdefault(key, []).append(row)

    out: list[SongRow] = []
    for group in groups.values():
        by_country: dict[str, SongRow] = {}
        for row in group:
            code = str((row.extra or {}).get("country_code") or "").upper()
            if not code:
                continue
            prev = by_country.get(code)
            if prev is None or row.metric_value > prev.metric_value:
                by_country[code] = row
        if not by_country:
            continue
        members = list(by_country.values())
        preferred = max(members, key=lambda r: (r.metric_value, r.listen_count or 0))
        aggregate = copy.deepcopy(preferred)
        total = sum(int(r.listen_count or r.metric_value or 0) for r in members)
        appearances = []
        for r in sorted(members, key=lambda x: (int((x.extra or {}).get("country_rank") or 10**9), str((x.extra or {}).get("country_code") or ""))):
            e = r.extra or {}
            appearances.append({
                "country_code": e.get("country_code"),
                "country_name": e.get("country_name"),
                "country_rank": e.get("country_rank"),
                "chart_streams": int(r.listen_count or r.metric_value or 0),
                "coverage_start": e.get("chart_coverage_start"),
                "coverage_end": e.get("chart_coverage_end"),
            })
        aggregate.metric_name = "spotify_regional_chart_streams_sum"
        aggregate.metric_value = float(total)
        aggregate.metric_unit = "streams"
        aggregate.listen_count = total
        aggregate.listen_source = "Spotify regional daily charts (Kworb aggregate; summed distinct country markets)"
        aggregate.source_url = KWORB_SPOTIFY_INDEX
        aggregate.source_notes = (
            "Sum of one deduplicated country-chart stream total per Spotify regional market in which the song appears. "
            "Markets are disjoint, but each market count only includes streams while the song was inside that market's daily Top 200; "
            "this is not a lifetime Spotify stream total."
        )
        aggregate.extra = dict(aggregate.extra or {})
        aggregate.extra["country_chart_market_count"] = len(appearances)
        aggregate.extra["country_chart_appearances"] = appearances
        aggregate.extra["metric_scope"] = "sum of distinct country-specific Spotify daily Top 200 chart-attributed stream totals"
        out.append(aggregate)

    out.sort(key=lambda r: (r.metric_value, r.listen_count or 0), reverse=True)
    for i, row in enumerate(out, 1):
        row.rank = i
    return out

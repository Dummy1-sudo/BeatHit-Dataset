# BeatHit Dataset

A reproducible, source-backed collection of large ranked music lists. The repository is designed around one rule: **missing data is reported, never invented**.

## Requested datasets

| Dataset | Requested coverage |
|---|---:|
| Popular anime + most recognizable theme song | 10,000 anime |
| Vocaloid / voice-synth songs | every verified song with >=10,000,000 Spotify streams, capped at 10,000 |
| Worldwide songs by era | 51,000 total |
| Classical | 10,000 |
| VTuber original songs | up to 10,000 verified entries |
| Emerging/promising artists | 10,000 songs |
| Genre-diverse | 10,000 songs across >=50 genres |
| Movie/TV-associated songs | 10,000 |
| VTuber non-original / cover songs | up to 10,000 verified entries |
| Spotify country charts | top 1,000 unique songs for every country/territory with a detected Spotify regional daily chart |
| Megalist | deduplicated union of every list, including all country lists |

Every song row includes `title` and `main_artist`. When a source exposes an actual count, it is also copied into the explicit `listen_count` or `view_count` field. Scores are never mislabeled as listens.

## Important accuracy distinction

No public source exposes a perfectly current, complete worldwide listen counter for every recording ever released. This repository therefore preserves the exact metric and provenance used for each row:

- `spotify_streams` → cumulative Spotify stream count from the cited snapshot/page
- `spotify_streams_snapshot` → cumulative Spotify stream count from a dated snapshot
- `spotify_chart_streams_snapshot` → chart-history aggregate from a dated chart dataset, **not** a lifetime counter and never used for the Vocaloid >=10M qualification
- `spotify_country_chart_streams` → exact country-specific sum of Spotify daily Top 200 streams observed while the song was charting; streams outside the chart are excluded
- `spotify_regional_chart_streams_sum` → megalist-only sum of the distinct country-chart totals for the same song; still a chart-attributed aggregate, not a lifetime Spotify counter
- `youtube_views` → actual YouTube view count
- `spotify_popularity` → Spotify 0–100 popularity score, **not** a listen count
- `anilist_users` / `anime_popularity_proxy` → anime popularity evidence, **not** song listens
- rank-based fallback metrics → explicitly labeled rank scores

`listen_count` is left blank instead of estimating a fake number when a trustworthy count is unavailable.

## Highest-accuracy build

The repository contains a full builder that combines:

- a ~0.9M-track Spotify-derived public snapshot from Zenodo;
- a 600k+ historical Spotify metadata dataset for dense decade coverage;
- a 114k / 125-genre Spotify fallback corpus;
- a live Kworb cumulative Spotify-stream overlay for globally prominent tracks, with exact normalized title+artist matching;
- Kworb aggregates of Spotify regional daily charts to discover every available country/territory chart and build one all-time top-1,000 list per market;
- AniList current popularity (stable live window) + AnimeThemes theme metadata, with a bundled MyAnimeList popularity/theme snapshot and paced/cached Jikan theme fallback when live AniList or theme joins are unavailable;
- VocaDB for voice-synth classification, using a rate-paced high-popularity candidate scan plus explicit voice-synth credit/genre markers so the API is not abused by a hundreds-of-thousands-row crawl;
- Holodex for VTuber `Original_Song` and `Music_Cover` classification, with HoloStats as a Hololive-only fallback/augmentation;
- YouTube Data API when available; otherwise a clearly attributed Return YouTube Dislike cached `viewCount` fallback, then optional capped `yt-dlp` as a last resort;
- ListenBrainz as a classical/popularity fallback;
- the bundled historical 10,000-song Spotify chart-history snapshot as independent popularity evidence; its `Total Streams` field is explicitly treated as a chart-window aggregate rather than a lifetime counter.

### Run locally

```bash
python -m pip install -e . -r requirements-full.txt
python scripts/build_full_dataset.py
python -m music_megalist validate
python scripts/verify_targets.py
```

The full source download can exceed 1 GB. Set `BEATHIT_SKIP_ZENODO=1` only when you accept lower coverage and lower stream-count accuracy. `BEATHIT_VOCADB_SCAN_LIMIT` defaults to 25,000 high-rated VocaDB candidates to respect the public API usage guidance; increasing it can improve recall but does not magically make Spotify/VocaDB coverage exhaustive.

## Automatic completion after manual GitHub push

The supplied `.github/workflows/full-build.yml` is triggered by the initial push of the builder files to `main`. It downloads the public sources, builds all lists, validates them, writes coverage/provenance reports, and commits generated datasets back to the repository.

**Important:** the ZIP itself contains the reproducible builder plus bootstrap evidence, not fabricated quota-filled final CSVs. The first network-enabled full-build run is what materializes the large final category files. Check `STATUS.json`, `data/coverage_report.csv`, and `data/target_report.json` after that run before treating a category as complete.

For the strongest VTuber ranking accuracy, add these optional GitHub repository secrets before running the workflow:

- `HOLODEX_API_KEY` — Holodex documents API-key authentication for reliable access.
- `YOUTUBE_API_KEY` — enables batched exact video-view retrieval. Without it, the workflow first attempts the Return YouTube Dislike public cached `viewCount` field and can optionally use capped `yt-dlp`. Unresolved rows stay clearly marked with a source-rank/subscriber proxy rather than fake views.

The workflow never pads a finite or conditional corpus. If fewer than 10,000 verified VTuber originals exist in the retrieved Holodex corpus, the output contains the verified corpus and `coverage_report.csv` records the shortfall.

## Spotify country top-1,000 lists

`data/countries/` contains one CSV per country/territory discovered from the Spotify regional chart index exposed by Kworb. The market set is discovered dynamically at build time instead of being hard-coded, so newly added or removed chart markets are reflected in `data/countries/index.json`.

Each country list targets the **top 1,000 unique songs by cumulative streams recorded while those songs were inside that country's Spotify daily Top 200 chart**, ranked from most to least. When a market's entire available historical source contains fewer than 1,000 unique songs, the exhaustive shorter list is kept as-is rather than padded and that requested market remains incomplete. This matches the project's earlier “overall popularity” preference better than taking only today's Top 200.

Important metric boundary: country totals do **not** include streams earned while a song was outside the daily chart. The files therefore use `spotify_country_chart_streams`, not `spotify_streams`. Each row also preserves country code/name, chart coverage dates, days on chart, Top-10 days, peak rank, peak daily streams, Spotify track ID when available, source URL, and retrieval date.

The country lists are all included in the final megalist. Before deduplication, repeated appearances of the same song across countries are collapsed into one evidence record with `extra.country_chart_appearances`. Its country totals are summed only across distinct country markets and labeled `spotify_regional_chart_streams_sum`; this aggregate is never presented as a lifetime Spotify counter.

Completion requires **1,000 unique songs for every country/territory advertised by the regional chart index**. Source-exhausted markets with fewer songs and stale index links with no historical totals page are recorded explicitly but keep `countries.complete=false`. Genuine fetch failures also remain incomplete. The builder never pads a shortfall.

## Worldwide 51,000 allocation

`data/worldwide/worldwide_51000.csv` contains exactly these requested bucket sizes when source coverage permits, and the builder also writes one independently ranked CSV per bucket (`worldwide_current.csv`, `worldwide_2020s.csv`, etc.):

- 10,000 current/global-popularity selections
- 10,000 from the 2020s
- 10,000 from the 2010s
- 10,000 from the 2000s
- 5,000 from the 1990s
- 3,000 from the 1980s
- 2,000 from the 1970s
- 1,000 from the 1960s

The current bucket emphasizes fresher popularity signals. Historical buckets prioritize cumulative stream evidence and then transparent popularity scores when a count is unavailable. Every combined-row `extra` field preserves both `era_bucket` and `era_rank`. A song may legitimately appear once in the current bucket and once in its release-era bucket; the final megalist removes cross-list duplicates.


## Completion semantics

A large output file existing is **not** enough to claim completion. `STATUS.json` is authoritative:

- fixed-size categories are complete only when the exact requested row count is materialized;
- Vocaloid is complete only when the qualifying source corpus is genuinely exhaustive enough to support the claim; it is never padded below 10M streams;
- VTuber lists are never padded with non-VTuber/unverified tracks merely to reach 10,000;
- every Spotify country/territory advertised by the source index must reach the requested 1,000 unique rows; source-exhausted short markets or unavailable historical totals stay explicitly incomplete rather than being padded;
- `megalist.complete` is true only when all upstream requested lists, including the country lists, are complete;
- `python scripts/verify_targets.py` checks era allocation, >=50 genre diversity, Vocaloid threshold validity, VTuber original/cover flags, per-country source-backed completeness, and megalist deduplication in addition to row counts.

The repository intentionally prefers an honest shortfall over a fabricated “100% complete” status.

## Generated reports

- `STATUS.json` — exact materialized row counts and completeness state
- `data/BUILD_REPORT.json` — source acquisition results, warnings, timestamps, per-dataset coverage
- `data/coverage_report.csv` — compact coverage table
- `data/target_report.json` — requested-vs-built counts
- `data/MANIFEST.json` — SHA-256 checksums of committed data files

## Repository layout

```text
data/
  anime/
  vocaloid/
  worldwide/
  classical/
  vtuber_original/
  vtuber_non_original/
  emerging/
  genres/
  screen_soundtracks/
  countries/              # one *_top1000.csv per detected Spotify country/territory + index.json
  megalist/
  raw/                    # bundled redistributable seed/snapshot data only
src/music_megalist/
  fullbuild.py            # complete high-accuracy build pipeline
scripts/
.github/workflows/
```

## Licensing

Repository code is MIT. Third-party datasets and APIs retain their own licenses/terms. Large raw third-party downloads used during the full build are git-ignored and are not silently relicensed or redistributed. Generated rows retain a `source_url` and source notes so provenance remains inspectable.

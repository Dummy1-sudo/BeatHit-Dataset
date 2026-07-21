# Methodology

## Core rules

1. No fabricated songs.
2. No fabricated listen/view counts.
3. Every row must have a song title and main artist.
4. Counts and scores are never conflated.
5. Prefer stable IDs for deduplication; fall back to Unicode-normalized title + artist matching.
6. Fixed-size lists target the requested count, but source exhaustion is reported rather than padded.
7. Conditional lists contain only qualifying records, even when that means fewer than 10,000 rows.

## Shared schema

Important fields:

- `rank`
- `title`
- `main_artist`
- `featured_artists`
- `album`
- `release_date`, `release_year`
- `genres`
- `composer` when explicitly known
- category-specific metadata (`anime_title`, `anime_popularity`, `screen_work`, `vtuber`, `is_original`)
- `metric_name`, `metric_value`, `metric_unit`
- `listen_count`, `listen_source` only for actual stream/listen counters
- `view_count` only for actual view counters
- `overall_popularity_score` as an internal normalized comparison score
- stable IDs where available
- `source_url`, `retrieved_at`, `source_notes`, `extra`

## “Overall popularity”

There is no universal public number combining Spotify, YouTube, historical sales, radio, TikTok, and every regional service. The builder therefore ranks using a composite only as an ordering aid, while preserving raw evidence.

Approximate catalog candidate score (0–100 ordering aid):

- cumulative/available stream evidence: up to 30 points
- Spotify popularity: up to 25 points
- cross-platform Track Score: up to 20 points
- YouTube views: up to 15 points
- current daily Spotify streams: up to 10 points

Missing components contribute zero rather than being guessed. The raw metric shown to users remains the strongest available observation.

## Worldwide 51,000

Buckets are selected independently with within-bucket deduplication:

1. current/global 10,000 — emphasizes current Spotify popularity/cross-platform score and refreshed Kworb stream counts;
2. 2020s 10,000;
3. 2010s 10,000;
4. 2000s 10,000;
5. 1990s 5,000;
6. 1980s 3,000;
7. 1970s 2,000;
8. 1960s 1,000.

Historical buckets emphasize cumulative streams, then transparent popularity evidence where counts are absent. A song may appear in both `current` and its release-decade bucket; this is intentional for the exact 51,000 requested allocation. `extra.era_rank` preserves the within-bucket order and the final megalist removes cross-list duplicates.

## Anime 10,000

1. Fetch AniList anime in `POPULARITY_DESC` order.
2. Join to AnimeThemes via AniList/MAL external IDs.
3. Generate OP/ED candidates with song/artist credits.
4. Match candidates to the normalized music catalog.
5. Select the candidate with strongest song-level popularity evidence.
6. If no song-level evidence matches, choose OP1 before ED1/other sequence order and mark `anime_popularity_proxy` if only the anime popularity is available.
7. Never invent a theme for an anime with no verified theme metadata.

This intentionally separates **anime popularity** from **song listen count**.

## Vocaloid / voice-synth >=10M Spotify

1. Query a rate-paced, bounded high-popularity VocaDB candidate window (default 25,000 by RatingScore) rather than abusing the public API with a hundreds-of-thousands-row crawl.
2. Use multilingual names/aliases plus explicit voice-synth credit/genre markers for high-precision classification.
3. Match against the acquired Spotify-derived catalog.
4. Require **trusted cumulative** Spotify evidence >=10,000,000. The hard-threshold channel is the Zenodo Spotify-API-derived snapshot and/or a live Kworb overlay; historical chart-window totals and noisy cross-platform outliers cannot qualify a song by themselves.
5. Deduplicate by stable IDs and normalized title + artist.
6. Rank strictly by qualifying cumulative Spotify stream count.
7. Cap at 10,000; never pad.

Because neither Spotify nor VocaDB exposes a single public exhaustive live join, `BUILD_REPORT.json` records exact source coverage and `STATUS.json` only marks the conditional corpus complete when the coverage conditions justify it.

## Classical 10,000

Primary selection uses source genres/tags such as classical, baroque, romantic, opera, orchestral, chamber, concerto, symphony, choral, classical piano, renaissance, contemporary classical and related subgenres. Candidates are ranked by strongest available popularity evidence.

If the catalog has fewer than 10,000 candidates, ListenBrainz tag-radio results provide a source-backed fallback. `composer` is filled only when explicitly supplied by a source; the credited performer remains `main_artist` otherwise.

## VTuber original / non-original

- Original classification: Holodex `Original_Song` topic.
- Cover/non-original classification: Holodex `Music_Cover` topic.
- Main artist: a single identified VTuber mention when Holodex supplies one; otherwise the uploader/channel identity.
- Popularity: exact YouTube views when available; Spotify streams if an exact catalog match provides a stronger count; otherwise a clearly labeled source rank.
- Deduplication prevents repeated video/song entries.
- Finite verified corpora are not padded.

## Emerging/promising 10,000

This category is inherently subjective, so the selection is explicitly a heuristic rather than a factual label.

Signals:

- release in roughly the last 3–4 years;
- Spotify popularity and/or meaningful stream count;
- recency bonus;
- penalty for already massive artist follower counts;
- maximum three selected tracks per artist for breadth.

The computed `emerging_score` and heuristic description are stored in `extra`.

## 50+ genres / 10,000 songs

1. Parse source genre metadata.
2. Select at least 50 sufficiently deep music genres.
3. Initially target 200 unique high-popularity tracks per genre.
4. Redistribute shortfalls across the same genre pool without duplicates.
5. Store the genre used for selection in `extra.selection_genre`.

## Movie/TV 10,000

Primary selection uses source-declared soundtrack/score metadata: album names and genres containing terms such as soundtrack, original motion picture, original series, television, film score, original score, etc. A tiny curated, independently sourced association seed covers famous needle-drop/theme cases explicitly requested by the user that would otherwise be missed because the Spotify release itself is not a soundtrack album. The association seed never invents a popularity count; song metrics still come from the matched catalog.

## Megalist deduplication

Rows are joined when either a shared stable identifier (Spotify ID, MusicBrainz MBID, ISRC) or the same conservative Unicode-normalized `title + main_artist` key proves they represent the same song. Edition decorations such as remaster/deluxe labels are normalized while meaningful versions such as live/remix/acoustic remain distinct.

When duplicates conflict, direct cumulative Spotify counters are preferred over YouTube views, other listen counters, daily counters, and finally proxies/scores. Alternate metrics and merged source URLs are retained in `extra` rather than summed across incompatible platforms.

## Accuracy tiers

- **A** — exact stable ID and exact count from cited source.
- **B** — exact normalized title+artist match to a source count.
- **C** — verified category membership but only a platform score/rank, not a count.
- **Proxy** — clearly labeled non-song proxy (for example AniList anime popularity when no song counter is public).

The builder does not upgrade a lower tier by estimating a count.

## Spotify country top 1,000 per market

The country extension follows the user's earlier preference for **overall popularity**, not a single-day snapshot:

1. Discover country/territory markets dynamically from Kworb's Spotify chart index by locating regional daily-chart links. `global` is excluded because the request is one list per country/territory.
2. Fetch each market's `daily_totals` page, which aggregates Spotify daily chart history.
3. Extract stable Spotify track IDs where exposed by Kworb's track-history links, plus title, main artist, chart coverage, days on chart, Top-10 days, peak rank, peak daily streams, and cumulative chart-attributed streams.
4. Deduplicate conservatively at song level within each country. Multiple editions/reissues are not blindly summed; the strongest observed country-chart total is retained and alternate evidence stays as provenance.
5. Rank unique songs by `spotify_country_chart_streams` descending and keep exactly the top 1,000 when at least 1,000 source-backed songs exist.
6. Write one CSV per market and a machine-readable `data/countries/index.json` describing every detected market, source URL, coverage dates, row count, and any failure.
7. Mark the country dataset complete only if every detected market builds successfully with exactly 1,000 unique songs. No market is padded.

### Country metric interpretation

`spotify_country_chart_streams` is an exact count within a limited scope: Spotify streams observed while the recording was present in that country's daily Top 200. It is not a lifetime country stream total because out-of-chart streams are unavailable in the public chart source.

For megalist integration, repeated country appearances are collapsed before global deduplication. One observation per country is retained in `extra.country_chart_appearances`. Those distinct-market totals are summed as `spotify_regional_chart_streams_sum`; because the markets are distinct, the sum is meaningful chart-attributed Spotify evidence, but its limited chart scope remains explicit.

### Why not use only today's country chart?

Spotify's regional daily chart contains only 200 songs. The requested 1,000-song country list therefore cannot be produced from a single current chart without inventing ranks 201–1,000. Aggregating historical daily chart streams provides a reproducible all-time/overall ranking with enough depth while preserving an exact source metric.

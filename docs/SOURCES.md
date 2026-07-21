# Sources and provenance

The builder deliberately combines multiple sources because no single public database supplies complete, current, comparable popularity counts for every requested category.

## Spotify-derived catalog and counts

### Zenodo — “Almost a million Spotify tracks”

- Canonical record: `https://doi.org/10.5281/zenodo.11453410`
- Snapshot published: 2024-06-03
- Approximate size: 0.9 million tracks
- Important fields used: `track_id`, `name`, `track_artists`, `streams`, `genres`, `artist_followers`, `artist_popularity`, `album_release_date`, `popularity`, `album_name`
- File integrity in builder: MD5 `c32dffb8f9a62fec8c5892b464d7ea42`
- Purpose: broad stream-count/popularity/genre/release-date backbone.
- Limitation: snapshot data is not a live 2026 counter.

### Kworb — live Spotify cumulative-stream overlay

- `https://kworb.net/spotify/songs.html`
- Purpose: refresh exact cumulative Spotify stream counts for globally prominent tracks where exact title+artist matching is possible.
- Stored with retrieval date.
- Limitation: not a complete Spotify catalog and is not an official Spotify API.

### Spotify 600k historical dataset

- `https://www.kaggle.com/datasets/yamaerenay/spotify-dataset-19212020-600k-tracks`
- Purpose: dense 1921–2020 metadata/release-year coverage, especially older decades.
- Limitation: Spotify `popularity` is a score, not cumulative listens; count fields are not invented where absent.

### 114k / 125-genre fallback corpus

- `https://github.com/szewczakjj/Almost-Million-Songs-Dataset-2025-16-Features-`
- 114,000 rows; fields include track ID, artist, album, title, Spotify popularity and `track_genre`.
- Purpose: genre stratification and fallback metadata.

### Cross-platform 2024 snapshot

- `https://gist.github.com/cooneycw/b4021d5d872ee4a07239f0ea25c23cd7`
- Fields may include Spotify streams/popularity, YouTube views, TikTok views, all-time rank, and Track Score.
- Purpose: additional current-ish/cross-platform ranking evidence.

### Bundled 2023 10k Spotify chart-history snapshot

- Stored at `data/raw/spotify_top10000_streamed_songs_2023.csv`.
- Purpose: independent historical chart-popularity evidence and bootstrap data.
- Critical limitation: its `Total Streams` field is treated as the aggregate represented by that chart-history dataset, **not** as a lifetime Spotify counter. The builder labels it `spotify_chart_streams_snapshot`, marks it non-cumulative, and never uses it to qualify the Vocaloid >=10M rule.

## Anime

### AniList GraphQL

- `https://graphql.anilist.co`
- Popularity meaning used by the build: number of users with an anime on their list.
- Purpose: rank the 10,000 anime by current source popularity.

### AnimeThemes

- `https://api.animethemes.moe`
- `https://animethemes.moe`
- Purpose: opening/ending theme title and artist metadata, linked external IDs.
- Theme recognizability selection: strongest matched song metric when available; otherwise OP1, then ED1/sequence order as a transparent fallback.

### Jikan / MyAnimeList theme fallback

- API: `https://api.jikan.moe/v4`
- Purpose: fill theme-metadata gaps for popular AniList titles that have no AnimeThemes external-ID match.
- Requests are lazy, rate-paced and cached; only verified opening/ending strings are used.
- Jikan is read-only and unofficial with respect to MyAnimeList, so AnimeThemes remains the preferred source.

### MAL theme fallback snapshot

- `https://gist.github.com/Chepubelja/00ed0aae9bdd4d9be5f4fd1032d0d250`
- Used only when an AniList title cannot be joined to AnimeThemes and an older MAL theme string is available.
- The anime popularity itself still comes from the current AniList query.

## Vocaloid / voice-synth

### VocaDB

- API: `https://vocadb.net/api/`
- Documentation: `https://wiki.vocadb.net/docs/public-api`
- Purpose: classify songs as Vocaloid/UTAU/CeVIO/Synthesizer V/related voice-synth works and retain producer/voicebank credit metadata.
- Qualification rule: a song enters `vocaloid_spotify_10m.csv` only when a cumulative Spotify-derived source reports at least 10,000,000 streams and voice-synth classification is supported by a VocaDB match or an explicit voice-synth credit/genre marker. Historical chart-window aggregates and daily counters are ineligible.
- API-respect rule: the default build scans the top 25,000 VocaDB songs by rating with request pacing instead of attempting a hundreds-of-thousands-row crawl that conflicts with VocaDB API usage guidance. `BEATHIT_VOCADB_SCAN_LIMIT` may be raised deliberately up to the builder hard cap, but completeness is only marked true when the VocaDB scan is exhaustive and the broad Spotify count source is available.
- A public third-party Hugging Face export of VocaDB exists, but the builder does not silently treat a third-party export as authoritative/current classification unless its schema/provenance can be validated.
- The list is conditional and is never padded to 10,000.

## VTuber original songs and covers

### Holodex

- API: `https://holodex.net/api/v2`
- Documentation: `https://docs.holodex.net/`
- Topic classifications used: `Original_Song` and `Music_Cover`.
- Main artist: uploader VTuber/channel identity supplied by Holodex.
- Reliable API access: `HOLODEX_API_KEY` via the documented `X-APIKEY` header.

### YouTube

- Exact video views are fetched in batches through the YouTube Data API when `YOUTUBE_API_KEY` is present.
- Without a key, the builder can use the public Return YouTube Dislike API `viewCount` field as a **third-party cached** fallback; provenance is explicitly recorded and it is not represented as an official live YouTube API response.
- Optional capped `yt-dlp` metadata lookup is a last resort.
- If all count routes fail, the row is retained only with an explicitly labeled proxy/rank metric; it is never assigned a fabricated view count.

### HoloStats fallback/augmentation

- `https://www.holostats.com/songs`
- Purpose: Hololive-only original/cover song view rankings when its public table is available.
- It supplements, but does not replace, the cross-agency Holodex corpus.

## Classical fallback

### ListenBrainz

- API: `https://api.listenbrainz.org/1`
- Purpose: tag-radio popularity fallback when the Spotify-derived genre catalog does not provide 10,000 verified classical candidates.
- Rank evidence from tag radio is explicitly labeled as a rank score, not listens.

## Source priority

For exact counts, the builder prefers:

1. live exact cumulative count retrieved at build time;
2. exact cumulative count from the newest trustworthy snapshot available to the builder;
3. older exact cumulative count snapshot;
4. chart-window aggregate or other exact-but-non-lifetime count, clearly labeled as such;
5. platform popularity score;
6. transparent source rank/proxy only when no song-level count exists.

The raw metric name, unit, retrieval date, and URL are retained so users can distinguish these cases.


## Screen soundtrack association seed

The builder primarily uses source-declared soundtrack/score album and genre metadata. A very small curated seed ensures famous needle-drop/theme cases explicitly requested by the user are not lost when the Spotify album itself is not a soundtrack release. Each seed carries an independent association URL. Current examples include *Way Back then* / *Squid Game*, *Bella Ciao* / *Money Heist*, and *What I've Done* / *Transformers (2007)*. The seed establishes only the screen-work association; the popularity metric still comes from the matched song catalog.

## Spotify regional country charts — Kworb aggregates of Spotify Charts

- Discovery/index: `https://kworb.net/spotify/`
- Per-market pattern: `https://kworb.net/spotify/country/<code>_daily_totals.html`
- Upstream provenance: Kworb states that these pages are aggregates of daily and weekly charts provided by Spotify.
- Fields used: artist, title, Spotify track ID embedded in the track-history URL, days on chart, Top-10 days, peak rank, peak daily streams, cumulative country-chart stream total, and chart coverage dates.
- Output: one `data/countries/*_top1000.csv` per dynamically detected country/territory market, plus `data/countries/index.json`.

Accuracy boundary: the `Total` on a country daily-totals page is the sum of streams recorded **while the song was inside that country's Spotify daily chart**. The source itself warns that totals do not include time spent outside the daily chart. BeatHit therefore labels this metric `spotify_country_chart_streams`; it is not represented as a lifetime Spotify play count.

For the final megalist only, one deduplicated country observation per market may be summed across distinct markets as `spotify_regional_chart_streams_sum`. Because country markets are geographically disjoint, that sum is useful popularity evidence, but it still excludes streams earned while a song was outside each market's Top 200 and is never labeled a lifetime total.

# BeatHit cover candidate discovery workers

This replaces the first YouTube-only `scripts/cover_discovery.py` worker, **after**
`BeatHit-cover-discovery-exact.patch` has already been applied. It does not touch
`.github/parallel/build_list.py` or existing verified cover CSVs.

## Setup on Windows CMD

From the root of `BeatHit-Dataset`:

```bat
python -m pip install -r requirements.txt
python -m pip install tzdata
set "YOUTUBE_API_KEY=YOUR_OWN_API_KEY"
python -m unittest discover -s tests -p test_cover_discovery.py -v
python scripts\cover_discovery.py --language all --watch
```

Leave the CMD process running. Stop with Ctrl+C. Start the same command later to
resume. `YOUTUBE_API_KEY` may be omitted for Apple Music/iTunes-only discovery.
Do not publish or paste your API key. Rotate an exposed key in Google Cloud.

For a single language:

```bat
python scripts\cover_discovery.py --language norwegian --watch
```

For a one-off pass compatible with existing workers:

```bat
python .github\parallel\build_list.py cover_norwegian
```

To inspect progress without using API quota:

```bat
python scripts\cover_discovery.py --status
```

## Data and resume behavior

- Authoritative SQLite database: `.cache/beathit-expansion/cover_discovery.sqlite3`.
- Candidate exports: `data/language_covers/<language>/<language>_cover_candidates.csv`.
- Old candidate CSVs and `.cache/beathit-expansion/cover_discovery_<language>.json`
  search checkpoints are imported automatically.
- SQLite records page tokens, retries, cooldowns, jobs, and global daily API use.
- Rate-limit failures do not crash the worker; `--watch` resumes at the next opportunity.
- Invalid pagination tokens restart that query; existing candidate IDs are deduplicated.
- Search jobs identify channels. `channels.list` + uploads `playlistItems.list`
  expand these channels with general API quota, preserving scarce search calls.
- Apple Search API adds a secondary, keyless discovery source.
- Each candidate is **unverified**. Text matching or national storefronts do not prove
  a song is a cover or determine its performed language. The collector will not
  silently add candidates to a verified dataset or claim a target has been met.

## Quota controls

As of the 2026 YouTube quota model, `search.list` has a separate default limit of
100 searches/day (each page is another search), and ordinary reads draw from a
10,000-unit/day bucket. Both reset at midnight Pacific time. Check the actual
limits assigned to your Google Cloud project.

The defaults intentionally leave a margin and divide search calls across 10
languages. Override as needed:

```bat
set "BEATHIT_YT_SEARCH_DAILY_LIMIT=90"
set "BEATHIT_YT_SEARCH_PER_LANGUAGE_DAILY=9"
set "BEATHIT_YT_GENERAL_DAILY_LIMIT=9000"
```

The limits are *project-wide* only if all workers share this SQLite file and use
one Google Cloud project/API key. Do not run independent copies with different
SQLite databases against the same key. The cache directory is local and not
persisted automatically by ephemeral GitHub Actions runners; back it up if needed.

The worker uses no credentials besides the optional YouTube API key. There is no
unlimited-source bypass. Search coverage, availability, and independent cover /
language verification remain unresolved limits on reaching 10,000 real covers.

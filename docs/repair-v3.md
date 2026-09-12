# Corrective patch v3: audit and operational limits

Base: `9ea9f2108bec712cc9d50569a7a781c9ab94fa80`.
This replaces the unpushed `BeatHit-fill-remaining-builders-hololive-v2.patch`.
Apply one replacement patch to this base, not both patches on top of one another.

## Findings and corrections

| Area | Finding | Correction |
|---|---|---|
| ListenBrainz | Tag radio returns recording IDs, not usable song metadata. Five broad bands still discarded those IDs. | Hydrate recording/artist/release metadata, scan five-point bands, retain successful checkpoints, deduplicate overlap. |
| Source classification | A live jazz query returned Coldplay's Colour Spectrum: one jazz vote against stronger pop/rock tags. | Confirm the requested tag on the hydrated entity and reject votes weaker than half its strongest genre vote. Radio tag_count is not treated as votes for the queried tag. |
| Novelty | The existing list included spoken stand-up routines tagged only comedy. | Require music-specific tags or explicit curated song evidence. Reject corrupted replacement-character identities. |
| Internet-native | A doujin-only artist tag classified Punjabi pop as internet-native. | Require another catalog category signal, or recording/release tag corroboration through ListenBrainz. |
| Screen music | Generic soundtrack/cinematic labels admitted game music and unrelated albums. | Use screen-specific evidence; hydrate fallback recordings. Do not manufacture a screen-work title from an unrelated release name. |
| Anime | English-title collisions were reported as duplicate songs even when MAL IDs differed. | Scope validation to stable anime IDs and enforce one entry per anime. Reused themes across separate anime remain valid. |
| Anime retrieval | Jikan errors were cached as empty successful results; canonical output was deleted before retrieval finished. | Retry transient failures in subsequent runs, cache successful pages/themes, preserve canonical files during construction, add a bounded current MAL popularity index fallback. |
| Game music | Soundtrack packaging remained in game names; chiptune/gaming tags alone were accepted as game evidence. | Clean packaging before selection and require stronger game tags. Generic film/game title homonyms no longer establish game membership. |
| Vocaloid | API-confirmed absent videos and failed lookups were conflated. Finalization printed/wrote every provisional song. | Separate available/unavailable/unresolved states, retry legacy missing entries, stop on authentication/quota errors, retain API checkpoints and emit progress every 5,000 candidates. |
| VTubers | Topic classifications could conflict with explicit Cover labels. Completed outputs could be replaced by a much smaller fallback. | Reject original-topic cover conflicts, preserve prior candidate pools and outputs through outages, use exact catalog matches for Spotify eligibility. |
| Hololive | Earlier patch guessed a YouTube threshold and counted five songs separately per list and only for observed members. | Joint original/cover selection against a source-backed roster, explicit shortfalls, verified OVER//RIDE video identity and fetched baseline. |
| Publishing | Soundtracks, countries and several standard output directories were not staged. Checksums preceded final validation reports. | Stage every generated category through an allowlist; publish data/reports/checksums in one normal non-forced commit. Preserve artifacts before pushing. |
| Validation | verify_targets.py printed incomplete results but exited successfully. Strict validation failure did not set the workflow exit status. | Both failures now propagate. Valid partial data is preserved without falsely declaring completion. |
| Reuse | Forced categories rebuilt on every future workflow run. | Builder revisions invalidate only affected old outputs once. Corrected complete outputs are eligible for reuse. |

## Hololive rule

- Five **distinct songs per member across originals and covers combined**.
- Prefer confidently matched, trusted cumulative Spotify streams **greater than 500,000**.
- If that count is unavailable, use the song's individual YouTube video views above the rounded-down OVER//RIDE baseline.
- The verified baseline video is `hgMl8y2ufIg`: [OVER//RIDE, Mori Calliope × Nerissa Ravencroft](https://www.youtube.com/watch?v=hgMl8y2ufIg).
- Rounding was not specified previously. The documented default is down to the nearest **100,000**, configurable with `BEATHIT_HOLOLIVE_ROUND_DOWN`.
- There is **no guessed fallback threshold**. If the baseline or candidate counters cannot be resolved, the report remains incomplete.
- Mandatory inclusions take precedence over the nominal 10,000-row VTuber target. Only those two categories can exceed their nominal target for this rule.
- The seed roster contains 102 members identified through the [official Hololive directory](https://hololive.hololivepro.com/en/talents/) and [official Holostars directory](https://holostars.hololivepro.com/en/talent/), including listed alumni, excluding staff. Each entry includes its exact source profile and verified channel IDs. Group navigation channels are excluded. Shared FUWAMOCO channels are handled explicitly.
- This is a dated, auditable roster snapshot, not a claim to enumerate historical members removed from both official directories. Add newly announced members or additional historical members to the roster with verified identity evidence; zero-song members must remain visible in coverage reports.
- `data/hololive_coverage.json` records each member's count, missing songs, mandatory identities, threshold evidence, unresolved counters and source-scan completeness. Validation recomputes member counts from the actual CSVs.

## What this patch does not prove

A passing regression suite does not prove that every external corpus yields 10,000 qualifying rows. No full network build is bundled or run on the user's computer.

The preserved build had 6,327 anime, 2,347 game recordings, 1,083 internet-native, 5,172 jazz, 2,104 children's and 1,261 novelty rows. The committed soundtrack CSV had **5,660** rows despite a later report claiming 5,669; missing staging explains that discrepancy.

Live probes during this audit established:

- AnimeThemes returned HTTP 200 with nested song/artist/resource data.
- ListenBrainz returned HTTP 200 and hydrated real recordings; 20 jazz samples passed the added tag corroboration, and the weak Coldplay match was rejected.
- Jikan returned HTTP 504 from its upstream MAL connection. Cache/retry fixes cannot force that upstream service to be available.
- Holodex returned HTTP 403 without an API key in the audit environment. The workflow uses the repository's `HOLODEX_API_KEY`; the authenticated full scan still needs execution there.
- YouTube's oEmbed identified the exact OVER//RIDE video and author. The Return YouTube Dislike response reported 1,448,207 cached views, timestamped 2026-09-11T23:00:58Z. This observation is audit evidence only; it is not hard-coded as the build threshold.

Country targets are unchanged. Cyprus and Malta's source-exhausted short lists and Andorra's unsupported historical chart remain honest incomplete results. Fabricating extra songs or redefining their chart totals would violate the requested dataset.

## Workflow behavior

The workflow runs tests before expensive retrieval. API caches and resumable candidate pools survive subsequent runs; legacy checkpoints are restored before current-format checkpoints. Successful per-file serialization replaces canonical CSV/gzip files atomically.

A full build may still finish with a validation failure while preserving improved datasets. Inspect the actual shortfall and source-error reports instead of treating a green job as the sole measure of progress. Failed network requests never establish an exhausted corpus.

Artifact restoration supports both old data-root and new repository-root layouts. Normal Git push semantics protect concurrent remote changes; if the remote moves, the push fails and the uploaded output remains available.

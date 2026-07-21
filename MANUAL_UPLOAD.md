# Manual upload to `Dummy1-sudo/BeatHit-Dataset`

## 1. Extract this ZIP

Copy the contents into the root of your local repository.

```bash
git clone https://github.com/Dummy1-sudo/BeatHit-Dataset.git
cd BeatHit-Dataset
# copy/extract the delivered files here
git add .
git commit -m "Add BeatHit high-accuracy dataset builder"
git push -u origin main
```

For an empty GitHub repository without an existing local clone:

```bash
git init
git branch -M main
git remote add origin https://github.com/Dummy1-sudo/BeatHit-Dataset.git
git add .
git commit -m "Add BeatHit high-accuracy dataset builder"
git push -u origin main
```

## 2. Let the full build run

The first push of the builder files triggers `.github/workflows/full-build.yml` automatically. The workflow downloads the large public source snapshots, builds every requested list, validates row counts plus semantic constraints, writes completion reports, and commits generated CSVs back to `main`. The network/source work can take a long time because the strongest source set is multi-gigabyte.

Open **Actions → full-high-accuracy-build** to watch progress or rerun it manually.

If the final “Commit generated datasets” step is denied, set **Settings → Actions → General → Workflow permissions → Read and write permissions**, then rerun the workflow. The workflow requests `contents: write`, but repository policy can still restrict the `GITHUB_TOKEN`.

## 3. Optional secrets for better VTuber metrics

In GitHub: **Settings → Secrets and variables → Actions → New repository secret**.

Recommended:

- `HOLODEX_API_KEY`
- `YOUTUBE_API_KEY`

Optional tuning:

- repository/environment variable `BEATHIT_VOCADB_SCAN_LIMIT` is not required; the workflow defaults to 25,000 high-rated candidates to respect VocaDB API usage guidance. Raising it increases request volume and should be done deliberately.

The build still runs without these. Without `YOUTUBE_API_KEY`, it first uses the explicitly attributed Return YouTube Dislike cached `viewCount` fallback; optional capped `yt-dlp` is available as a last resort. If no count is resolved, the row keeps a clearly labeled proxy instead of invented views.

## 4. Verify completion

After the workflow commits:

```bash
git pull
python -m pip install -e . -r requirements-full.txt
python -m music_megalist validate --complete
python scripts/verify_targets.py
```

`--complete` checks fixed-size targets. `scripts/verify_targets.py` additionally checks the 51k era allocation, >=50 genre coverage, Vocaloid threshold validity, VTuber original/cover flags, and megalist deduplication. Vocaloid is a conditional threshold list, so its correct size is the number of verified qualifying songs supported by defensible source coverage, up to 10,000—not an artificially forced 10,000. Check `STATUS.json` before calling the entire project complete.

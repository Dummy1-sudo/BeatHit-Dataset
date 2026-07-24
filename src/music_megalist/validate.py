from __future__ import annotations
from pathlib import Path
import csv
import json
from .io import read_rows
from .dedupe import norm

TARGETS={
 "anime/anime_songs.csv":1000,
 "worldwide/worldwide_51000.csv":51000,
 "classical/classical_10000.csv":10000,
 "vtuber_original/vtuber_original_10000.csv":1000,
 "emerging/emerging_10000.csv":10000,
 "genres/genres_10000.csv":10000,
 "screen_soundtracks/screen_soundtracks_10000.csv":10000,
 "vtuber_non_original/vtuber_non_original_10000.csv":1000,
 "video_games/video_game_music_1000.csv":1000,
 "internet_native/internet_native_1000.csv":1000,
 "electronic_subcultures/electronic_subcultures_1000.csv":1000,
 "alternative_extreme/alternative_extreme_1000.csv":1000,
 "jazz_depth/jazz_depth_1000.csv":1000,
 "children_childhood/children_childhood_100.csv":100,
 "unserious/unserious_1000.csv":1000,
 "special_required/special_required.csv":4,
}

def _worldwide_bucket(row: dict[str,str]) -> str:
    try:
        extra=json.loads(row.get("extra") or "{}")
        if isinstance(extra,dict):
            return str(extra.get("era_bucket") or "")
    except Exception:
        pass
    return ""

def _generic_csv_errors(path: Path) -> list[str]:
    errors=[]
    with path.open(encoding='utf-8',newline='') as f: rows=list(csv.DictReader(f))
    required={'title','main_artist','metric_name','metric_value','metric_unit','source_url','retrieved_at'}
    if rows and not required.issubset(rows[0]):
        errors.append(f"SCHEMA {path}: missing {sorted(required-set(rows[0]))}")
    # The 51k worldwide file intentionally allows one song to appear once in current and once
    # in its decade bucket. Uniqueness is therefore scoped to era_bucket for that one file.
    bucketed = path.name == 'worldwide_51000.csv'
    # Anime rows represent anime titles, not a globally unique song catalog. The same theme can
    # legitimately be reused by multiple seasons/entries, so uniqueness is scoped to the anime.
    anime_scoped = path.name == 'anime_songs.csv'
    seen_spotify=set(); seen_mbid=set(); seen_isrc=set(); seen_text=set()
    for i,r in enumerate(rows,1):
        rank=(r.get('rank') or '').strip()
        if rank:
            try:
                if int(rank) != i: errors.append(f'RANK {path} row {i}: {rank} != {i}')
            except Exception: errors.append(f'RANK_INVALID {path} row {i}: {rank}')
        for k in required:
            if not str(r.get(k,'')).strip(): errors.append(f"BLANK {path} row {i} field {k}")
        # New canonical outputs include languages as a JSON list. Legacy/minimal fixtures
        # without this newer optional column remain valid and readable.
        if 'languages' in r:
            try:
                languages=json.loads(r.get('languages') or '[]')
                if not isinstance(languages,list) or not languages or any(not str(x).strip() for x in languages):
                    errors.append(f"LANGUAGES {path} row {i}: expected non-empty JSON list")
            except Exception:
                errors.append(f"LANGUAGES_INVALID {path} row {i}")
        try: mv=float(r.get('metric_value',''))
        except Exception: errors.append(f"METRIC {path} row {i}"); continue
        unit=r.get('metric_unit','')
        if unit in {'streams','listens'}:
            lc=r.get('listen_count','')
            if not lc: errors.append(f"COUNT_MISSING {path} row {i}: count metric without listen_count")
            else:
                try:
                    if int(float(lc)) != int(mv): errors.append(f"COUNT_MISMATCH {path} row {i}")
                except Exception: errors.append(f"COUNT_INVALID {path} row {i}")
        if unit=='views':
            vc=r.get('view_count','')
            if not vc: errors.append(f"VIEW_MISSING {path} row {i}")
            else:
                try:
                    if int(float(vc)) != int(mv): errors.append(f"VIEW_MISMATCH {path} row {i}")
                except Exception: errors.append(f"VIEW_INVALID {path} row {i}")
        bucket=_worldwide_bucket(r) if bucketed else ''
        if bucketed:
            scope=(bucket,)
        elif anime_scoped:
            try:
                anime_extra=json.loads(r.get('extra') or '{}')
            except Exception:
                anime_extra={}
            anime_key=str(r.get('anime_title') or anime_extra.get('anilist_id') or '').strip()
            scope=(anime_key,) if anime_key else ()
        else:
            scope=()
        sid=(r.get('spotify_track_id') or '').strip()
        if sid:
            key=scope+(sid,)
            if key in seen_spotify: errors.append(f"DUP_SPOTIFY {path} row {i}: {sid} bucket={bucket}")
            seen_spotify.add(key)
        mb=(r.get('musicbrainz_recording_mbid') or '').strip()
        if mb:
            key=scope+(mb,)
            if key in seen_mbid: errors.append(f"DUP_MBID {path} row {i}: {mb} bucket={bucket}")
            seen_mbid.add(key)
        isrc=(r.get('isrc') or '').strip().casefold()
        if isrc:
            key=scope+(isrc,)
            if key in seen_isrc: errors.append(f"DUP_ISRC {path} row {i}: {isrc} bucket={bucket}")
            seen_isrc.add(key)
        text=(norm(r.get('title') or ''),norm(r.get('main_artist') or ''))
        if all(text):
            key=scope+text
            if key in seen_text: errors.append(f"DUP_TEXT {path} row {i}: {r.get('title')} / {r.get('main_artist')} bucket={bucket}")
            seen_text.add(key)
    return errors

def validate(data_dir: str|Path='data', *, require_complete: bool=False) -> list[str]:
    data=Path(data_dir); errors=[]
    for p in data.rglob('*.csv'):
        if '/raw/' in p.as_posix(): continue
        # Reports/inputs are not canonical song-list CSVs and use intentionally different schemas.
        if p.name in {'coverage_report.csv'}: continue
        # bootstrap files are provenance examples, not final canonical categories.
        if '/bootstrap/' in p.as_posix() or '/seeds/' in p.as_posix() or p.name.endswith('_snapshot.csv'): continue
        errors.extend(_generic_csv_errors(p))
    vp=data/'vocaloid'/'vocaloid_youtube_100m.csv'
    if vp.exists():
        rows=read_rows(vp)
        if len(rows)>10000: errors.append('COUNT vocaloid > 10000')
        for i,r in enumerate(rows,1):
            ex=r.extra or {}
            if r.metric_name!='youtube_views' or r.metric_unit!='views' or r.metric_value<100_000_000:
                errors.append(f'VOCALOID_THRESHOLD row {i}')
            if r.view_count is None or int(r.view_count)!=int(r.metric_value):
                errors.append(f'VOCALOID_VIEW_COUNT row {i}')
            if str(ex.get('vocadb_song_type') or '').casefold()!='original':
                errors.append(f'VOCALOID_SONG_TYPE row {i}')
            if str(ex.get('youtube_pv_type') or '').casefold()!='original':
                errors.append(f'VOCALOID_PV_TYPE row {i}')
            if str(ex.get('youtube_pv_service') or '').casefold()!='youtube':
                errors.append(f'VOCALOID_PV_SERVICE row {i}')
            if not str(ex.get('youtube_video_id') or '').strip():
                errors.append(f'VOCALOID_VIDEO_ID row {i}')
    kp=data/'kpop'/'kpop_youtube_over_100m.csv'
    if kp.exists():
        rows=read_rows(kp)
        for i,r in enumerate(rows,1):
            if r.metric_name!='youtube_views' or r.metric_unit!='views' or r.metric_value<=100_000_000:
                errors.append(f'KPOP_THRESHOLD row {i}')
            if r.view_count is None or int(r.view_count)!=int(r.metric_value):
                errors.append(f'KPOP_VIEW_COUNT row {i}')
    wp=data/'worldwide'/'worldwide_51000.csv'
    if wp.exists():
        with wp.open(encoding='utf-8',newline='') as f: wr=list(csv.DictReader(f))
        expected={'current':10000,'2020s':10000,'2010s':10000,'2000s':10000,'1990s':5000,'1980s':3000,'1970s':2000,'1960s':1000}
        counts={k:0 for k in expected}
        for r in wr:
            b=_worldwide_bucket(r)
            if b in counts: counts[b]+=1
        for b,target in expected.items():
            if counts[b] != target: errors.append(f'WORLDWIDE_BUCKET {b}: {counts[b]} != {target}')
    gp=data/'genres'/'genres_10000.csv'
    if gp.exists():
        with gp.open(encoding='utf-8',newline='') as f: gr=list(csv.DictReader(f))
        gs=set()
        for r in gr:
            try:
                e=json.loads(r.get('extra') or '{}')
                if isinstance(e,dict) and e.get('selection_genre'): gs.add(str(e['selection_genre']))
            except Exception: pass
        if len(gr)>=10000 and len(gs)<50: errors.append(f'GENRE_DIVERSITY: {len(gs)} < 50')
    cp=data/'countries'/'index.json'
    if cp.exists():
        try:
            ci=json.loads(cp.read_text(encoding='utf-8'))
            markets=ci.get('markets') or []
            detected=int(ci.get('detected_country_markets') or 0)
            failures=ci.get('failures') or []
            codes=set(); materialized_rows=0
            for m in markets:
                code=str(m.get('country_code') or '').upper()
                if not code: errors.append('COUNTRY_INDEX blank country code'); continue
                if code in codes: errors.append(f'COUNTRY_INDEX duplicate code {code}')
                codes.add(code)
                rows=int(m.get('unique_songs') or 0)
                materialized_rows += rows
                if rows < 0 or rows > 1000:
                    errors.append(f'COUNTRY_COUNT_INVALID {code}: {rows}')
                fn=str(m.get('file') or '')
                fp=data/'countries'/fn if fn else None
                if not fn or fp is None or not fp.exists():
                    errors.append(f'COUNTRY_FILE {code}: missing {fn}')
                elif fp is not None:
                    with fp.open(encoding='utf-8',newline='') as f:
                        actual=max(0,sum(1 for _ in csv.reader(f))-1)
                    if actual != rows:
                        errors.append(f'COUNTRY_INDEX_COUNT_MISMATCH {code}: index={rows} file={actual}')
                available=int(m.get('available_unique_songs') or rows)
                expected=int(m.get('expected_rows') or min(1000, available))
                exhausted=bool(m.get('source_exhausted_below_target'))
                if expected < 0 or expected > 1000:
                    errors.append(f'COUNTRY_EXPECTED_INVALID {code}: {expected}')
                if exhausted and available != expected:
                    errors.append(f'COUNTRY_EXHAUSTION_MISMATCH {code}: available={available} expected={expected}')
                if require_complete and rows != 1000:
                    suffix=' (source exhausted)' if exhausted else ''
                    errors.append(f'COUNTRY_COUNT {code}: {rows} != 1000{suffix}')
            if detected <= 0: errors.append('COUNTRY_INDEX no detected markets')
            if len(markets) > detected:
                errors.append(f'COUNTRY_MARKETS built {len(markets)} > detected {detected}')
            recorded_success=int(ci.get('successfully_built_markets') or len(markets))
            if recorded_success != len(markets):
                errors.append(f'COUNTRY_SUCCESS_COUNT {recorded_success} != {len(markets)}')
            recorded_rows=int(ci.get('total_materialized_rows') or 0)
            if recorded_rows != materialized_rows:
                errors.append(f'COUNTRY_TOTAL_ROWS {recorded_rows} != materialized {materialized_rows}')
            # Source-exhausted short markets are valid partial outputs, but they do not satisfy
            # the user's requested top-1,000 target. Normal validation accepts them; strict
            # completion remains false without fabricating missing ranks.
            if require_complete:
                if len(markets) != detected: errors.append(f'COUNTRY_MARKETS built {len(markets)} != detected {detected}')
                if failures: errors.append(f'COUNTRY_FAILURES {len(failures)}')
                unsupported=ci.get('unsupported_markets') or []
                if unsupported: errors.append(f'COUNTRY_UNSUPPORTED {len(unsupported)}')
                expected_rows=detected*1000
                if recorded_rows != expected_rows:
                    errors.append(f'COUNTRY_TOTAL_ROWS {recorded_rows} != {expected_rows}')
        except Exception as exc:
            errors.append(f'COUNTRY_INDEX_INVALID {exc}')
    elif require_complete:
        errors.append('MISSING countries/index.json')
    if require_complete:
        for rel,target in TARGETS.items():
            p=data/rel
            if not p.exists(): errors.append(f'MISSING {rel}'); continue
            with p.open(encoding='utf-8',newline='') as f: n=max(0,sum(1 for _ in csv.reader(f))-1)
            if n != target: errors.append(f'COUNT {rel}: {n} != {target}')
    return errors

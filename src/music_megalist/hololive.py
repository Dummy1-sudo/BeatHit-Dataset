"""Hololive selection rules, applied jointly to originals and covers."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

from .dedupe import dedupe, norm
from .io import read_rows, write_rows
from .listenbrainz import atomic_json

OVERRIDE_VIDEO_ID = 'hgMl8y2ufIg'


def load_roster(data: Path) -> dict:
    payload = json.loads((data / 'seeds' / 'hololive_members.json').read_text(encoding='utf-8'))
    return {m['member_id']: m for m in payload['members']}


def credited_members(video: dict, roster: dict) -> list[str]:
    channel = video.get('channel') or {}
    channel_id = channel.get('id') or video.get('channel_id')
    title = norm(str(video.get('title') or ''))
    owners = [key for key, m in roster.items() if channel_id in m.get('channel_ids', [])]
    named = [key for key in owners if any(norm(a) and norm(a) in title for a in roster[key]['aliases'])]
    # Shared duo channels may credit a solo performance explicitly.
    members = set(named or owners)
    for mention in video.get('mentions') or []:
        if not isinstance(mention, dict):
            continue
        for key, member in roster.items():
            if mention.get('id') in member.get('channel_ids', []) and any(
                    norm(a) and norm(a) in title for a in member['aliases']):
                members.add(key)
    return sorted(members)


def popular(row, threshold: int | None) -> bool:
    ex = row.extra or {}
    # Exact trusted cumulative Spotify matching takes precedence. Chart-window
    # aggregates, fuzzy artist matches and untrusted snapshots do not qualify.
    streams = ex.get('hololive_trusted_spotify_streams')
    if streams is not None:
        return int(streams) > 500_000
    views = ex.get('hololive_youtube_views')
    return threshold is not None and views is not None and int(views) > threshold


def priority(row):
    # Counts from different services are not compared as if their units were equal.
    ex = row.extra or {}
    views = ex.get('hololive_youtube_views', row.view_count)
    if views is not None:
        return (2, math.log10(max(0, int(views)) + 1))
    streams = ex.get('hololive_trusted_spotify_streams')
    if streams is not None:
        return (1, math.log10(max(0, int(streams)) + 1))
    return (0, row.overall_popularity_score or 0)


def select_joint(pools: dict, roster: dict, threshold: int | None, target=10_000):
    """Reserve high-reach songs and five distinct songs per member across both lists."""
    ranked = {key: sorted(dedupe(rows), key=priority, reverse=True) for key, rows in pools.items()}
    candidates = [(key, row) for key, rows in ranked.items() for row in rows]
    candidates.sort(key=lambda pair: priority(pair[1]), reverse=True)
    required = set()
    by_member = defaultdict(list)
    for key, row in candidates:
        identity = (key, norm(row.title), norm(row.main_artist))
        members = set((row.extra or {}).get('hololive_member_ids') or []) & roster.keys()
        if members and popular(row, threshold):
            required.add(identity)
        for member in members:
            by_member[member].append((identity, row))
    for member in roster:
        songs = set()
        for identity, row in by_member[member]:
            # Multiple uploads of one song cannot satisfy the five-song minimum.
            song = norm(row.title)
            if song in songs:
                continue
            songs.add(song)
            required.add(identity)
            if len(songs) == 5:
                break
    selected = {}
    for key, rows in ranked.items():
        mandatory = [r for r in rows if (key, norm(r.title), norm(r.main_artist)) in required]
        optional = [r for r in rows if (key, norm(r.title), norm(r.main_artist)) not in required]
        # The user's "all qualifying songs" rule takes precedence over the nominal cap.
        selected[key] = sorted(mandatory + optional[:max(0, target-len(mandatory))], key=priority, reverse=True)
        for rank, row in enumerate(selected[key], 1):
            row.rank = rank
    counts = {}
    for member in roster:
        songs = {norm(r.title) for rows in selected.values() for r in rows
                 if member in (r.extra or {}).get('hololive_member_ids', [])}
        counts[member] = len(songs)
    report = {'minimum_per_member': 5, 'scope': 'originals and covers combined',
              'members': {key: {'name': roster[key]['name'], 'songs': value,
                                  'missing': max(0, 5-value)} for key, value in counts.items()},
              'required_songs_selected': len(required),
              'required_song_keys': [list(key) for key in sorted(required)],
              'coverage_complete': bool(roster) and all(c >= 5 for c in counts.values())}
    return selected, report


def finalize(data, cache, status, built, threshold, threshold_evidence, target=10_000):
    roster = load_roster(data)
    pools = {}
    for key in ('vtuber_original', 'vtuber_non_original'):
        path = cache / (key + '_candidates.jsonl.gz')
        pools[key] = read_rows(path) if path.exists() else list(built.get(key, []))
    selected, report = select_joint(pools, roster, threshold, target)
    report['youtube_threshold'] = threshold_evidence
    scans = {key: status.sources.get('holodex_' + ('Original_Song' if key == 'vtuber_original' else 'Music_Cover'), {}) for key in pools}
    report['source_scans'] = scans
    report['unresolved_popularity'] = [
        {'title':r.title,'artist':r.main_artist,'video_id':r.extra.get('holodex_video_id')}
        for rows in pools.values() for r in rows if r.extra.get('hololive_member_ids')
        and r.extra.get('hololive_trusted_spotify_streams') is None
        and r.extra.get('hololive_youtube_views') is None]
    report['complete'] = bool(report['coverage_complete'] and threshold is not None and
                              all(s.get('complete') for s in scans.values()) and not report['unresolved_popularity'])
    report['qualification_note'] = 'Trusted Spotify streams >500000, otherwise individual YouTube views above the rounded-down OVER//RIDE baseline. No invented threshold.'
    atomic_json(data / 'hololive_coverage.json', report)
    for key, rows in selected.items():
        filename = 'vtuber_original_10000.csv' if key == 'vtuber_original' else 'vtuber_non_original_10000.csv'
        write_rows(rows, data / key / filename)
        built[key] = rows
        st = status.datasets[key]
        st.rows = len(rows)
        st.complete = len(rows) >= target and report['complete']
        st.metric_coverage = dict(__import__('collections').Counter(r.metric_name for r in rows))
        st.notes.append('Hololive member coverage and threshold evidence: data/hololive_coverage.json. Mandatory inclusions may exceed 10000.')
    status.save()
    return report


def coverage_errors(data: Path, *, require_complete: bool = False) -> list[str]:
    """Verify the coverage report against materialized CSVs, not just its booleans."""
    path=data/'hololive_coverage.json'
    if not path.exists():
        return ['missing coverage report'] if require_complete else []
    try:
        report=json.loads(path.read_text(encoding='utf-8'))
        roster=load_roster(data)
        rows_by_key={}
        for key in ('vtuber_original','vtuber_non_original'):
            filename='vtuber_original_10000.csv' if key=='vtuber_original' else 'vtuber_non_original_10000.csv'
            rows_by_key[key]=read_rows(data/key/filename)
        errors=[]
        if set(report.get('members',{}))!=set(roster):
            errors.append('reported roster differs from source roster')
        for member in roster:
            actual=len({norm(r.title) for rows in rows_by_key.values() for r in rows
                        if member in r.extra.get('hololive_member_ids',[])})
            stated=report.get('members',{}).get(member,{})
            if stated.get('songs')!=actual or stated.get('missing')!=max(0,5-actual):
                errors.append(f'{member}: coverage count mismatch')
            if require_complete and actual<5:
                errors.append(f'{member}: {actual}/5 distinct songs')
        identities={(key,norm(r.title),norm(r.main_artist)) for key,rows in rows_by_key.items() for r in rows}
        required={tuple(key) for key in report.get('required_song_keys',[])}
        if required-identities:
            errors.append(f'{len(required-identities)} mandatory songs absent from outputs')
        threshold=report.get('youtube_threshold') or {}
        views=threshold.get('observed_views');step=threshold.get('rounded_down_to')
        if views is not None and (not isinstance(step,int) or step<=0 or threshold.get('threshold')!=(int(views)//step)*step):
            errors.append('invalid rounded YouTube threshold')
        if threshold.get('video_id')!=OVERRIDE_VIDEO_ID:
            errors.append('wrong baseline video ID')
        scans=report.get('source_scans') or {}
        if require_complete and (not report.get('complete') or views is None
                or threshold.get('threshold') is None or report.get('unresolved_popularity')
                or not all((scans.get(key) or {}).get('complete') for key in ('vtuber_original','vtuber_non_original'))):
            errors.append('incomplete source scan, member coverage or popularity resolution')
        return errors
    except (OSError,ValueError,TypeError,KeyError) as exc:
        return [f'invalid coverage data: {exc}']

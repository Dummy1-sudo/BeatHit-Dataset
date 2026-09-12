"""Resumable tag-radio discovery with recording metadata hydration.

Radio is a bounded sample, not an exhaustive discography. Popularity bands are
preferences in the upstream API, so overlap is expected and never counted twice.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

API = 'https://api.listenbrainz.org/1'
MBID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    temp.replace(path)


def request_json(client: httpx.Client, method: str, url: str, **kwargs: Any) -> Any:
    for attempt in range(3):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < 2:
                try:
                    delay = float(response.headers.get('Retry-After', 2 ** attempt))
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(min(30, max(1, delay)))
                continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError('request retry loop exhausted')


def tag_support(metadata: dict, evidence: dict) -> dict | None:
    """Reject weak/mismatched tags using the hydrated entity's own tag votes.

    Radio's tag_count is not the vote count for the requested tag. A stray jazz
    vote on a strongly pop/rock release must not establish jazz membership.
    """
    scope=str(evidence.get('source') or '').replace('-', '_')
    tags=(metadata.get('tag') or {}).get(scope) or []
    normalize=lambda value: re.sub(r"[\s_-]+", " ", str(value).casefold()).strip()
    matching=[t for t in tags if normalize(t.get('tag')) == normalize(evidence.get('tag'))]
    votes=max((int(t.get('count') or 0) for t in matching),default=0)
    strongest=max((int(t.get('count') or 0) for t in tags if t.get('genre_mbid')),default=0)
    if votes <= 0 or votes < strongest * .5:
        return None
    return {'matching_tag_votes':votes,'strongest_genre_votes':strongest}


class TagRadio:
    def __init__(self, cache: Path, status: Any, category: str):
        self.path = cache / 'listenbrainz_radio_v1.json'
        self.status = status
        self.category = category
        self.data: dict = {'queries': {}, 'metadata': {}}
        try:
            if self.path.exists():
                self.data.update(json.loads(self.path.read_text(encoding='utf-8')))
            # Migrate positive metadata from previous builds; no large refetch.
            legacy = cache / 'listenbrainz_video_game_music.json'
            if legacy.exists():
                old = json.loads(legacy.read_text(encoding='utf-8'))
                for mbid, value in (old.get('metadata') or {}).items():
                    if isinstance(value, dict):
                        self.data['metadata'].setdefault(mbid, value)
        except (ValueError, OSError, TypeError) as exc:
            status.warnings.append(f'ListenBrainz cache read: {exc}')
        self.stats = {'queries': 0, 'cached_queries': 0, 'candidates': 0,
                      'hydrated': 0, 'errors': [], 'exhaustive': False}
        status.sources['listenbrainz_' + category] = self.stats

    def recordings(self, tags: list[str], *, scopes: set[str] | None = None):
        """Yield hydrated records in descending preferred popularity bands.

        The caller may stop after filling its target. Every successful request is
        checkpointed first; errors and missing metadata are retried on the next run.
        """
        seen: set[str] = set()
        attempted_metadata: set[str] = set()
        now = time.time()
        deadline=time.monotonic()+max(0,int(os.getenv('BEATHIT_LB_MAX_SECONDS','900')))
        max_requests=max(0,int(os.getenv('BEATHIT_LB_MAX_QUERIES','400')))
        consecutive_failures=0
        with httpx.Client(timeout=45, follow_redirects=True,
                          headers={'User-Agent': 'BeatHit-Dataset/1.0 (+https://github.com/Dummy1-sudo/BeatHit-Dataset)'}) as client:
            # Five-point windows cover deeper candidates than the former head query.
            for high in range(100, 0, -5):
                for tag in dict.fromkeys(tags):
                    key = f'{tag}|{high-5}|{high}'
                    cached = self.data['queries'].get(key) or {}
                    payload = cached.get('items')
                    if isinstance(payload, list) and now - cached.get('checked_at', 0) < 7 * 86400:
                        self.stats['cached_queries'] += 1
                    else:
                        if self.stats['queries']>=max_requests or time.monotonic()>=deadline or consecutive_failures>=3:
                            self.stats['stop_reason']='request_budget_or_circuit_breaker'
                            return
                        self.stats['queries'] += 1
                        if self.stats['queries'] % 20 == 0:
                            print(f"ListenBrainz {self.category}: requests={self.stats['queries']} hydrated={self.stats['hydrated']}",flush=True)
                        try:
                            payload = request_json(client, 'GET', API + '/lb-radio/tags', params={
                                'tag': tag, 'operator': 'OR', 'count': 1000,
                                'pop_begin': high-5, 'pop_end': high})
                            if not isinstance(payload, list):
                                raise ValueError('expected tag-radio list of recording MBIDs')
                            self.data['queries'][key] = {'items': payload, 'checked_at': now}
                            atomic_json(self.path, self.data)
                            consecutive_failures=0
                            time.sleep(.2)
                        except (httpx.HTTPError, ValueError) as exc:
                            consecutive_failures+=1
                            self.stats['errors'].append(f'{tag} {high-5}-{high}: {exc}')
                            if not isinstance(payload, list):
                                continue
                    candidates = {}
                    for item in payload:
                        mbid = str(item.get('recording_mbid') or '').lower()
                        if not MBID.fullmatch(mbid) or mbid in seen:
                            continue
                        if scopes is not None and item.get('source') not in scopes:
                            continue
                        candidates[mbid] = item
                    self.stats['candidates'] += len(candidates)
                    missing = [m for m in candidates if not self.data['metadata'].get(m) and m not in attempted_metadata]
                    for start in range(0, len(missing), 500):
                        batch = missing[start:start+500]
                        attempted_metadata.update(batch)
                        if time.monotonic()>=deadline or consecutive_failures>=3:
                            self.stats['stop_reason']='metadata_budget_or_circuit_breaker'
                            return
                        try:
                            metadata = request_json(client, 'POST', API + '/metadata/recording/',
                                                    json={'recording_mbids': batch, 'inc': 'artist release tag'})
                            if not isinstance(metadata, dict):
                                raise ValueError('expected metadata keyed by recording MBID')
                            for mbid in batch:
                                value = metadata.get(mbid)
                                if isinstance(value, dict) and (value.get('recording') or {}).get('name') and (value.get('artist') or {}).get('name'):
                                    self.data['metadata'][mbid] = value
                            atomic_json(self.path, self.data)
                            consecutive_failures=0
                            time.sleep(.2)
                        except (httpx.HTTPError, ValueError) as exc:
                            consecutive_failures+=1
                            self.stats['errors'].append(f'metadata: {exc}')
                    for mbid, item in candidates.items():
                        metadata = self.data['metadata'].get(mbid)
                        if not isinstance(metadata, dict):
                            continue
                        # Missing titles may become available later; never cache a failure as empty.
                        if not (metadata.get('recording') or {}).get('name') or not (metadata.get('artist') or {}).get('name'):
                            continue
                        evidence={**item,'tag':tag,'band':[high-5,high]}
                        support=tag_support(metadata,evidence)
                        if support is None:
                            self.stats['weak_or_unconfirmed_tags']=self.stats.get('weak_or_unconfirmed_tags',0)+1
                            continue
                        seen.add(mbid)
                        self.stats['hydrated'] += 1
                        yield mbid, metadata, {**evidence,**support}

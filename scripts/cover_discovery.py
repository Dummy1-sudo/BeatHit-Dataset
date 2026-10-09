"""Crash-resumable, multi-source BeatHit language-cover *candidate* discovery.

YouTube Data API v3: search.list uses a separate daily search-call quota;
channels.list and playlistItems.list use the general quota bucket (2026).
All requests, including errors, are conservatively debited before they are sent.

No search results are labelled as verified covers or verified sung languages.
SQLite is authoritative; CSV is a review/export interface, not the job database.
"""
from __future__ import annotations

import argparse
import csv
import email.utils
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache' / 'beathit-expansion'
DB_PATH = Path(os.getenv('BEATHIT_COVER_DB', str(CACHE / 'cover_discovery.sqlite3')))
YOUTUBE = 'https://www.googleapis.com/youtube/v3/'
ITUNES = 'https://itunes.apple.com/search'
TARGET = 10_000
SEARCH_DAILY = int(os.getenv('BEATHIT_YT_SEARCH_DAILY_LIMIT', '90'))
GENERAL_DAILY = int(os.getenv('BEATHIT_YT_GENERAL_DAILY_LIMIT', '9000'))
ITUNES_DELAY = float(os.getenv('BEATHIT_ITUNES_REQUEST_INTERVAL', '2.0'))
YOUTUBE_DELAY = float(os.getenv('BEATHIT_YOUTUBE_REQUEST_INTERVAL', '1.0'))
MAX_CHANNELS = int(os.getenv('BEATHIT_COVER_MAX_CHANNELS', '120'))
MAX_PLAYLIST_PAGES = int(os.getenv('BEATHIT_COVER_PLAYLIST_PAGES', '20'))
MAX_SEARCH_PAGES = int(os.getenv('BEATHIT_COVER_SEARCH_PAGES', '6'))
PER_LANGUAGE_SEARCH_DAILY = int(os.getenv('BEATHIT_YT_SEARCH_PER_LANGUAGE_DAILY', '9'))

# No language is inferred from the uploader's country or the search term.
LANGUAGES = {
    'russian': ('ru', 'UA', ['кавер песня', 'кавер на русском', 'перепевка']),
    'japanese': ('ja', 'JP', ['歌ってみた', 'カバー曲', '日本語カバー']),
    'german': ('de', 'DE', ['Coverversion deutsch', 'deutsche Version cover', 'Lied cover']),
    'afrikaans': ('af', 'ZA', ['Afrikaanse cover', 'Afrikaanse weergawe', 'Afrikaans cover song']),
    'korean': ('ko', 'KR', ['노래 커버', '커버곡', '한국어 커버']),
    'chinese': ('zh', 'TW', ['翻唱歌曲', '中文翻唱', '歌曲翻唱']),
    'ukrainian': ('uk', 'UA', ['кавер українською', 'український кавер', 'переспів']),
    'swedish': ('sv', 'SE', ['svensk coverlåt', 'svensk version cover', 'cover på svenska']),
    'norwegian': ('no', 'NO', ['norsk coverlåt', 'norsk versjon cover', 'cover på norsk']),
    'indonesian': ('id', 'ID', ['cover lagu indonesia', 'cover akustik indonesia', 'cover lagu barat versi indonesia']),
}
COVER_RE = re.compile(r'\bcover\b|\bcovered\b|\btribute\b|\bacoustic version\b|'
                      r'кавер|перепев|歌ってみた|カバー|커버|翻唱|翻唱|翻唱|'
                      r'coverlåt|coverversie|weergawe|переспів', re.I)
INVALID_RE = re.compile(r'\b(karaoke|instrumental|tutorial|reaction|nightcore|sped up|slowed|remix)\b', re.I)
VIDEO_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
CHANNEL_ID_RE = re.compile(r'^UC[A-Za-z0-9_-]{22}$')
FIELDS = ['youtube_id', 'video_title', 'channel', 'description', 'url', 'query',
          'language_target', 'discovered_utc', 'review_status', 'original_title',
          'original_artist', 'language_verified', 'cover_verified', 'source',
          'source_id', 'cover_evidence_url', 'language_evidence_url', 'hint_score']


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp() -> float:
    return time.time()


def pacific_midnight() -> float:
    try:
        tz = ZoneInfo('America/Los_Angeles')
    except ZoneInfoNotFoundError:
        raise RuntimeError('Pacific time-zone data unavailable. Install the tzdata Python package.')
    now = utc_now().astimezone(tz)
    return (datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=tz)
            .astimezone(timezone.utc).timestamp() + 60)


def quota_day() -> str:
    try:
        return utc_now().astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()
    except ZoneInfoNotFoundError:
        raise RuntimeError('Install tzdata for reliable YouTube quota resets.')


def _norm(value: str) -> str:
    s = unicodedata.normalize('NFKC', str(value)).casefold()
    return re.sub(r'\s+', ' ', s).strip()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=40, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=40000')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS candidates (
      language TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL,
      youtube_id TEXT NOT NULL DEFAULT '', video_title TEXT NOT NULL,
      channel TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
      url TEXT NOT NULL DEFAULT '', query TEXT NOT NULL DEFAULT '',
      discovered_utc TEXT NOT NULL, review_status TEXT NOT NULL DEFAULT 'unverified',
      original_title TEXT NOT NULL DEFAULT '', original_artist TEXT NOT NULL DEFAULT '',
      language_verified TEXT NOT NULL DEFAULT '', cover_verified TEXT NOT NULL DEFAULT '',
      cover_evidence_url TEXT NOT NULL DEFAULT '', language_evidence_url TEXT NOT NULL DEFAULT '',
      hint_score INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(language, source, source_id)
    );
    CREATE TABLE IF NOT EXISTS jobs (
      id INTEGER PRIMARY KEY, language TEXT NOT NULL, source TEXT NOT NULL,
      query TEXT NOT NULL, cursor TEXT NOT NULL DEFAULT '', pages INTEGER NOT NULL DEFAULT 0,
      priority INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
      locked_until REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
      done INTEGER NOT NULL DEFAULT 0, last_yield INTEGER NOT NULL DEFAULT 0,
      UNIQUE(language, source, query)
    );
    CREATE INDEX IF NOT EXISTS job_queue ON jobs(language,done,next_at,priority);
    CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS service_limits (
      service TEXT PRIMARY KEY, day TEXT NOT NULL DEFAULT '', used INTEGER NOT NULL DEFAULT 0,
      next_at REAL NOT NULL DEFAULT 0, last_request REAL NOT NULL DEFAULT 0,
      failures INTEGER NOT NULL DEFAULT 0
    );
    ''')
    return db


def _csv_path(language: str) -> Path:
    return ROOT / 'data' / 'language_covers' / language / f'{language}_cover_candidates.csv'


def import_legacy(db: sqlite3.Connection, language: str) -> int:
    """Preserve old candidates AND manual review edits in the previous CSV."""
    path = _csv_path(language)
    if not path.exists():
        return 0
    imported = 0
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            src = (row.get('source') or 'youtube').strip()
            sid = (row.get('source_id') or row.get('youtube_id') or '').strip()
            if not sid:
                continue
            source_lang = (row.get('language_target') or LANGUAGES[language][0]).strip()
            entry = {
                'youtube_id': row.get('youtube_id') or (sid if src == 'youtube' else ''),
                'video_title': row.get('video_title') or '', 'channel': row.get('channel') or '',
                'description': row.get('description') or '', 'url': row.get('url') or '',
                'query': row.get('query') or '',
                'discovered_utc': row.get('discovered_utc') or utc_now().isoformat(),
                'review_status': row.get('review_status') or 'unverified',
                'original_title': row.get('original_title') or '',
                'original_artist': row.get('original_artist') or '',
                'language_verified': row.get('language_verified') or '',
                'cover_verified': row.get('cover_verified') or '',
                'cover_evidence_url': row.get('cover_evidence_url') or '',
                'language_evidence_url': row.get('language_evidence_url') or '',
                'hint_score': int(row.get('hint_score') or 0),
            }
            if not entry['video_title']:
                continue
            imported += _insert_candidate(db, language, src, sid, entry)
            # Manual changes in the CSV take precedence when evidence is explicit.
            if row.get('review_status') in {'verified', 'rejected', 'needs_review'}:
                db.execute('''UPDATE candidates SET review_status=?, original_title=?,
                    original_artist=?, cover_verified=?, language_verified=?,
                    cover_evidence_url=?, language_evidence_url=?
                    WHERE language=? AND source=? AND source_id=?''',
                    (entry['review_status'], entry['original_title'], entry['original_artist'],
                     entry['cover_verified'], entry['language_verified'],
                     entry['cover_evidence_url'], entry['language_evidence_url'],
                     language, src, sid))
    return imported


def _insert_candidate(db: sqlite3.Connection, language: str, source: str, sid: str, values: dict) -> int:
    columns = ['language', 'source', 'source_id', 'youtube_id', 'video_title', 'channel',
               'description', 'url', 'query', 'discovered_utc', 'review_status',
               'original_title', 'original_artist', 'language_verified', 'cover_verified',
               'cover_evidence_url', 'language_evidence_url', 'hint_score']
    data = [language, source, sid] + [values.get(k, '') for k in columns[3:]]
    cur = db.execute(f"INSERT OR IGNORE INTO candidates ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", data)
    return max(cur.rowcount, 0)


def _seed(db: sqlite3.Connection, language: str, youtube_enabled: bool) -> None:
    lang, country, terms = LANGUAGES[language]
    # Broader search groups avoid repeatedly querying one generic phrase.
    modifiers = ['', ' acoustic', ' live', ' indie', ' duet', ' 2025', ' 2024']
    for idx, term in enumerate(terms):
        for modidx, mod in enumerate(modifiers):
            if youtube_enabled:
                _job(db, language, 'yt_search', term + mod, priority=40 - modidx * 3 - idx)
        for term2 in (term, f'{term} acoustic', f'{term} tribute'):
            _job(db, language, 'itunes', term2, priority=60 - idx)


def _job(db: sqlite3.Connection, lang: str, source: str, query: str, priority: int = 0) -> None:
    db.execute('''INSERT OR IGNORE INTO jobs(language,source,query,priority)
                  VALUES(?,?,?,?)''', (lang, source, query, priority))


def _migrate_state(db: sqlite3.Connection, language: str) -> None:
    """Resume from the pagination progress of the previous YouTube collector."""
    marker = 'migrated-cover-discovery-v2:' + language
    if db.execute('SELECT 1 FROM settings WHERE name=?', (marker,)).fetchone():
        return
    path = CACHE / f'cover_discovery_{language}.json'
    if path.exists():
        try:
            legacy = json.loads(path.read_text(encoding='utf-8'))
            index = max(0, int(legacy.get('query_index') or 0))
            tokens = legacy.get('page_tokens') or {}
            for i, term in enumerate(LANGUAGES[language][2]):
                if i < index:
                    db.execute("UPDATE jobs SET done=1 WHERE language=? AND source='yt_search' AND query=?",
                               (language, term))
                elif i == index and tokens.get(str(i)):
                    db.execute("UPDATE jobs SET cursor=? WHERE language=? AND source='yt_search' AND query=?",
                               (str(tokens[str(i)]), language, term))
        except (ValueError, TypeError, OSError):
            pass
    db.execute('INSERT OR IGNORE INTO settings(name,value) VALUES (?,?)', (marker, '1'))


def _limit(db: sqlite3.Connection, service: str, daily_limit: int, interval: float, reserve: bool = True) -> float:
    """Reserve a call atomically; return wait seconds if unavailable.

    Must be invoked inside BEGIN IMMEDIATE. Never refunds: errors also cost quota.
    """
    today = quota_day() if service.startswith('yt_') else utc_now().date().isoformat()
    row = db.execute('SELECT * FROM service_limits WHERE service=?', (service,)).fetchone()
    now = timestamp()
    if row is None:
        db.execute('INSERT INTO service_limits(service,day) VALUES (?,?)', (service, today))
        row = db.execute('SELECT * FROM service_limits WHERE service=?', (service,)).fetchone()
    used = int(row['used']) if row['day'] == today else 0
    next_at = float(row['next_at'])
    # A former day's quota block expires when the day rolls over.
    if row['day'] != today:
        next_at = min(next_at, now)
    if used >= daily_limit:
        return max(1.0, pacific_midnight() - now) if service.startswith('yt_') else 3600.0
    if next_at > now:
        return next_at - now
    if float(row['last_request']) + interval > now:
        return float(row['last_request']) + interval - now
    if reserve:
        db.execute('''UPDATE service_limits SET day=?, used=?, last_request=?,next_at=?, failures=? WHERE service=?''',
                   (today, used + 1, now, 0, 0 if row['day'] != today else row['failures'], service))
    return 0.0


def _cooldown(db: sqlite3.Connection, service: str, delay: float, *, rate_limited: bool = False) -> None:
    row = db.execute('SELECT failures FROM service_limits WHERE service=?', (service,)).fetchone()
    failures = int(row[0]) + 1 if row else 1
    if rate_limited and failures >= 3 and service.startswith('yt_'):
        delay = max(delay, pacific_midnight() - timestamp())
    db.execute('''UPDATE service_limits SET next_at=MAX(next_at,?), failures=? WHERE service=?''',
               (timestamp() + max(1, delay), failures, service))


def _healthy(db: sqlite3.Connection, service: str) -> None:
    db.execute('UPDATE service_limits SET failures=0 WHERE service=?', (service,))


def _service(source: str) -> str:
    return 'yt_search' if source == 'yt_search' else ('yt_general' if source.startswith('yt_') else 'itunes')


def _claim(db: sqlite3.Connection, language: str, youtube_enabled: bool, allow_search: bool = True) -> tuple[dict | None, float]:
    """Lease one due job and charge its actual quota bucket, shared by workers."""
    db.execute('BEGIN IMMEDIATE')
    try:
        now = timestamp()
        jobs = db.execute('''SELECT * FROM jobs WHERE language=? AND done=0
            AND next_at<=? AND locked_until<=? ORDER BY priority DESC, pages ASC, id ASC LIMIT 20000''',
            (language, now, now)).fetchall()
        min_wait = 300.0
        selected = None
        service_waits: dict[str, float] = {}
        for job in jobs:
            src = job['source']
            if src.startswith('yt_') and not youtube_enabled:
                continue
            if src == 'yt_search' and not allow_search:
                continue
            service = _service(src)
            daily = SEARCH_DAILY if service == 'yt_search' else GENERAL_DAILY if service == 'yt_general' else 100_000
            interval = YOUTUBE_DELAY if src.startswith('yt_') else ITUNES_DELAY
            # Check BOTH independent quotas before charging either one.
            if service in service_waits:
                min_wait = min(min_wait, service_waits[service])
                continue
            local_bucket = 'yt_search_language:' + language if src == 'yt_search' else None
            local_wait = (_limit(db, local_bucket, PER_LANGUAGE_SEARCH_DAILY, interval, reserve=False)
                          if local_bucket else 0)
            global_wait = _limit(db, service, daily, interval, reserve=False)
            wait = max(local_wait, global_wait)
            if wait > 0:
                min_wait = min(min_wait, wait)
                service_waits[service] = wait
                continue
            if local_bucket:
                _limit(db, local_bucket, PER_LANGUAGE_SEARCH_DAILY, interval)
            _limit(db, service, daily, interval)
            db.execute('UPDATE jobs SET locked_until=? WHERE id=?', (now + 90, job['id']))
            selected = dict(job)
            break
        if selected is None:
            # All jobs might be leased or waiting on cooldown, not exhausted.
            next_row = db.execute('''SELECT MIN(CASE WHEN locked_until>next_at THEN locked_until ELSE next_at END)
                FROM jobs WHERE language=? AND done=0
                  AND (? OR source NOT LIKE 'yt_%')
                  AND (? OR source != 'yt_search')
                  AND (locked_until>? OR next_at>?)''',
                (language, int(youtube_enabled), int(allow_search), now, now)).fetchone()[0]
            if next_row is not None:
                min_wait = min(min_wait, max(1, float(next_row) - now))
        db.execute('COMMIT')
        return selected, min_wait
    except BaseException:
        db.execute('ROLLBACK')
        raise


def _error_detail(response: httpx.Response) -> tuple[str, str]:
    try:
        payload = response.json()
        err = payload.get('error') or {}
        reasons = [e.get('reason') or '' for e in (err.get('errors') or []) if isinstance(e, dict)]
        return str(err.get('message') or '')[:250], ','.join(reasons).casefold()
    except (ValueError, TypeError, AttributeError):
        return '', ''


class RequestFailure(Exception):
    def __init__(self, status: int, reason: str = '', retry_after: str = ''):
        self.status = status
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(f'HTTP {status}: {reason[:100]}')  # Never print URL or API key.


def _get(client: httpx.Client, url: str, params: dict, *, key: str = '') -> dict:
    if key:
        params = {**params, 'key': key}
    response = client.get(url, params=params)
    if response.status_code >= 400:
        message, reason = _error_detail(response)
        raise RequestFailure(response.status_code, reason or message, response.headers.get('Retry-After', ''))
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError('API returned a non-object JSON body')
    return data


def _candidate(source: str, source_id: str, title: str, channel: str, description: str,
               url: str, query: str, lang: str) -> dict:
    strength = int(bool(COVER_RE.search(title))) * 2 + int(bool(COVER_RE.search(description)))
    return dict(youtube_id=source_id if source == 'youtube' else '', video_title=title.strip(),
                channel=channel.strip(), description=description[:2000], url=url, query=query,
                discovered_utc=utc_now().isoformat(), review_status='unverified',
                original_title='', original_artist='', language_verified='', cover_verified='',
                cover_evidence_url='', language_evidence_url='', hint_score=strength)


def _page(client: httpx.Client, job: dict, lang: str, country: str, api_key: str) -> tuple[list[dict], str, list[str]]:
    """One API response; return candidates, next page, channel IDs for cheap expansion."""
    src, query, cursor = job['source'], job['query'], job['cursor']
    result: list[dict] = []
    channels: list[str] = []
    if src == 'yt_search':
        params = dict(part='snippet', type='video', maxResults=50,
                      q=query, relevanceLanguage=lang)
        if cursor:
            params['pageToken'] = cursor
        data = _get(client, YOUTUBE + 'search', params, key=api_key)
        for item in data.get('items') or []:
            vid = str((item.get('id') or {}).get('videoId') or '')
            snippet = item.get('snippet') or {}
            title = str(snippet.get('title') or '')
            if not VIDEO_ID_RE.fullmatch(vid) or not title:
                continue
            channel = str(snippet.get('channelTitle') or '')
            desc = str(snippet.get('description') or '')
            if INVALID_RE.search(title):
                continue
            result.append(dict(source='youtube', source_id=vid,
                               payload=_candidate('youtube', vid, title, channel, desc,
                                                  f'https://www.youtube.com/watch?v={vid}', query, lang)))
            channel_id = str(snippet.get('channelId') or '')
            if CHANNEL_ID_RE.fullmatch(channel_id) and COVER_RE.search(title + ' ' + desc):
                channels.append(channel_id)
        return result, str(data.get('nextPageToken') or ''), channels
    if src == 'yt_channel':
        data = _get(client, YOUTUBE + 'channels', {'part': 'contentDetails', 'id': query}, key=api_key)
        for item in data.get('items') or []:
            pid = str((((item.get('contentDetails') or {}).get('relatedPlaylists') or {}).get('uploads') or ''))
            if pid:
                channels.append(pid)
        return result, '', channels
    if src == 'yt_playlist':
        params = dict(part='snippet', playlistId=query, maxResults=50)
        if cursor:
            params['pageToken'] = cursor
        data = _get(client, YOUTUBE + 'playlistItems', params, key=api_key)
        for item in data.get('items') or []:
            snippet = item.get('snippet') or {}
            vid = str((snippet.get('resourceId') or {}).get('videoId') or '')
            title = str(snippet.get('title') or '')
            desc = str(snippet.get('description') or '')
            if (not VIDEO_ID_RE.fullmatch(vid) or not title or INVALID_RE.search(title)
                    or not COVER_RE.search(title + ' ' + desc)):
                continue
            channel = str(snippet.get('videoOwnerChannelTitle') or snippet.get('channelTitle') or '')
            result.append(dict(source='youtube', source_id=vid,
                               payload=_candidate('youtube', vid, title, channel, desc,
                                                  f'https://www.youtube.com/watch?v={vid}', 'channel:' + query, lang)))
        return result, str(data.get('nextPageToken') or ''), []
    if src == 'itunes':
        data = _get(client, ITUNES, {'entity': 'song', 'limit': 200, 'country': country, 'term': query})
        for item in data.get('results') or []:
            sid = str(item.get('trackId') or '')
            title = str(item.get('trackName') or '').strip()
            artist = str(item.get('artistName') or '').strip()
            album = str(item.get('collectionName') or '').strip()
            # Apple Search API query relevance alone is not proof of a cover.
            if not sid.isdigit() or not title or not artist or INVALID_RE.search(title):
                continue
            if not COVER_RE.search(title + ' ' + album):
                continue
            data_row = _candidate('itunes', sid, title, artist, album,
                                  str(item.get('trackViewUrl') or ''), query, lang)
            result.append(dict(source='itunes', source_id=sid, payload=data_row))
        return result, '', []
    raise ValueError(f'Unknown job source: {src}')


def _finish(db: sqlite3.Connection, job: dict, rows: list[dict], cursor: str,
            expansions: list[str], language: str) -> int:
    db.execute('BEGIN IMMEDIATE')
    try:
        added = sum(_insert_candidate(db, language, r['source'], r['source_id'], r['payload']) for r in rows)
        pages = int(job['pages']) + 1
        max_pages = MAX_SEARCH_PAGES if job['source'] == 'yt_search' else MAX_PLAYLIST_PAGES if job['source'] == 'yt_playlist' else 1
        exhausted = not cursor or pages >= max_pages
        # Discover upload playlists at 1 unit/page instead of 100 search units in old quota model.
        if job['source'] == 'yt_channel':
            for playlist in expansions:
                _job(db, language, 'yt_playlist', playlist, priority=85)
        elif job['source'] == 'yt_search':
            known = db.execute("SELECT COUNT(*) FROM jobs WHERE language=? AND source='yt_channel'", (language,)).fetchone()[0]
            for channel in dict.fromkeys(expansions):
                if known >= MAX_CHANNELS:
                    break
                old = db.total_changes
                _job(db, language, 'yt_channel', channel, priority=90)
                known += int(db.total_changes > old)
        db.execute('''UPDATE jobs SET cursor=?, pages=?, done=?, locked_until=0,
            attempts=0, last_yield=? WHERE id=?''',
            (cursor if not exhausted else '', pages, int(exhausted), added, job['id']))
        _healthy(db, _service(job['source']))
        db.execute('COMMIT')
        return added
    except BaseException:
        db.execute('ROLLBACK')
        raise


def _fail(db: sqlite3.Connection, job: dict, exc: Exception) -> str:
    attempts = int(job['attempts']) + 1
    service = _service(job['source'])
    now = timestamp()
    reason = 'temporary_error'
    delay = min(3600, 30 * (2 ** min(attempts - 1, 7)))
    if isinstance(exc, RequestFailure):
        message = exc.reason.casefold()
        if ('quotaexceeded' in message or 'dailylimitexceeded' in message
                or 'dailylimit' in message):
            delay = max(60, pacific_midnight() - now)
            reason = 'daily_quota_exhausted'
        elif exc.status == 429 or 'ratelimitexceeded' in message:
            reason = 'rate_limited'
            try:
                delay = max(delay, min(86400, int(exc.retry_after)))
            except ValueError:
                try:
                    retry_date = email.utils.parsedate_to_datetime(exc.retry_after)
                    delay = max(delay, min(86400, retry_date.timestamp() - now))
                except (TypeError, ValueError, OverflowError):
                    pass
            if attempts >= 4:
                delay = max(delay, pacific_midnight() - now)
        elif exc.status == 400 and ('pagetoken' in message or 'page token' in message or 'invalidpage' in message):
            db.execute('''UPDATE jobs SET cursor='', locked_until=0, attempts=0,
                         next_at=? WHERE id=?''', (now + 5, job['id']))
            return 'expired_page_token_restart'
        elif exc.status in (401, 403) and ('keyinvalid' in message or 'accessnotconfigured' in message
                                           or 'forbidden' in message or 'iprefererblocked' in message):
            # Configuration errors are not transient. Pause instead of retry-spamming.
            delay = 86400
            reason = 'api_key_or_permission_error'
        elif exc.status in (400, 401, 403, 404):
            db.execute('UPDATE jobs SET done=1, locked_until=0 WHERE id=?', (job['id'],))
            return f'permanent_http_{exc.status}'
    elif isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        reason = 'network_error'
    db.execute('''UPDATE jobs SET locked_until=0, attempts=?, next_at=? WHERE id=?''',
               (attempts, now + delay, job['id']))
    if reason in ('daily_quota_exhausted', 'rate_limited'):
        _cooldown(db, service, delay, rate_limited=reason == 'rate_limited')
    return reason


def export(db: sqlite3.Connection, language: str) -> int:
    rows = db.execute('''SELECT * FROM candidates WHERE language=?
                         ORDER BY hint_score DESC, discovered_utc ASC''', (language,)).fetchall()
    path = _csv_path(language)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with temp.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            payload = dict(row)
            payload['language_target'] = LANGUAGES[language][0]
            writer.writerow({name: payload.get(name, '') for name in FIELDS})
    os.replace(temp, path)
    return len(rows)


def status(db: sqlite3.Connection, language: str) -> dict:
    candidates = db.execute('SELECT COUNT(*) FROM candidates WHERE language=?', (language,)).fetchone()[0]
    reviewed = db.execute("""SELECT COUNT(*) FROM candidates WHERE language=? AND
        review_status='verified' AND original_title<>'' AND original_artist<>'' AND
        cover_verified='yes' AND language_verified='yes' AND
        cover_evidence_url<>'' AND language_evidence_url<>''""", (language,)).fetchone()[0]
    jobs = db.execute('''SELECT COUNT(*),SUM(CASE WHEN done=0 THEN 1 ELSE 0 END)
                         FROM jobs WHERE language=?''', (language,)).fetchone()
    waiting = db.execute('''SELECT MIN(next_at) FROM jobs WHERE language=? AND done=0 AND next_at>?''',
                         (language, timestamp())).fetchone()[0]
    return {'candidate_rows': candidates, 'reviewed_evidence_rows': reviewed,
            'jobs_total': jobs[0], 'jobs_remaining': jobs[1] or 0,
            'next_retry_utc': datetime.fromtimestamp(waiting, timezone.utc).isoformat() if waiting else None}


def discover(language: str, deadline: float, search_budget: int) -> dict:
    if language not in LANGUAGES:
        raise ValueError(f'Unknown language: {language}')
    api_key = os.getenv('YOUTUBE_API_KEY', '').strip()
    db = connect()
    searches = 0
    requests = 0
    new_candidates = 0
    failures: dict[str, int] = {}
    try:
        import_legacy(db, language)
        _seed(db, language, bool(api_key))
        _migrate_state(db, language)
        while time.monotonic() < deadline:
            job, wait = _claim(db, language, bool(api_key), searches < max(0, search_budget))
            if job is None:
                if 0 < wait < 35 and time.monotonic() + wait + 1 < deadline:
                    time.sleep(max(0.1, wait + 0.1))
                    continue
                break
            if job['source'] == 'yt_search':
                searches += 1
            requests += 1
            try:
                with httpx.Client(timeout=35, follow_redirects=True) as client:
                    found, cursor, expansions = _page(client, job, *LANGUAGES[language][:2], api_key)
                new_candidates += _finish(db, job, found, cursor, expansions, language)
            except (httpx.HTTPError, RequestFailure, ValueError, KeyError) as exc:
                reason = _fail(db, job, exc)
                failures[reason] = failures.get(reason, 0) + 1
                if reason == 'api_key_or_permission_error':
                    break
        count = export(db, language)
        progress = status(db, language)
        return {
            'target': TARGET, 'rows': 0, 'complete': False, 'candidate_rows': count,
            'new_candidates': new_candidates, 'requests_this_run': requests,
            'search_requests_this_run': searches, 'errors_this_run': failures,
            'notes': [f'{count} candidates (NOT verified) in {_csv_path(language).relative_to(ROOT)}',
                      f'{progress["jobs_remaining"]} queued unfinished discovery jobs',
                      'SQLite checkpoint: ' + str(DB_PATH),
                      'YouTube search, cheap channel uploads, and Apple Music/iTunes discovery.',
                      'Original and performed language must be verified before publication.',
                      'YouTube API ' + ('enabled' if api_key else 'disabled (YOUTUBE_API_KEY not set)'),
                      'errors=' + json.dumps(failures, sort_keys=True),
                      'next_retry_utc=' + str(progress['next_retry_utc'])],
            **progress,
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description='Run resumable BeatHit cover discovery workers')
    parser.add_argument('--language', choices=[*LANGUAGES, 'all'], default='all')
    parser.add_argument('--watch', action='store_true', help='Keep running; resume after limits reset')
    parser.add_argument('--status', action='store_true', help='Show saved progress without contacting APIs')
    parser.add_argument('--max-seconds-per-language', type=int, default=300)
    parser.add_argument('--searches-per-language', type=int, default=90)
    parser.add_argument('--poll-seconds', type=int, default=300)
    args = parser.parse_args()
    languages = list(LANGUAGES) if args.language == 'all' else [args.language]
    if args.status:
        db = connect()
        try:
            for language in languages:
                print(json.dumps({'language': language, **status(db, language)}, ensure_ascii=False))
        finally:
            db.close()
        return 0
    while True:
        for lang in languages:
            try:
                result = discover(lang, time.monotonic() + max(5, args.max_seconds_per_language),
                                  args.searches_per_language)
                print(json.dumps({'language': lang, **result}, ensure_ascii=False), flush=True)
            except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
                # A failed language should not prevent the rest from progressing.
                print(f'{lang}: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        if not args.watch:
            return 0
        time.sleep(max(30, args.poll_seconds))


if __name__ == '__main__':
    raise SystemExit(main())

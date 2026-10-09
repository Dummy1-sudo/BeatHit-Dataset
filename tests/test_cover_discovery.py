"""Offline tests: python -m unittest discover -s tests -p test_cover_discovery.py -v"""
import csv
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import cover_discovery as cd


class OfflineCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.patchers = [
            patch.object(cd, 'ROOT', self.base),
            patch.object(cd, 'CACHE', self.base / '.cache'),
            patch.object(cd, 'DB_PATH', self.base / '.cache' / 'cover.sqlite3'),
            patch.object(cd, 'YOUTUBE_DELAY', 0),
            patch.object(cd, 'ITUNES_DELAY', 0),
            patch.object(cd, 'SEARCH_DAILY', 90),
            patch.object(cd, 'PER_LANGUAGE_SEARCH_DAILY', 90),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_checkpoint_survives_restart(self):
        db = cd.connect()
        cd._job(db, 'norwegian', 'yt_search', 'cover på norsk', priority=1)
        job, wait = cd._claim(db, 'norwegian', True)
        self.assertEqual(job['source'], 'yt_search')
        vid = 'abcdefghijk'
        row = cd._candidate('youtube', vid, 'Norwegian cover', 'Test Channel',
                            'A cover recording', 'https://www.youtube.com/watch?v=' + vid,
                            'cover på norsk', 'no')
        cd._finish(db, job, [{'source': 'youtube', 'source_id': vid, 'payload': row}],
                   'NEXT_PAGE', [], 'norwegian')
        db.close()
        db = cd.connect()
        job, wait = cd._claim(db, 'norwegian', True)
        self.assertEqual(job['cursor'], 'NEXT_PAGE')
        self.assertEqual(cd.status(db, 'norwegian')['candidate_rows'], 1)
        cd._finish(db, job, [{'source': 'youtube', 'source_id': vid, 'payload': row}],
                   '', [], 'norwegian')
        self.assertEqual(cd.status(db, 'norwegian')['candidate_rows'], 1)
        self.assertEqual(cd.status(db, 'norwegian')['jobs_remaining'], 0)
        db.close()

    def test_legacy_csv_and_state_migration(self):
        path = cd._csv_path('norwegian')
        path.parent.mkdir(parents=True)
        with path.open('w', newline='', encoding='utf8') as f:
            w = csv.DictWriter(f, fieldnames=cd.FIELDS)
            w.writeheader()
            w.writerow({'youtube_id': 'abcdefghijk', 'video_title': 'Existing song',
                        'language_target': 'no', 'review_status': 'unverified'})
        cache = self.base / '.cache' / 'beathit-expansion'
        cache.mkdir(parents=True)
        # The previous collector keeps this JSON state.
        (self.base / '.cache' / 'cover_discovery_norwegian.json').write_text(
            json.dumps({'query_index': 1, 'page_tokens': {'1': 'PAGE_2'}}), encoding='utf8')
        db = cd.connect()
        self.assertEqual(cd.import_legacy(db, 'norwegian'), 1)
        cd._seed(db, 'norwegian', True)
        cd._migrate_state(db, 'norwegian')
        term1, term2 = cd.LANGUAGES['norwegian'][2][:2]
        previous = db.execute("SELECT * FROM jobs WHERE language='norwegian' AND source='yt_search' AND query=?", (term1,)).fetchone()
        current = db.execute("SELECT * FROM jobs WHERE language='norwegian' AND source='yt_search' AND query=?", (term2,)).fetchone()
        self.assertEqual(previous['done'], 1)
        self.assertEqual(current['cursor'], 'PAGE_2')
        self.assertEqual(cd.status(db, 'norwegian')['candidate_rows'], 1)
        db.close()

    def test_shared_daily_search_quota(self):
        with patch.object(cd, 'SEARCH_DAILY', 1):
            a = cd.connect()
            b = cd.connect()
            cd._job(a, 'norwegian', 'yt_search', 'query A')
            cd._job(a, 'indonesian', 'yt_search', 'query B')
            self.assertIsNotNone(cd._claim(a, 'norwegian', True)[0])
            claimed, delay = cd._claim(b, 'indonesian', True)
            self.assertIsNone(claimed)
            self.assertGreater(delay, 0)
            a.close()
            b.close()

    def test_repeated_429_causes_global_pause(self):
        db = cd.connect()
        cd._job(db, 'swedish', 'yt_search', 'q')
        job, _ = cd._claim(db, 'swedish', True)
        for i in range(3):
            cd._fail(db, {**job, 'attempts': i}, cd.RequestFailure(429, 'Too many requests'))
        service = db.execute("SELECT * FROM service_limits WHERE service='yt_search'").fetchone()
        self.assertGreater(service['next_at'], cd.timestamp() + 600)
        self.assertEqual(service['failures'], 3)
        db.close()

    def test_invalid_page_token_is_restarted(self):
        db = cd.connect()
        cd._job(db, 'korean', 'yt_search', 'cover music')
        job, _ = cd._claim(db, 'korean', True)
        db.execute("UPDATE jobs SET cursor='EXPIRED' WHERE id=?", (job['id'],))
        self.assertEqual(cd._fail(db, job, cd.RequestFailure(400, 'invalidPageToken')),
                         'expired_page_token_restart')
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()
        self.assertEqual(row['cursor'], '')
        self.assertEqual(row['done'], 0)
        db.close()

    def test_channel_expansion_uses_general_quota(self):
        db = cd.connect()
        cd._job(db, 'japanese', 'yt_search', 'カバー')
        job, _ = cd._claim(db, 'japanese', True)
        cd._finish(db, job, [], '', ['UC' + 'a' * 22], 'japanese')
        channel = db.execute("SELECT * FROM jobs WHERE source='yt_channel'").fetchone()
        self.assertIsNotNone(channel)
        claimed, _ = cd._claim(db, 'japanese', True)
        self.assertEqual(claimed['source'], 'yt_channel')
        cd._finish(db, claimed, [], '', ['UU' + 'b' * 22], 'japanese')
        playlist = db.execute("SELECT * FROM jobs WHERE source='yt_playlist'").fetchone()
        self.assertIsNotNone(playlist)
        self.assertEqual(db.execute("SELECT used FROM service_limits WHERE service='yt_search'").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT used FROM service_limits WHERE service='yt_general'").fetchone()[0], 1)
        db.close()

    def test_no_key_skips_old_youtube_jobs_without_busy_loop(self):
        db = cd.connect()
        cd._job(db, 'indonesian', 'yt_search', 'old-unfinished-job')
        job, wait = cd._claim(db, 'indonesian', False)
        self.assertIsNone(job)
        self.assertGreaterEqual(wait, 100)
        db.close()

    def test_search_quota_pause_does_not_spin(self):
        with patch.object(cd, 'SEARCH_DAILY', 1):
            db = cd.connect()
            cd._job(db, 'indonesian', 'yt_search', 'query-1')
            cd._job(db, 'indonesian', 'yt_search', 'query-2')
            self.assertIsNotNone(cd._claim(db, 'indonesian', True)[0])
            job, wait = cd._claim(db, 'indonesian', True)
            self.assertIsNone(job)
            self.assertGreater(wait, 60)
            db.close()

    def test_no_key_discover_still_uses_itunes(self):
        def fake_page(client, job, lang, country, api_key):
            if job['source'] != 'itunes':
                raise AssertionError('YouTube should not be scheduled without a key')
            return ([{'source': 'itunes', 'source_id': '1234',
                      'payload': cd._candidate('itunes', '1234', 'Cover Version', 'Artist',
                                               '', 'https://music.apple.com/1234', job['query'], lang)}], '', [])
        with patch.dict(os.environ, {'YOUTUBE_API_KEY': ''}), patch.object(cd, '_page', fake_page):
            result = cd.discover('indonesian', time.monotonic() + 2.5, 2)
            self.assertEqual(result['candidate_rows'], 1)
            self.assertEqual(result['rows'], 0)
            self.assertEqual(result['search_requests_this_run'], 0)
            self.assertTrue(cd._csv_path('indonesian').exists())

    def test_http_failures_hide_api_key(self):
        class StubResponse:
            status_code = 429
            headers = {}
            def json(self):
                return {'error': {'message': 'Too many requests', 'errors': [{'reason': 'rateLimitExceeded'}]}}
        class StubClient:
            def get(self, *a, **kw):
                return StubResponse()
        with self.assertRaises(cd.RequestFailure) as failure:
            cd._get(StubClient(), cd.YOUTUBE + 'search', {'q': 'cover'}, key='PRIVATE_TOKEN')
        self.assertNotIn('PRIVATE_TOKEN', str(failure.exception))

    def test_review_claims_not_automatically_verified(self):
        db = cd.connect()
        cd._insert_candidate(db, 'norwegian', 'youtube', 'abcdefghijk',
                             cd._candidate('youtube', 'abcdefghijk', 'Norsk cover', 'Channel',
                                           'description', '', '', 'no'))
        self.assertEqual(cd.status(db, 'norwegian')['reviewed_evidence_rows'], 0)
        self.assertEqual(cd.export(db, 'norwegian'), 1)
        db.close()


if __name__ == '__main__':
    unittest.main()

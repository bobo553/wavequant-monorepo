from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache
from wavequant.domain.models.model import Bar
from wavequant.domain.models.validated_bars import ValidatedBars
from wavequant.domain.market_state.wave_strength import validate_prefix


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cache=ArtifactCache(self.tmp.name)

    def test_roundtrip_restart_and_namespace_inputs_isolation(self):
        key=dict(symbol='sh.600519',asof='2026-09-07',price_basis='raw')
        value=dict(points=[dict(value=12.3,kind='H',known_at=5)],中文='折线')
        self.assertTrue(self.cache.put('geometry',key,value))
        restarted=ArtifactCache(self.tmp.name)
        self.assertEqual(restarted.get('geometry',key),value)
        for changed in (dict(key,asof='2026-09-06'),dict(key,price_basis='adjusted'),dict(key,symbol='sh.600009')):
            self.assertIsNone(restarted.get('geometry',changed))
        self.assertIsNone(restarted.get('signals',key))

    def test_corruption_is_miss_and_recompute_can_replace(self):
        self.cache.put('test',{},dict(a=1))
        with closing(sqlite3.connect(self.cache.path)) as db,db:
            db.execute("UPDATE artifacts SET payload=X'0001'")
        with self.assertLogs(level='WARNING'):
            self.assertIsNone(self.cache.get('test',{}))
        self.cache.put('test',{},dict(a=2))
        self.assertEqual(self.cache.get('test',{}),dict(a=2))

    def test_concurrent_atomic_writes_and_disk_failure(self):
        def task(i):
            cache=ArtifactCache(self.tmp.name)
            cache.put('test',i,dict(rows=list(range(i))))
            return cache.get('test',i)
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(task,range(20))),[dict(rows=list(range(i))) for i in range(20)])
        with patch.object(self.cache,'_connect',side_effect=sqlite3.OperationalError('disk full')):
            with self.assertLogs(level='WARNING'):
                self.assertFalse(self.cache.put('test',0,{}))
                self.assertIsNone(self.cache.get('test',0))

    def test_logical_size_budget_evicts_old_disposable_data(self):
        cache=ArtifactCache(self.tmp.name,max_bytes=250)
        for i in range(10):cache.put('test',i,dict(i=i))
        with closing(sqlite3.connect(cache.path)) as db:
            self.assertLessEqual(db.execute('SELECT SUM(size) FROM artifacts').fetchone()[0],250)
        self.assertEqual(cache.get('test',9),dict(i=9))

    def test_busy_writer_does_not_block_interactive_result_for_thirty_seconds(self):
        self.cache.put('seed',{},dict(ok=True))
        locked=sqlite3.connect(self.cache.path,timeout=1,isolation_level=None)
        self.addCleanup(locked.close)
        locked.execute('BEGIN IMMEDIATE')
        started=time.perf_counter()
        with self.assertLogs(level='WARNING'):
            self.assertFalse(self.cache.put('interactive',{},dict(valid=True)))
        self.assertLess(time.perf_counter()-started,2)
        locked.execute('ROLLBACK')
        self.assertTrue(self.cache.put('interactive',{},dict(valid=True)))
        self.assertEqual(self.cache.get('interactive',{}),dict(valid=True))

    def assert_usage_matches_artifacts(self):
        with closing(sqlite3.connect(self.cache.path)) as db:
            actual = db.execute('SELECT COALESCE(SUM(size),0) FROM artifacts').fetchone()[0]
            self.assertEqual(db.execute('SELECT total_bytes FROM artifact_usage WHERE id=1').fetchone()[0], actual)
            self.assertEqual(db.execute('SELECT key,size FROM artifact_sizes ORDER BY key').fetchall(),
                             db.execute('SELECT key,size FROM artifacts ORDER BY key').fetchall())

    def test_legacy_database_initializes_usage_once_and_restart_writes_do_not_sum(self):
        with closing(sqlite3.connect(self.cache.path)) as db, db:
            db.execute('CREATE TABLE artifacts (key TEXT PRIMARY KEY, namespace TEXT NOT NULL, '
                       'payload BLOB NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL, created REAL NOT NULL)')
            db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?)', ('old', 'geometry', b'old', 'digest', 3, 0))
        self.assertTrue(self.cache.put('test', 0, dict(value=0)))
        self.assert_usage_matches_artifacts()
        statements = []
        real_connect = sqlite3.connect

        def traced_connect(*args, **kwargs):
            db = real_connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        with patch('wavequant.infrastructure.persistence.artifact_cache.sqlite3.connect', side_effect=traced_connect):
            restarted = ArtifactCache(self.tmp.name)
            self.assertTrue(restarted.put('test', 1, dict(value=1)))
            self.assertTrue(restarted.put('test', 1, dict(value=list(range(50)))))
        self.assertFalse(any('SUM(' in statement.upper() for statement in statements), statements)
        self.assertEqual(restarted.get('test', 1), dict(value=list(range(50))))
        self.assert_usage_matches_artifacts()

    def test_usage_tracks_legacy_replace_update_delete_and_rollback(self):
        self.assertTrue(self.cache.put('seed', 0, dict(ok=True)))
        for recursive in (0, 1):
            with self.subTest(recursive_triggers=recursive):
                with closing(sqlite3.connect(self.cache.path)) as db:
                    db.execute(f'PRAGMA recursive_triggers={recursive}')
                    for size in (20, 35, 7):
                        with db:
                            db.execute('INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?)',
                                       ('legacy', 'test', b'x' * size, 'digest', size, 0))
                        self.assert_usage_matches_artifacts()
                    with db:
                        db.execute('UPDATE artifacts SET size=11 WHERE key=?', ('legacy',))
                    self.assert_usage_matches_artifacts()
                    db.execute('INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?)',
                               ('legacy', 'test', b'changed', 'digest', 100, 0))
                    db.rollback()
                    self.assert_usage_matches_artifacts()
                    with db:
                        db.execute('DELETE FROM artifacts WHERE key=?', ('legacy',))
                    self.assert_usage_matches_artifacts()

    def test_usage_remains_exact_across_engine_cleanup_and_eviction(self):
        self.assertTrue(self.cache.put('backtest', 0, dict(old=True)))
        self.assertTrue(self.cache.put('geometry', 0, dict(points=[1, 2, 3])))
        self.assertEqual(self.cache.clear_backtests_for_engine('new-engine'), 1)
        self.assert_usage_matches_artifacts()
        self.assertEqual(self.cache.get('geometry', 0), dict(points=[1, 2, 3]))
        limited = ArtifactCache(self.tmp.name, max_bytes=250)
        for index in range(10):
            self.assertTrue(limited.put('test', index, dict(value=index)))
            self.assert_usage_matches_artifacts()
            with closing(sqlite3.connect(self.cache.path)) as db:
                self.assertLessEqual(db.execute('SELECT total_bytes FROM artifact_usage WHERE id=1').fetchone()[0], 250)
        self.assertEqual(limited.get('test', 9), dict(value=9))

    def test_usage_is_shared_across_concurrent_cache_instances(self):
        def task(index):
            cache = ArtifactCache(self.tmp.name)
            return cache.put('shared', index % 4, dict(value=index))

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(pool.map(task, range(24))))
        self.assert_usage_matches_artifacts()


class ValidatedBarsTests(unittest.TestCase):
    def test_immutable_safe_slices_and_invalid_inputs(self):
        bars=[Bar(datetime(2020,1,1)+timedelta(days=i),'x',10,12,9,11,100) for i in range(5)]
        seq=ValidatedBars(bars)
        with self.assertRaises(FrozenInstanceError):seq._values=()
        bars[0]=replace(bars[0],high=5)
        self.assertEqual(seq[0].high,12)
        self.assertIsInstance(seq[1:3],ValidatedBars)
        with patch('wavequant.domain.market_state.wave_strength._validate_bar',side_effect=AssertionError('repeated validation')):
            validate_prefix(seq[1:3],symbol='x',end=1)
        for invalid in (bars,list(reversed(seq)),[seq[0],replace(seq[1],symbol='y')]):
            with self.assertRaises(ValueError):ValidatedBars(invalid)
        with self.assertRaises(ValueError):validate_prefix(seq,symbol='y',end=1)
        with self.assertRaises(ValueError):validate_prefix(seq,symbol='x',end=5)
        with self.assertRaises(ValueError):seq[::-1]
        self.assertIsInstance(seq[:1]+seq[2:3],tuple)

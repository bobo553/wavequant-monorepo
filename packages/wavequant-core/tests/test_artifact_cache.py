from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from wavequant.artifact_cache import ArtifactCache
from wavequant.model import Bar
from wavequant.validated_bars import ValidatedBars
from wavequant.wave_strength import validate_prefix


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


class ValidatedBarsTests(unittest.TestCase):
    def test_immutable_safe_slices_and_invalid_inputs(self):
        bars=[Bar(datetime(2020,1,1)+timedelta(days=i),'x',10,12,9,11,100) for i in range(5)]
        seq=ValidatedBars(bars)
        with self.assertRaises(FrozenInstanceError):seq._values=()
        bars[0]=replace(bars[0],high=5)
        self.assertEqual(seq[0].high,12)
        self.assertIsInstance(seq[1:3],ValidatedBars)
        with patch('wavequant.wave_strength._validate_bar',side_effect=AssertionError('repeated validation')):
            validate_prefix(seq[1:3],symbol='x',end=1)
        for invalid in (bars,list(reversed(seq)),[seq[0],replace(seq[1],symbol='y')]):
            with self.assertRaises(ValueError):ValidatedBars(invalid)
        with self.assertRaises(ValueError):validate_prefix(seq,symbol='y',end=1)
        with self.assertRaises(ValueError):validate_prefix(seq,symbol='x',end=5)
        with self.assertRaises(ValueError):seq[::-1]
        self.assertIsInstance(seq[:1]+seq[2:3],tuple)

"""Persist disposable, content-addressed research artifacts as safe JSON.

SQLite commits are atomic across processes. Source hashes and exact prefix are
part of caller keys; an old chart is never sliced to manufacture a backtest.
"""
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from threading import RLock
from weakref import WeakValueDictionary
import time
import zlib
from contextlib import closing


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


class ArtifactCache:
    SCHEMA = 1

    def __init__(self, root, *, max_bytes=4 * 1024**3):
        self.path = Path(root) / 'artifacts-v1.sqlite'
        self.max_bytes = max_bytes
        self.locks = WeakValueDictionary()
        self.locks_guard = RLock()
        self.ready = False
        self.init_lock = RLock()

    def key(self, namespace, inputs):
        return hashlib.sha256(canonical([self.SCHEMA, namespace, inputs]).encode()).hexdigest()

    def lock(self, key):
        # Exact keys avoid nested stripe-lock collisions between geometry and
        # stock work. Weak references release idle single-flight locks.
        with self.locks_guard:
            lock = self.locks.get(key)
            if lock is None:
                lock = RLock()
                self.locks[key] = lock
            return lock

    def _connect(self):
        with self.init_lock:
            if not self.ready:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                for attempt in range(5):
                    try:
                        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
                            if db.execute('PRAGMA journal_mode').fetchone()[0]!='wal':
                                db.execute('PRAGMA journal_mode=WAL')
                            db.execute('CREATE TABLE IF NOT EXISTS artifacts (key TEXT PRIMARY KEY, namespace TEXT NOT NULL, payload BLOB NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL, created REAL NOT NULL)')
                        break
                    except sqlite3.OperationalError as exc:
                        if 'locked' not in str(exc) or attempt==4:
                            raise
                        # WAL initialization alone can race across fresh processes.
                        time.sleep(.02*(attempt+1))
                self.ready = True
        return sqlite3.connect(self.path, timeout=30)

    def get(self, namespace, inputs):
        key = self.key(namespace, inputs)
        try:
            with closing(self._connect()) as db, db:
                row = db.execute('SELECT payload,digest FROM artifacts WHERE key=?', (key,)).fetchone()
            if row is None:
                return None
            packed, digest = row
            if hashlib.sha256(packed).hexdigest() != digest:
                raise ValueError('artifact checksum mismatch')
            value = json.loads(zlib.decompress(packed))
            if value['key'] != key or value['schema'] != self.SCHEMA:
                raise ValueError('artifact identity mismatch')
            return value['data']
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, zlib.error) as exc:
            logging.warning('Research cache miss after unreadable artifact %s: %s', key, exc)
            return None

    def put(self, namespace, inputs, data):
        key = self.key(namespace, inputs)
        packed = zlib.compress(canonical(dict(schema=self.SCHEMA, key=key, data=data)).encode(), 1)
        if len(packed) > self.max_bytes:
            return False
        try:
            with closing(self._connect()) as db, db:
                db.execute('INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?)',
                           (key, namespace, packed, hashlib.sha256(packed).hexdigest(), len(packed), time.time()))
                total = db.execute('SELECT COALESCE(SUM(size),0) FROM artifacts').fetchone()[0]
                if total > self.max_bytes:
                    for old, size in db.execute('SELECT key,size FROM artifacts WHERE key!=? ORDER BY created', (key,)).fetchall():
                        db.execute('DELETE FROM artifacts WHERE key=?', (old,))
                        total -= size
                        if total <= self.max_bytes * .9:
                            break
            return True
        except (OSError, sqlite3.Error) as exc:
            logging.warning('Research cache write skipped: %s', exc)
            return False  # Disk failure never turns a valid calculation into an empty scan.

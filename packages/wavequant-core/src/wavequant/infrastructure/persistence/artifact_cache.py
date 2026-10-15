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
    def contains(self, namespace: str, inputs: object) -> bool:
        """Check retained chunk identity without decompressing all historical rows."""
        try:
            with closing(self._connect()) as db:
                return db.execute('SELECT 1 FROM artifacts WHERE key=?', (self.key(namespace, inputs),)).fetchone() is not None
        except (OSError, sqlite3.Error):
            return False

    SCHEMA = 1
    BACKTEST_NAMESPACES = ('backtest', 'signals', 'screen', 'akshare-backtest', 'akshare-signals', 'adjusted_theory')
    # Reads use WAL snapshots and can wait for transient filesystem pressure.
    # Writes are disposable optimizations: a busy background precomputation
    # must never make an interactive chart/backtest wait 30 seconds per
    # artifact.  On contention the caller keeps its valid computed result and
    # a later request or worker may populate the cache.
    READ_TIMEOUT_SECONDS = 30.0
    WRITE_TIMEOUT_SECONDS = 0.5

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

    def clear_backtests_for_engine(self, engine):
        """Discard derived results once when the loaded quant code changes."""
        version = hashlib.sha256(canonical(engine).encode()).hexdigest()
        try:
            with closing(self._connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('CREATE TABLE IF NOT EXISTS artifact_versions (name TEXT PRIMARY KEY, version TEXT NOT NULL)')
                row = db.execute('SELECT version FROM artifact_versions WHERE name=?', ('quant-engine',)).fetchone()
                if row is not None and row[0] == version:
                    return 0
                placeholders = ','.join('?' for _ in self.BACKTEST_NAMESPACES)
                deleted = db.execute(
                    f'DELETE FROM artifacts WHERE namespace IN ({placeholders})', self.BACKTEST_NAMESPACES
                ).rowcount
                db.execute(
                    'INSERT OR REPLACE INTO artifact_versions (name, version) VALUES (?, ?)',
                    ('quant-engine', version),
                )
                return deleted
        except (OSError, sqlite3.Error) as exc:
            logging.warning('Research backtest cache invalidation skipped: %s', exc)
            return 0

    def _connect(self, *, timeout_seconds=READ_TIMEOUT_SECONDS):
        with self.init_lock:
            if not self.ready:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                for attempt in range(5):
                    try:
                        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
                            if db.execute('PRAGMA journal_mode').fetchone()[0]!='wal':
                                db.execute('PRAGMA journal_mode=WAL')
                            usage_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                                     "AND name='artifact_usage'").fetchone()
                            if usage_table is None or db.execute('SELECT 1 FROM artifact_usage WHERE id=1').fetchone() is None:
                                # 迁移标记与触发器在同一事务发布；已初始化的新实例只读确认。
                                db.execute('BEGIN IMMEDIATE')
                                db.execute('CREATE TABLE IF NOT EXISTS artifacts (key TEXT PRIMARY KEY, namespace TEXT NOT NULL, payload BLOB NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL, created REAL NOT NULL)')
                                self._initialize_usage(db)
                        break
                    except sqlite3.OperationalError as exc:
                        if 'locked' not in str(exc) or attempt==4:
                            raise
                        # WAL initialization alone can race across fresh processes.
                        time.sleep(.02*(attempt+1))
                self.ready = True
        return sqlite3.connect(self.path, timeout=timeout_seconds)

    @staticmethod
    def _initialize_usage(db: sqlite3.Connection) -> None:
        """容量账本与产物同事务更新；旧库仅在首次迁移时读取全部 size。

        每键账本保留被 REPLACE 覆盖前的大小，兼容旧进程连接默认关闭
        recursive_triggers 的行为；普通删除、覆盖和回滚都不会重复计费。
        统计口径仍是压缩 payload 的逻辑大小，不代表 SQLite 物理文件大小。
        """
        db.execute('CREATE TABLE IF NOT EXISTS artifact_sizes '
                   '(key TEXT PRIMARY KEY, size INTEGER NOT NULL) WITHOUT ROWID')
        db.execute('CREATE TABLE IF NOT EXISTS artifact_usage '
                   '(id INTEGER PRIMARY KEY CHECK(id=1), total_bytes INTEGER NOT NULL CHECK(total_bytes>=0))')
        if db.execute('SELECT 1 FROM artifact_usage WHERE id=1').fetchone() is None:
            db.execute('DELETE FROM artifact_sizes')
            db.execute('INSERT INTO artifact_sizes SELECT key,size FROM artifacts')
            db.execute('INSERT INTO artifact_usage SELECT 1,COALESCE(SUM(size),0) FROM artifact_sizes')
        db.execute('CREATE INDEX IF NOT EXISTS artifacts_eviction_order ON artifacts(created,key,size)')
        db.execute('''CREATE TRIGGER IF NOT EXISTS artifact_usage_insert AFTER INSERT ON artifacts BEGIN
            UPDATE artifact_usage SET total_bytes=total_bytes+NEW.size-
                COALESCE((SELECT size FROM artifact_sizes WHERE key=NEW.key),0) WHERE id=1;
            INSERT OR REPLACE INTO artifact_sizes(key,size) VALUES(NEW.key,NEW.size);
        END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS artifact_usage_delete AFTER DELETE ON artifacts BEGIN
            UPDATE artifact_usage SET total_bytes=total_bytes-
                COALESCE((SELECT size FROM artifact_sizes WHERE key=OLD.key),0) WHERE id=1;
            DELETE FROM artifact_sizes WHERE key=OLD.key;
        END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS artifact_usage_update AFTER UPDATE OF key,size ON artifacts BEGIN
            UPDATE artifact_usage SET total_bytes=total_bytes-
                COALESCE((SELECT size FROM artifact_sizes WHERE key=OLD.key),0) WHERE id=1;
            DELETE FROM artifact_sizes WHERE key=OLD.key;
            UPDATE artifact_usage SET total_bytes=total_bytes+NEW.size-
                COALESCE((SELECT size FROM artifact_sizes WHERE key=NEW.key),0) WHERE id=1;
            INSERT OR REPLACE INTO artifact_sizes(key,size) VALUES(NEW.key,NEW.size);
        END''')

    def _evict_over_budget(self, db: sqlite3.Connection, key: str, packed_size: int) -> None:
        """写事务内读取单行容量，超限时按索引分批淘汰，保留本次产物。"""
        total = db.execute('SELECT total_bytes FROM artifact_usage WHERE id=1').fetchone()[0]
        if total <= self.max_bytes:
            return
        target = max(self.max_bytes * 9 // 10, packed_size)
        while total > target:
            rows = db.execute('SELECT key,size FROM artifacts WHERE key!=? '
                              'ORDER BY created,key LIMIT 256', (key,)).fetchall()
            if not rows:
                break
            expired = []
            for old, size in rows:
                expired.append((old,))
                total -= size
                if total <= target:
                    break
            db.executemany('DELETE FROM artifacts WHERE key=?', expired)

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
            with closing(self._connect(timeout_seconds=self.WRITE_TIMEOUT_SECONDS)) as db, db:
                db.execute('INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?)',
                           (key, namespace, packed, hashlib.sha256(packed).hexdigest(), len(packed), time.time()))
                self._evict_over_budget(db, key, len(packed))
            return True
        except (OSError, sqlite3.Error) as exc:
            logging.warning('Research cache write skipped: %s', exc)
            return False  # Disk failure never turns a valid calculation into an empty scan.

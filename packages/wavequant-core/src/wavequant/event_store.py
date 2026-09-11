"""SQLite append-only journal with transactional idempotency and hash chaining.

One transaction owns load -> validate -> append. SQLite serializes concurrent
writers; no network side effects may occur inside a journal transaction.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def utc(value):
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise ValueError('timezone-aware event time required')
    return dt.astimezone(timezone.utc).isoformat()


class EventStore:
    def __init__(self, path, *, readonly=False):
        self.path = Path(path)
        self.readonly = readonly
        if readonly:
            if not self.path.is_file(): raise ValueError('existing journal required for read-only access')
            self.db = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True,
                                      isolation_level=None, timeout=10)
            self.db.row_factory = sqlite3.Row
            try: self.verify()
            except BaseException:
                self.db.close(); raise
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
            event_time TEXT NOT NULL, payload TEXT NOT NULL, previous_hash TEXT NOT NULL,
            digest TEXT NOT NULL)''')
        self.db.executescript('''
            CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
        ''')
        try:
            self.verify()
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def transaction(self):
        if self.readonly: raise RuntimeError('read-only journal cannot write')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield self
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        else:
            self.db.execute('COMMIT')

    def get(self, event_id):
        row = self.db.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
        return None if row is None else self._decode(row)

    @staticmethod
    def _decode(row):
        value = dict(row)
        value['payload'] = json.loads(value['payload'])
        return value

    def events(self):
        return [self._decode(r) for r in self.db.execute('SELECT * FROM events ORDER BY seq')]

    def append(self, event_id, kind, when, payload):
        if not self.db.in_transaction:
            raise RuntimeError('append requires transaction')
        if not event_id or not kind:
            raise ValueError('event identity and kind required')
        when, encoded = utc(when), canonical(payload)
        existing = self.get(event_id)
        if existing is not None:
            if (existing['kind'], existing['event_time'], canonical(existing['payload'])) != (kind, when, encoded):
                raise ValueError('idempotency conflict: same ID has different content')
            return existing
        last = self.db.execute('SELECT seq,digest FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        seq, previous = (last['seq']+1, last['digest']) if last else (1, '0'*64)
        digest = hashlib.sha256(canonical([seq,event_id,kind,when,payload,previous]).encode()).hexdigest()
        self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',
                        (seq,event_id,kind,when,encoded,previous,digest))
        return self.get(event_id)

    def verify(self):
        previous = '0'*64
        for seq, e in enumerate(self.events(), 1):
            expected = hashlib.sha256(canonical([seq,e['event_id'],e['kind'],e['event_time'],e['payload'],previous]).encode()).hexdigest()
            if e['seq'] != seq or e['previous_hash'] != previous or e['digest'] != expected:
                raise ValueError('journal hash chain integrity failure')
            previous = expected
        return previous

    def backup(self, destination):
        target = Path(destination)
        if target.exists():
            raise ValueError('backup destination exists')
        target.parent.mkdir(parents=True, exist_ok=True)
        other=sqlite3.connect(target)
        try:
            self.db.backup(other)
        finally:
            other.close()

    def close(self):
        self.db.close()

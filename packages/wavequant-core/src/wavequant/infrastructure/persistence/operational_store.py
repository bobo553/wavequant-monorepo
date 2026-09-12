"""Persist single-writer run control and acknowledgeable health alerts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re

from .event_store import canonical


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value):
        raise ValueError('identity must be 1-80 ASCII letters, digits, hyphen or underscore')
    if value.upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        raise ValueError('reserved Windows device identity')
    return value


@contextmanager
def workspace_lock(root):
    """OS-held lock: crashes release it; an old lock file is not a live owner.

    Never unlink the file (another process may already have opened its inode).
    All operations commands share this lock; unrelated writers must be stopped
    before making a consistent multi-database backup.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / '.operations.lock').open('a+b')
    locked = False
    try:
        if handle.seek(0, 2) == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('another operations command owns this workspace') from exc
        locked = True
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def run_states(store):
    runs = {}
    for event in store.events():
        p = event['payload']
        if event['kind'] == 'RUN_STARTED':
            runs[p['run_id']] = dict(p, status='RUNNING', started_at=event['event_time'], stages=[])
        elif event['kind'] == 'RUN_STAGE':
            runs[p['run_id']]['stages'].append(dict(p, at=event['event_time']))
        elif event['kind'] == 'RUN_FINISHED':
            runs[p['run_id']].update(p, finished_at=event['event_time'])
    return runs


def alert_states(store):
    alerts = {}
    for e in store.events():
        p = e['payload']
        if e['kind'] == 'ALERT_OPENED':
            alerts[p['alert_id']] = dict(p, opened_at=e['event_time'], active=True, acknowledged=False)
        elif e['kind'] == 'ALERT_ACKNOWLEDGED':
            alerts[p['alert_id']].update(acknowledged=True, acknowledgement=p['reason'])
        elif e['kind'] == 'ALERT_RESOLVED':
            alerts[p['alert_id']].update(active=False, resolved_at=e['event_time'])
    return alerts


def synchronize_alerts(store, scope, observations, when):
    """Deduplicate unchanged conditions; acknowledgement never resolves a fault.

    This is a LOCAL notification inbox. No network delivery is claimed.
    """
    if len({o['code'] for o in observations}) != len(observations):
        raise ValueError('one observation per health code required')
    for o in observations:
        if o['severity'] not in ('warning', 'critical') or not o['code'] or not o['message']:
            raise ValueError('identified warning or critical alert required')
    with store.transaction():
        states = alert_states(store)
        wanted = {hashlib.sha256(canonical(o).encode()).hexdigest(): o for o in observations}
        active = {a['condition_hash']: a for a in states.values() if a['scope'] == scope and a['active']}
        for digest, a in active.items():
            if digest not in wanted:
                store.append('resolve:' + a['alert_id'], 'ALERT_RESOLVED', when, dict(alert_id=a['alert_id']))
        for digest, observation in wanted.items():
            if digest not in active:
                aid = hashlib.sha256(canonical([scope, digest, len(store.events())]).encode()).hexdigest()
                store.append('alert:' + aid, 'ALERT_OPENED', when,
                             dict(alert_id=aid, condition_hash=digest, scope=scope, **observation))
    return [a for a in alert_states(store).values() if a['active']]


def acknowledge_alert(store, alert_id, reason, when):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('operator acknowledgement reason required')
    with store.transaction():
        a = alert_states(store).get(alert_id)
        if a is None:
            raise ValueError('unknown alert')
        if a['acknowledged']:
            if a['acknowledgement'] != reason:
                raise ValueError('acknowledgement already recorded with a different reason')
            return a
        store.append('ack-alert:' + alert_id, 'ALERT_ACKNOWLEDGED', when,
                     dict(alert_id=alert_id, reason=reason))
    return alert_states(store)[alert_id]

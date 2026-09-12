"""Create verified directory backups without unsafe extraction or overwrite."""
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3

from ..market_data.data import dump_json, fingerprint


def _has_wal_frames(path):
    try: return Path(str(path)+'-wal').stat().st_size > 0
    except FileNotFoundError: return False


def _target(base, relative):
    name = PurePosixPath(relative)
    if (not relative or name.is_absolute() or '..' in name.parts or '\\' in relative
            or ':' in relative or name.as_posix() != relative):
        raise ValueError('unsafe bundle member path')
    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()) or target == base.resolve():
        raise ValueError('bundle member escapes root')
    return target


def verify_bundle(bundle):
    root = Path(bundle).resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or not isinstance(manifest.get('files'), dict) or not manifest['files']:
        raise ValueError('nonempty versioned bundle manifest required')
    for relative, digest in manifest['files'].items():
        path = _target(root / 'payload', relative)
        if not path.is_file() or fingerprint(path) != digest:
            raise ValueError('bundle integrity failure: ' + relative)
    actual = set()
    for p in (root / 'payload').rglob('*'):
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise ValueError('linked bundle members are not supported')
        if p.is_file(): actual.add(p.relative_to(root / 'payload').as_posix())
    if actual != set(manifest['files']):
        raise ValueError('unregistered bundle members')
    return manifest


def create_bundle(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_dir() or destination.exists():
        raise ValueError('existing source and new backup destination required')
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('backup and source must be disjoint trees')
    members = []
    for p in source.rglob('*'):
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise ValueError('linked source members are not supported')
        if p.is_file() and p.name != '.operations.lock' and not p.name.endswith(('-wal', '-shm', '-journal')):
            members.append(p)
    if not members: raise ValueError('nothing to back up')
    destination.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for p in sorted(members):
        relative = p.relative_to(source).as_posix()
        target = _target(destination / 'payload', relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix == '.sqlite':
            before=fingerprint(p)
            checkpointed=not _has_wal_frames(p)
            db = sqlite3.connect(p.as_uri() + '?mode=ro', uri=True)
            out = sqlite3.connect(target)
            try:
                db.backup(out)
                if out.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('SQLite backup integrity failure')
            finally:
                out.close(); db.close()
            # backup() can rewrite SQLite header/page bookkeeping while keeping
            # all rows identical. Preserve bytes for already-checkpointed sealed
            # artifacts; still use the API snapshot whenever WAL frames exist.
            # Source must remain quiescent under the caller's workspace lock.
            if checkpointed and not _has_wal_frames(p) and fingerprint(p)==before:
                shutil.copyfile(p,target)
                if fingerprint(p)!=before or fingerprint(target)!=before or _has_wal_frames(p):
                    raise ValueError('checkpointed source changed during backup')
        else:
            before = fingerprint(p)
            shutil.copyfile(p, target)
            if fingerprint(p) != before or fingerprint(target) != before:
                raise ValueError('source changed during backup')
        hashes[relative] = fingerprint(target)
    dump_json(destination / 'manifest.json', dict(schema=1, files=hashes,
              scope='local directory snapshot; writers must be quiescent',
              authenticity='hashes detect damage, not malicious replacement of payload and manifest'))
    return verify_bundle(destination)


def restore_bundle(bundle, destination):
    manifest = verify_bundle(bundle)
    destination = Path(destination).resolve()
    source = Path(bundle).resolve() / 'payload'
    if destination.exists() or destination.is_relative_to(Path(bundle).resolve()):
        raise ValueError('restore requires a new destination outside the bundle')
    destination.mkdir(parents=True, exist_ok=False)
    for relative, digest in manifest['files'].items():
        target = _target(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_target(source, relative), target)
        if fingerprint(target) != digest:
            raise ValueError('restore verification failed')
    return dict(restored_files=len(manifest['files']), destination=str(destination), verified=True)

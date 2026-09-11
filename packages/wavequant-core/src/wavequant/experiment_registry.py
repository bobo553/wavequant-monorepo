"""Immutable experiment lifecycle, reproducible inputs and artifact hashes."""
from pathlib import Path
import platform
import sys

from .data import fingerprint
from .event_store import utc


def environment_snapshot(root):
    root=Path(root)
    files=sorted((root/'wavequant').glob('*.py'))+sorted((root/'tests').glob('test_*.py'))
    files += [p for p in (root/'pyproject.toml',root/'requirements-lock.txt') if p.exists()]
    return dict(python=sys.version,platform=platform.platform(),
                hashes={str(p.relative_to(root)):fingerprint(p) for p in files})


class ExperimentRegistry:
    def __init__(self, store): self.store=store

    def start(self, run_id, when, *, config, inputs, environment):
        if not run_id or not inputs: raise ValueError('run identity and fingerprinted inputs required')
        for p,digest in inputs.items():
            if fingerprint(Path(p))!=digest: raise ValueError('experiment input hash mismatch')
        with self.store.transaction():
            if self.store.get('experiment:'+run_id): raise ValueError('run ID already exists; never overwrite history')
            self.store.append('experiment:'+run_id,'EXPERIMENT_STARTED',when,
                              dict(config=config,inputs=inputs,environment=environment))

    def finish(self, run_id, when, *, artifacts, status='COMPLETED', error=None):
        if status not in ('COMPLETED','FAILED'): raise ValueError('terminal run status required')
        hashes={str(Path(p).resolve()):fingerprint(Path(p)) for p in artifacts}
        with self.store.transaction():
            started=self.store.get('experiment:'+run_id)
            if not started: raise ValueError('unknown run')
            if utc(when)<started['event_time']: raise ValueError('finish precedes start')
            if self.store.get('experiment:end:'+run_id): raise ValueError('experiment already terminal')
            if status=='COMPLETED':
                for p,digest in started['payload']['inputs'].items():
                    if fingerprint(Path(p))!=digest: raise ValueError('input changed during experiment')
                if not hashes: raise ValueError('completed run requires artifacts')
            self.store.append('experiment:end:'+run_id,'EXPERIMENT_FINISHED',when,
                              dict(status=status,artifacts=hashes,error=error))

    def verify_artifacts(self, run_id):
        event=self.store.get('experiment:end:'+run_id)
        if event is None: raise ValueError('run has not finished')
        return {p:Path(p).is_file() and fingerprint(Path(p))==digest
                for p,digest in event['payload']['artifacts'].items()}

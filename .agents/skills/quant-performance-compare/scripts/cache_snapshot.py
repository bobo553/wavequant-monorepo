"""Read only cache metadata; never load market payloads or evict entries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from datetime import datetime, timezone


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite',type=Path)
    parser.add_argument('--redis-container')
    parser.add_argument('--redis-prefix',default='wavequant:rust:local')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    snapshot={'observed_at':datetime.now(timezone.utc).isoformat()}
    if args.sqlite:
        database=args.sqlite.resolve()
        if database.exists():
            with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
                namespaces=[{'namespace':name,'entries':count,'payload_bytes':size} for name,count,size in db.execute('SELECT namespace,COUNT(*),COALESCE(SUM(length(payload)),0) FROM artifacts GROUP BY namespace ORDER BY namespace')]
            snapshot['sqlite']={'path':str(database),'namespaces':namespaces,'database_bytes':database.stat().st_size,
                'wal_bytes':Path(str(database)+'-wal').stat().st_size if Path(str(database)+'-wal').exists() else 0}
        else:
            snapshot['sqlite']={'path':str(database),'exists':False}
    if args.redis_container:
        prefix=args.redis_prefix+':market-kline-v2:'
        def read(*command):
            output=subprocess.check_output(['docker','exec',args.redis_container,'redis-cli','--json',*command],creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,timeout=15)
            return json.loads(output)
        sizes=read('HGETALL',prefix+'sizes')
        if isinstance(sizes,list):
            sizes=dict(zip(sizes[::2],sizes[1::2]))
        keys=sorted(sizes)
        snapshot['redis']={'container':args.redis_container,'prefix':prefix,'lru_entries':read('ZCARD',prefix+'lru'),
            'size_entries':len(sizes),'payload_bytes':sum(int(size) for size in sizes.values()),
            'keyset_sha256':hashlib.sha256(json.dumps(keys,separators=(',',':')).encode()).hexdigest(),
            'sizes_sha256':hashlib.sha256(json.dumps(sizes,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'sample_pttl_ms':read('PTTL',keys[0]) if keys else None}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(snapshot,ensure_ascii=False))


if __name__=='__main__':
    main()

"""Run from the project root: python -m examples.price_action_basics"""
from dataclasses import asdict
from datetime import datetime
import json

from wavequant.model import Bar
from wavequant.price_action import AttackBasis, KeyLevel, LevelKind, observe_sequence


def main():
    bars = [
        Bar(datetime(2026,9,1),'EXAMPLE',9,10,8.8,9.5,1000),
        Bar(datetime(2026,9,2),'EXAMPLE',9.6,10.8,9.4,10.5,1000),
        Bar(datetime(2026,9,3),'EXAMPLE',10.6,10.7,9.8,10.1,1000),
        Bar(datetime(2026,9,4),'EXAMPLE',10.2,11.3,10,11,1000),
    ]
    key = KeyLevel(symbol='EXAMPLE',timeframe='1d',kind=LevelKind.RESISTANCE,
                   price=10,source_index=0,confirmed_index=0,source='example_preidentified_rebound_high')
    for length in (2,3,4):
        evidence = observe_sequence(bars[:length],1,key,timeframe='1d',attack_basis=AttackBasis.CLOSE)
        print(json.dumps(asdict(evidence),default=str,ensure_ascii=False,allow_nan=False,indent=2))


if __name__=='__main__':
    main()

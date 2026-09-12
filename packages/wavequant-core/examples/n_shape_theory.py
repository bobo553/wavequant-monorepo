"""Run: python -m examples.n_shape_theory (synthetic definition examples only)."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
import sys

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, MilestoneBasis, NSetup, PivotRef, observe_n


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    rows=[(8.5,9,8,8.5),(10,12,9.5,11),(10.7,11,10,10.5),
          (10.8,12.6,10.4,12.2),(12.2,14,12,13.8),
          (14,17.3,13.5,17.1),(17.2,21.9,17,21.8)]
    bars=[Bar(datetime(2026,1,1)+timedelta(days=i),'EXAMPLE',o,h,l,c,1000)
          for i,(o,h,l,c) in enumerate(rows)]
    setup=NSetup('EXAMPLE','1d',Direction.UP,PivotRef(0,0),PivotRef(1,1),PivotRef(2,2),
                 'manually_identified_example_ABC',BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)
    for size in (3,4,5,6,7):
        r=observe_n(bars[:size],setup,timeframe='1d',milestone_basis=MilestoneBasis.EXTREME)
        print(json.dumps(dict(asof=size-1,status=r.status,completion=asdict(r.completion) if r.completion else None,
                              targets=asdict(r.targets) if r.targets else None,
                              milestones=[asdict(m) for m in r.milestones],next_target=r.next_target,
                              measured_wave_complete=r.measured_wave_complete),default=str,ensure_ascii=False,indent=2))
    inverse=[replace(b,open=40-b.open,high=40-b.low,low=40-b.high,close=40-b.close) for b in bars]
    r=observe_n(inverse,replace(setup,direction=Direction.DOWN),timeframe='1d',milestone_basis=MilestoneBasis.EXTREME)
    print(json.dumps(dict(inverse_completion=asdict(r.completion),inverse_targets=asdict(r.targets)),
                     default=str,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

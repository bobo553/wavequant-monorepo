"""Create causal strategy snapshots with the same reducer used by the chart.

The child/mother order is an explicit lecture convention, not observed ticks.
Each snapshot owns frozen turns; later drawing changes never backdate a signal.
Only undefined doji/initial-direction issues delimit structural episodes.
"""
from ..market_structure.lecture_drawing import lecture_drawing
from ..market_structure.polyline import LinePoint,PointKind,ReversalPoint


def lecture_pivot_history(bars):
    snapshots={};epochs={};limits={};blocked=set();prior={};last_epoch=None
    def accept(i,epoch,points):
        nonlocal prior,last_epoch
        if epoch!=last_epoch: prior={}
        last_epoch=epoch;epochs[i]=epoch
        if i and not points and epoch==i: blocked.add(i)
        known=[];now={}
        # Match reversal_trends' geometric-turn selection, not synthetic bridges.
        compact=[]
        for p in points:
            if not compact or p['value']!=compact[-1]['value']:compact.append(p)
        for left,p,right in zip(compact,compact[1:],compact[2:]):
            if p['state'] in ('seed','developing'):continue
            kind=('H' if p['value']>left['value'] and p['value']>right['value'] else
                  'L' if p['value']<left['value'] and p['value']<right['value'] else None)
            if kind is None:continue
            # A geometric seam may turn at a candle high while looking like a
            # local low. Do not relabel that price as the candle's actual low.
            if kind!=p['kind']:continue
            key=(p['index'],p['ordinal'],kind,p['value'])
            confirmed=prior[key].confirmed_index if key in prior else i
            if known:confirmed=max(confirmed,known[-1].confirmed_index)
            ref=ReversalPoint(LinePoint(p['index'],p['ordinal'],PointKind(kind),p['value']),confirmed,
                              'lecture_close_confirmed_convention_not_intrabar_execution')
            # Geometric extrema alternate even through mother/child seams.
            if known and ref.point.kind==known[-1].point.kind:
                sign=1 if kind=='H' else -1
                if sign*(ref.point.price-known[-1].point.price)<=0:continue
                known.pop()
            known.append(ref);now[key]=ref
        prior=now;snapshots[i]=tuple(known)
    lecture_drawing(bars,on_step=accept)
    # Existing event pipeline bounds setups to a causal episode; prefix tests
    # enforce that a future episode boundary cannot alter any earlier signal.
    end=len(bars)-1
    for i in range(end,-1,-1):
        if i in blocked:end=i-1
        limits[i]=end
    return snapshots,epochs,limits,blocked

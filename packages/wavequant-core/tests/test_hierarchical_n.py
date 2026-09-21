from datetime import datetime
import json
from pathlib import Path

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies import hierarchical_n


def test_larger_n_folds_lower_correction_without_repainting(monkeypatch):
    def point(index,kind,value,known):
        return dict(index=index,kind=kind,value=value,available_at=known,ordinal=0)
    first=[point(0,'L',5,1),point(2,'H',10,3),point(4,'L',8,6)]
    later=[*first,point(6,'H',9,7),point(8,'L',7,9)]
    monkeypatch.setattr(hierarchical_n,'hierarchical_history',lambda bars: (
        {6:{2:first,3:[]},9:{2:later,3:[]}},{}))
    result=hierarchical_n.hierarchical_n_candidates([])
    assert result[6][0][0][-1].point.index==4
    assert result[9][0][0][-1].point.index==8
    assert result[9][0][0][-1].confirmed_index==9
    assert all(level==2 for rows in result.values() for _,level in rows)
    invalid=[*first,point(6,'H',9,7),point(8,'L',4,9)]
    monkeypatch.setattr(hierarchical_n,'hierarchical_history',lambda bars: ({9:{2:invalid,3:[]}},{}))
    assert hierarchical_n.hierarchical_n_candidates([])=={}


def test_guilin_dec2_larger_n_and_dec6_squeeze_are_causal():
    raw=json.loads((Path(__file__).parent/'fixtures'/'guilin_2022_hierarchy.json').read_text(encoding='utf-8'))
    bars=[Bar(datetime.fromisoformat(day),raw['symbol'],*values) for day,*values in raw['bars']]
    config=SystemStrategy(pivot_mode='lecture_causal',entry_policy='hierarchical_two_buy_points',
                          buy_point_definition='whole_flip_wave_v3',volume_filter=False)
    full=generate_system_signals(bars,config)
    event=next(e for e in full.audit if e['event']=='n_completed' and e['direction']=='up'
               and e['timestamp'].startswith('2022-12-02'))
    assert event['n_level']==2
    assert [bars[event[k]].timestamp.date().isoformat() for k in ('origin','neckline','pullback')]==[
        '2022-04-27','2022-06-30','2022-10-12']
    assert any(e['event']=='regime_confirmation' and e['attack']==event['bar_index']
               and e['timestamp'].startswith('2022-12-06') for e in full.audit)
    for date in ('2022-12-02','2022-12-06'):
        cutoff=next(i for i,b in enumerate(bars) if b.timestamp.date().isoformat()==date)
        prefix=generate_system_signals(bars[:cutoff+1],config)
        assert [e for e in full.audit if e['event']=='n_completed' and e['bar_index']<=cutoff]==[
            e for e in prefix.audit if e['event']=='n_completed']
        assert [s for s in full.signals if s.bar_index<=cutoff]==prefix.signals

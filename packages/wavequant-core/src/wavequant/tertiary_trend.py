"""Level-3 trend: the level-2 structural-break rule applied to level-2 only."""
from zoneinfo import ZoneInfo

from .lecture_trend import _annotate
from .secondary_trend import _structural_reversals


def tertiary_trends(level2,bars):
    dates={(b.timestamp.astimezone(ZoneInfo('Asia/Shanghai')) if b.timestamp.tzinfo else b.timestamp).date().isoformat():i
           for i,b in enumerate(bars)}
    strokes=[]
    for source in level2['strokes']:
        points=_structural_reversals(source['points'],source_level=2)
        if not points:
            continue
        _annotate(points,bars[0].symbol,dates)
        for p in points:
            p['levels'].insert(0,dict(name='二级'+('末升低' if p['flip']=='翻多为空' else '末跌高'),price=p['broken_key']['value']))
        strokes.append(dict(id='tertiary-'+source['id'],source_path=source['id'],kind='tertiary',
                            trend_level=3,points=points,input_turn_count=len(source['points'])))
    return dict(name='三级趋势线',trend_level=3,source_level=2,strokes=strokes,
                input_turn_count=sum(len(s['points']) for s in level2['strokes']),
                confirmed_wave_count=sum(len(s['points']) for s in strokes),
                aggregation_rule='level2_structural_key_break',scope='lecture_level3_not_strategy_confirmation',
                note='仅以已确认二级点为输入；突破二级末跌高确认整段低点，跌破二级末升低确认整段高点；不等待67%交替，不跨断点，不补未确认尾端。')

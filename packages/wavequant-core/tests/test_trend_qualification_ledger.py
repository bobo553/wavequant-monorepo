"""完整事件账本不能让晚证明倒改已知策略节点的日期。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_lines as module
from wavequant.domain.market_structure.trend_judgement import judge_trend, mutable_points
from wavequant.domain.market_structure.trend_publication import publish_uptrends
from wavequant.domain.models.model import Bar


@pytest.mark.parametrize('reverse_ledger', [False, True])
def test_strategy_nodes_use_earliest_structure_while_late_facts_remain_in_the_ledger(
    monkeypatch: pytest.MonkeyPatch, reverse_ledger: bool,
) -> None:
    values = [20,30,10,20,14,26,18,35,28,45,38,42,32,36,24,29,26,50,30,48,28,46]
    source: list[dict[str, object]] = [dict(index=i, ordinal=0, kind='L' if i % 2 == 0 else 'H',
        value=value, time=str(i), available_at=i+1, state='reversal', trend_level=0, label=f'p{i}')
        for i, value in enumerate(values)]
    events = mutable_points(judge_trend(source).candidates)
    facts = [event for event in events if event['index'] == 17]
    assert {event['available_at'] for event in facts} == {18, 22}
    ledger = list(reversed(events)) if reverse_ledger else events
    before = deepcopy(ledger)
    original = publish_uptrends
    received: list[dict[str, object]] = []

    def publish(candidates: Sequence[Mapping[str, object]], points: Sequence[Mapping[str, object]],
                bars: Sequence[Bar], *, source_level: int,
                qualified_source: Sequence[Mapping[str, object]] | None = None,
                n_target_trend_confirmation_enabled: bool = False) -> list[dict[str, object]]:
        received.extend(dict(point) for point in candidates)
        return original(candidates, points, bars, source_level=source_level,
            qualified_source=qualified_source,
            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)

    monkeypatch.setattr(module, 'publish_uptrends', publish)
    # 行情使用合法OHLC，许可证仍由真实publisher核验；spy只观察入参边界。
    bars = [Bar(datetime(2026,1,1)+timedelta(days=i), 'test', value, value+1, value-1, value, 100)
            for i, value in enumerate([*values, 44])]
    module.qualify_trend_structure(ledger, source, bars, trend_level=1)
    identity = [(point['index'], point['ordinal']) for point in received]
    assert len(identity) == len(set(identity))
    assert identity == sorted(identity)
    node = next(point for point in received if point['index'] == 17)
    assert node['available_at'] == 18
    assert cast(Mapping[str, object], node['upgrade_evidence'])['direction'] == 'up'
    assert ledger == before
    assert {event['available_at'] for event in events if event['index'] == 17} == {18, 22}

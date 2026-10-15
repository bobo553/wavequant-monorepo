"""回测买点区间参数保持二级缺省，严格区分三个级别。"""

import pytest

from wavequant_api.server import buy_interval_level_option
from wavequant_api.watchlists import settings


def test_query_default_and_supported_levels() -> None:
    assert buy_interval_level_option({}) == 2
    for level in ('1', '2', '3'):
        assert buy_interval_level_option({'buy_interval_level': [level]}) == int(level)


@pytest.mark.parametrize('values', ([], ['0'], ['4'], ['true'], ['2.0'], ['1', '3']))
def test_query_rejects_invalid_or_duplicate_level(values: list[str]) -> None:
    with pytest.raises(ValueError):
        buy_interval_level_option({'buy_interval_level': values})


def test_old_watchlist_settings_default_to_second_level_and_preserve_explicit_choice() -> None:
    context = dict(run='example', variant='lecture_v3', scenario='base', source='akshare',
        start='2018-01-01', volume_filter='false', net_reward_risk_filter='false',
        shallow_base_breakout_enabled='true', initial_capital='100000', max_position_weight='1')
    default_context = settings(dict(enabled=True, context=context))['context']
    assert isinstance(default_context, dict)
    assert default_context['buy_interval_level'] == '2'
    selected_context = settings(dict(enabled=True, context=dict(context, buy_interval_level='1')))['context']
    assert isinstance(selected_context, dict)
    assert selected_context['buy_interval_level'] == '1'
    with pytest.raises(ValueError):
        settings(dict(enabled=True, context=dict(context, buy_interval_level='4')))

"""Opening signals execute without borrowing the same session's close."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.n_opening_squeeze import opening_n_squeeze
from wavequant.domain.strategies.strategy_profiles import WAVE_PROFILES, whole_wave_profile
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result


def opening_sample():
    first = datetime(2024, 1, 2)
    bars = [Bar(first + timedelta(days=j), 'sh.600825', 10, 11, 9, 10, 100_000) for j in range(3)]
    bars.append(Bar(first + timedelta(days=3), 'sh.600825', 10.5, 12, 9.5, 11.8, 150_000))
    signal = Signal(bars[-1].timestamp, bars[-1].symbol, 3, 'LONG', 10.5, 9,
                    'system_n_opening_gap_squeeze', bars[1].timestamp, 0.3, None, '轧空', 15)
    config = StrategyConfig(entry_at_close=True, initial_capital=100_000, max_position_weight=1,
                            exit_on_target=False, commission_bps_per_side=0, minimum_commission=0,
                            a_share_taxes=False, max_participation=1)
    return bars, signal, config


def test_opening_signal_fills_same_session_open_even_with_close_execution_selected():
    bars, signal, config = opening_sample()
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    buy = next(order for order in result.orders if order['side'] == 'BUY')
    assert buy['status'] == 'filled'
    assert buy['price'] == pytest.approx(10.5 * 1.0005)
    assert buy['execution_model'] == 'same_day_open'
    assert buy['decision_timestamp'].endswith('09:30:00')
    assert buy['execution_timestamp'] == buy['decision_timestamp']


def test_opening_fill_size_ignores_later_close_range_and_volume():
    bars, signal, config = opening_sample()
    baseline = run_portfolio({bars[0].symbol: bars}, [signal], config)
    changed = [*bars[:-1], replace(bars[-1], high=10.5, low=8.5, close=8.6, volume=1)]
    replay = run_portfolio({bars[0].symbol: changed}, [signal], config)
    assert baseline.orders[0] == replay.orders[0]


def test_unbuyable_open_cannot_use_later_nonflat_limit_close_permission():
    bars, signal, config = opening_sample()
    bars[-1] = replace(bars[-1], buyable=False, close_buyable=False, nonflat_close_buyable=True)
    result = run_portfolio({bars[0].symbol: bars}, [signal], replace(config, nonflat_limit_close_fill=True))
    assert result.orders[0]['status'] == 'cancelled'
    assert result.orders[0]['reason'] == 'not_buyable'
    assert result.orders[0]['execution_model'] == 'same_day_open'


def test_regular_daily_signal_still_uses_close():
    bars, signal, config = opening_sample()
    signal = replace(signal, reason='system_transition_squeeze', reference_price=bars[-1].close)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    assert result.orders[0]['price'] == pytest.approx(bars[-1].close * 1.0005)
    assert result.orders[0]['execution_model'] == 'same_day_close'


@pytest.mark.parametrize('opening,expected', [(10.0001, True), (10, False), (9.99, False)])
def test_strict_gap_above_previous_close(opening, expected):
    bars, _, _ = opening_sample()
    bars[-1] = replace(bars[-1], open=opening, high=max(opening, 12))
    proof = opening_n_squeeze(bars, attack=1, known=2, now=3, defense=9)
    assert (proof is not None) is expected


@pytest.mark.parametrize('known,invalidated', [(3, None), (4, None), (2, 2)])
def test_future_or_already_invalidated_n_cannot_open(known, invalidated):
    bars, _, _ = opening_sample()
    assert opening_n_squeeze(bars, attack=1, known=known, now=3, defense=9,
                             invalidated_at=invalidated) is None


def test_prior_defense_touch_remains_live_but_break_and_recovery_does_not():
    bars, _, _ = opening_sample()
    assert opening_n_squeeze(bars, attack=1, known=2, now=3, defense=9)
    bars[2] = replace(bars[2], low=8.999)
    assert opening_n_squeeze(bars, attack=1, known=2, now=3, defense=9) is None


def test_later_inverse_and_today_range_cannot_revoke_an_opening_proof():
    bars, _, _ = opening_sample()
    before = opening_n_squeeze(bars, attack=1, known=2, now=3, defense=9)
    bars[-1] = replace(bars[-1], high=10.5, low=8.5, close=8.6, volume=1)
    assert opening_n_squeeze(bars, attack=1, known=2, now=3, defense=9, invalidated_at=3) == before


@pytest.mark.parametrize('variant', WAVE_PROFILES)
def test_all_current_v3_profiles_enable_opening_squeeze(variant):
    profile = whole_wave_profile({'scenarios': {'base': {'execution': {}}}}, variant)
    assert profile['strategy']['opening_gap_squeeze_enabled'] is True
    assert profile['profile_version'].startswith('gap_up_bullish_squeeze_v94_')


@pytest.fixture(scope='module')
def xinhua_opening():
    raw = json.loads((Path(__file__).parent / 'fixtures/xinhua_2019_volume_record.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], opening, high, low, close, volume,
                adjustment_factor=factor, **raw['permissions'][day])
            for day, opening, high, low, close, volume, factor, _ in raw['bars'] if day <= '2019-01-18']
    config = SystemStrategy(**dict(raw['strategy'], opening_gap_squeeze_enabled=True))
    return bars, config, raw['execution'], generate_system_signals(bars, config)


def test_real_january_17_predicate_and_first_earlier_opening_are_reported_honestly(xinhua_opening):
    bars, _, _, result = xinhua_opening
    dates = {str(bar.timestamp.date()): i for i, bar in enumerate(bars)}
    attack, now = dates['2019-01-07'], dates['2019-01-17']
    proof = opening_n_squeeze(bars, attack=attack, known=attack, now=now, defense=5.169097651421507)
    assert proof is not None
    assert proof['opening_price'] == pytest.approx(5.620766378244745)
    assert proof['prior_close'] == pytest.approx(5.4601730531520385)
    signals = [signal for signal in result.signals if signal.side == 'LONG'
               and signal.trigger_timestamp == bars[attack].timestamp]
    assert [str(signal.timestamp.date()) for signal in signals] == ['2019-01-11']
    assert signals[0].reference_price == bars[dates['2019-01-11']].open
    assert not any(signal.bar_index == now and signal.side == 'LONG' for signal in result.signals)


def test_real_signal_and_orders_keep_prefix_and_opening_ohlcv_independence(xinhua_opening):
    from dataclasses import asdict

    bars, config, execution, result = xinhua_opening
    now = next(i for i, bar in enumerate(bars) if str(bar.timestamp.date()) == '2019-01-11')
    prefix = bars[:now + 1]
    replay = generate_system_signals(prefix, config)
    assert replay.signals == [signal for signal in result.signals if signal.bar_index <= now]
    changed = [*prefix[:-1], replace(prefix[-1], high=prefix[-1].open, low=5.0, close=5.0, volume=1)]
    altered = generate_system_signals(changed, config)
    opening = [signal for signal in replay.signals if signal.side == 'LONG' and signal.bar_index == now]
    assert [signal for signal in altered.signals if signal.side == 'LONG' and signal.bar_index == now] == opening
    base = single_stock_result(prefix, asdict(config), dict(execution, staged_exit_intraday=False,
                               consolidation_entry_intraday=False), replay)
    second = single_stock_result(changed, asdict(config), dict(execution, staged_exit_intraday=False,
                                 consolidation_entry_intraday=False), altered)
    order = next(order for order in base['orders'] if order['side'] == 'BUY'
                 and order['timestamp'].startswith('2019-01-11'))
    assert order['status'] == 'filled'
    assert order['execution_model'] == 'same_day_open'
    assert order['raw_price'] == pytest.approx(5.38 * 1.0005)
    assert order == next(order for order in second['orders'] if order['side'] == 'BUY'
                         and order['timestamp'].startswith('2019-01-11'))
    assert all(condition['passed'] is not False for condition in order['entry_conditions'])


def test_opening_purchase_is_not_erased_by_a_later_exit_and_remains_t_plus_one():
    bars, signal, config = opening_sample()
    exit_signal = replace(signal, side='EXIT', reason='wave_two_t_body_volume_clear',
                          reference_price=bars[-1].close, target_price=None)
    result = run_portfolio({bars[0].symbol: bars}, [signal, exit_signal], config)
    assert result.orders[0]['side'] == 'BUY' and result.orders[0]['status'] == 'filled'
    assert not any(order['side'] == 'SELL' and order['status'] == 'filled' for order in result.orders)
    assert result.open_positions


@pytest.mark.parametrize('gate', ['five_top_entry_history', 'ten_full_entry_history'])
def test_known_target_guard_blocks_the_new_opening_channel(monkeypatch, xinhua_opening, gate):
    bars, config, _, _ = xinhua_opening
    now = next(i for i, bar in enumerate(bars) if str(bar.timestamp.date()) == '2019-01-11')
    prefix = bars[:now + 1]
    risk = dict(reason='test_known_target_guard', attack=1)
    monkeypatch.setattr('wavequant.domain.strategies.integrated_strategy.' + gate,
                        lambda source, *args, **kwargs: {len(source) - 1: risk})
    replay = generate_system_signals(prefix, config)
    assert not any(signal.side == 'LONG' and signal.bar_index == now for signal in replay.signals)
    assert any(event['event'] == 'entry_rejected' and event['bar_index'] == now
               and event.get('candidate_channel') == 'n_opening_gap_squeeze'
               and event['reason'] == 'test_known_target_guard' for event in replay.audit)


def test_disabled_opening_profile_preserves_the_previous_real_close_buy(xinhua_opening):
    bars, config, _, _ = xinhua_opening
    previous = generate_system_signals(bars, replace(config, opening_gap_squeeze_enabled=False))
    longs = [signal for signal in previous.signals if signal.side == 'LONG'
             and str(signal.trigger_timestamp.date()) == '2019-01-07']
    assert [str(signal.timestamp.date()) for signal in longs] == ['2019-01-17']
    assert longs[0].reference_price == bars[-2].close

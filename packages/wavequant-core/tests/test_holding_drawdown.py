"""Independent entry-cost MAE checks, including executable holding boundaries."""
from datetime import datetime, time
import random

import pytest

from wavequant.application.analytics.holding_drawdown import holding_drawdowns, drawdown_metrics
from wavequant.domain.models.model import Bar
from wavequant.infrastructure.market_data.minute import MinuteBar


def bar(day, opening=10, high=20, low=9, close=12, symbol='TEST', factor=1):
    return Bar(datetime.fromisoformat(f'2026-01-{day:02}'), symbol, opening, high, low, close, 4800,
               adjustment_factor=factor)


def fill(day, side='BUY', price=10, quantity=100, model='next_open', symbol='TEST', **extra):
    return dict(timestamp=f'2026-01-{day:02}T00:00:00', symbol=symbol, side=side, status='filled',
                price=price, quantity=quantity, execution_model=model, **extra)


def measure(bars, orders, **kwargs):
    return holding_drawdowns({'TEST': bars}, orders, **kwargs)


def test_entry_cost_loss_excludes_profit_giveback():
    episodes = measure([bar(1, low=10), bar(2, low=11), bar(3, opening=12)],
                       [fill(1), fill(3, 'SELL', price=12)])
    assert episodes[0]['max_drawdown'] == 0
    assert drawdown_metrics(episodes)['holding_max_drawdown'] == 0


def test_close_entry_and_open_exit_exclude_outside_extremes():
    episodes = measure([bar(1, high=100, low=1, close=10), bar(2, low=8), bar(3, low=1)],
                       [fill(1, model='same_day_close'), fill(3, 'SELL', price=9)])
    e = episodes[0]
    assert e['max_drawdown'] == pytest.approx(-.2)
    assert (e['cost_price'], e['low_price'], e['loss_amount']) == (10, 8, -200)
    assert e['low_time'] == '2026-01-02'
    assert e['entry_time'].endswith('15:00:00')
    assert e['exit_time'].endswith('09:30:00')


def test_close_exit_includes_exit_day_low_and_actual_slipped_exit():
    orders = [fill(1, model='same_day_close'), fill(2, 'SELL', price=7.9, model='same_day_close')]
    e = measure([bar(1, low=1), bar(2, low=8)], orders)[0]
    assert e['max_drawdown'] == pytest.approx(-.21)
    assert e['low_source'] == 'fill'


def test_close_entry_marks_slippage_loss_without_borrowing_earlier_low():
    e = measure([bar(1, low=1, close=10)], [fill(1, price=10.01, model='same_day_close')])[0]
    assert e['max_drawdown'] == pytest.approx(10 / 10.01 - 1)
    assert e['low_source'] == 'daily_close'
    assert e['low_price'] == 10


def test_flat_period_and_reentry_reset_cost():
    bars = [bar(1, low=10), bar(2, opening=11), bar(3, low=1), bar(4, opening=20, high=22, low=19, close=21), bar(5, opening=21, high=22, close=21)]
    episodes = measure(bars, [fill(1), fill(2, 'SELL', price=11), fill(4, price=20), fill(5, 'SELL', price=21)])
    assert [e['max_drawdown'] for e in episodes] == pytest.approx([0, -.05])
    assert drawdown_metrics(episodes)['holding_drawdown_interval'] is episodes[1]


def test_partial_sells_keep_one_holding_and_current_quantity():
    episodes = measure([bar(1, low=9), bar(2, low=7), bar(3, low=6), bar(4)],
                       [fill(1), fill(2, 'SELL', quantity=50), fill(4, 'SELL', quantity=50)])
    assert len(episodes) == 1
    assert episodes[0]['max_drawdown'] == pytest.approx(-.4)
    assert episodes[0]['loss_amount'] == -200  # 50 remaining shares at the worst percentage loss.
    assert episodes[0]['cost_time'] == '2026-01-01T09:30:00'


def test_addon_cost_is_causal_and_uses_remaining_shares():
    orders = [fill(1), fill(2, 'SELL', quantity=50), fill(3, price=20, quantity=50), fill(5, 'SELL', price=15)]
    bars = [bar(1, low=8), bar(2), bar(3, opening=20, high=22, low=13, close=20), bar(4, opening=15, low=12, close=15), bar(5, opening=15)]
    e = measure(bars, orders)[0]
    assert len(measure(bars, orders)) == 1
    # Before the add-on: 8/10-1; afterward: 12/15-1. Equal losses keep the earlier observation.
    assert e['max_drawdown'] == pytest.approx(-.2)
    assert e['cost_price'] == 10
    assert e['low_time'] == '2026-01-01'
    deeper = measure([*bars[:3], bar(4, opening=15, low=10, close=15), bars[4]], orders)[0]
    assert deeper['cost_price'] == 15
    assert deeper['max_drawdown'] == pytest.approx(10 / 15 - 1)


def test_open_holding_respects_prefix_and_nontrading_asof():
    bars = [bar(2, low=9), bar(5, low=8), bar(6, low=1)]
    prefix = measure(bars[:2], [fill(2)], end='2026-01-05')[0]
    replay = measure(bars, [fill(2), fill(6, 'SELL', price=1)], end='2026-01-05')[0]
    assert replay == prefix
    weekend = measure(bars, [fill(2)], end='2026-01-04')[0]
    assert weekend['asof'] == '2026-01-02T15:00:00'
    assert weekend['max_drawdown'] == pytest.approx(-.1)
    assert prefix['status'] == 'open'


def test_no_fills_is_unknown_not_zero_and_cancelled_orders_are_ignored():
    orders = [dict(fill(1), status='cancelled'), dict(fill(2, 'SELL'), status='deferred')]
    metrics = drawdown_metrics(measure([bar(1), bar(2)], orders))
    assert metrics['holding_max_drawdown'] is None
    assert metrics['holding_drawdown_status'] == 'no_entry_fills'
    assert metrics['holding_drawdown_interval'] is None


def test_missing_intraday_boundary_returns_lower_bound_not_full_day_low():
    orders = [fill(1, model='intraday_5m_next_open', execution_timestamp='2026-01-01T14:00:00')]
    e = measure([bar(1, low=1, close=9)], orders)[0]
    assert e['max_drawdown'] is None
    assert e['observed_max_drawdown'] == pytest.approx(-.1)
    assert e['missing_sessions'] == ['2026-01-01']
    assert drawdown_metrics([e])['holding_max_drawdown'] is None


def minute_day(day, lows, factor=1):
    times = [time(9, m) for m in range(35, 60, 5)]
    times += [time(h, m) for h in (10, 14) for m in range(0, 60, 5)]
    times += [time(11, m) for m in range(0, 35, 5)]
    times += [time(13, m) for m in range(5, 60, 5)] + [time(15)]
    return [MinuteBar(datetime.combine(datetime(2026, 1, day), stamp), 10/factor, 10/factor,
                      lows.get(stamp, 10)/factor, 10/factor, 100) for stamp in sorted(times)]


def test_verified_minutes_clip_both_boundaries_and_convert_raw_prices():
    bars = [bar(1, high=10, low=1, close=10, factor=2)]
    minutes = minute_day(1, {time(10): 1, time(14, 5): 8, time(14, 30): 2}, factor=2)
    orders = [fill(1, model='intraday_5m_next_open', execution_timestamp='2026-01-01T14:00:00+08:00'),
              fill(1, 'SELL', model='intraday_5m_next_open', execution_timestamp='2026-01-01T14:15:00+08:00')]
    e = measure(bars, orders, minute_loader=lambda b: minutes)[0]
    assert e['coverage'] == 'complete'
    assert e['max_drawdown'] == pytest.approx(-.2)
    assert e['low_price'] == 8
    assert e['low_source'] == 'verified_5m_low'


@pytest.mark.parametrize('bad', ['partial', 'wrong_day', 'volume', 'extrema'])
def test_invalid_boundary_minutes_do_not_claim_complete_coverage(bad):
    bars = [bar(1, high=10, low=8, close=10)]
    minutes = minute_day(1, {time(14, 5): 8})
    if bad == 'partial': minutes = minutes[:-1]
    if bad == 'wrong_day': minutes = minute_day(2, {time(14, 5): 8})
    if bad == 'volume': bars = [Bar(datetime(2026, 1, 1), 'TEST', 10, 10, 8, 10, 10000)]
    if bad == 'extrema': bars = [bar(1, high=20, low=8, close=10)]
    order = fill(1, model='intraday_5m_next_open', execution_timestamp='2026-01-01T14:00:00')
    assert measure(bars, [order], minute_loader=lambda b: minutes)[0]['coverage'] == 'incomplete'


def test_independent_oracle_for_daily_single_fill_holdings():
    rng = random.Random(231)
    for _ in range(80):
        entry, exit_price = rng.uniform(8, 12), rng.uniform(8, 12)
        lows = [rng.uniform(5, 10) for _ in range(4)]
        bars = [bar(1, close=entry)] + [bar(i + 2, low=p) for i, p in enumerate(lows)] + [bar(6, opening=exit_price)]
        e = measure(bars, [fill(1, price=entry, model='same_day_close'), fill(6, 'SELL', price=exit_price)])[0]
        expected = min(0, (min(entry, exit_price, *lows) - entry) / entry)
        assert e['max_drawdown'] == pytest.approx(expected)


def test_unmatched_sell_rejected():
    with pytest.raises(ValueError, match='matching holding'):
        measure([bar(1)], [fill(1, 'SELL')])

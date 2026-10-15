"""信号状态机一次消费一天，异常后不能沿用部分消耗的买点状态。"""

import pytest

from wavequant.domain.strategies.signal_clock import SignalClock


def test_signal_clock_preserves_order_and_never_duplicates_a_consumed_day():
    days = []
    clock = SignalClock(3, days.append)
    clock.advance(0)
    with pytest.raises(ValueError, match='consecutive'):
        clock.advance(0)
    with pytest.raises(ValueError, match='consecutive'):
        clock.advance(2)
    clock.advance(1)
    clock.advance(2)
    clock.finish()
    assert days == [0, 1, 2]
    with pytest.raises(ValueError, match='consecutive'):
        clock.advance(3)


def test_failed_consumer_cannot_repeat_partially_emitted_signals():
    days = []

    def consume(index):
        days.append(index)
        raise RuntimeError('policy failed')

    clock = SignalClock(2, consume)
    with pytest.raises(RuntimeError, match='policy failed'):
        clock.advance(0)
    with pytest.raises(RuntimeError, match='cannot be reused'):
        clock.advance(0)
    with pytest.raises(RuntimeError, match='not completed'):
        clock.finish()
    assert days == [0]


def test_finishing_a_partial_market_does_not_claim_a_complete_backtest():
    clock = SignalClock(2, lambda index: None)
    clock.advance(0)
    with pytest.raises(RuntimeError, match='not completed'):
        clock.finish()
    SignalClock(0, lambda index: None).finish()


def test_recursive_consumer_cannot_observe_the_same_date_twice():
    def consume(index):
        clock.advance(index)

    clock = SignalClock(1, consume)
    with pytest.raises(RuntimeError, match='recursively'):
        clock.advance(0)

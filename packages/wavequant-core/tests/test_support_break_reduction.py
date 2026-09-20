import pytest
from datetime import datetime

from wavequant.domain.strategies.staged_exit import closing_reduction_intent, support_break_reduction


@pytest.mark.parametrize(
    ("low", "price", "expected"),
    [
        (12.42, 12.57, 0.65),
        (12.42, 12.70, 0.35),
        (12.42, 12.68, 0.35),
        (12.43, 12.57, 0.0),
        (12.44, 12.57, 0.0),
    ],
)
def test_ruiling_low_is_required_and_close_break_selects_cumulative_target(low, price, expected):
    assert support_break_reduction(low, price, 12.43, 12.68) == expected


@pytest.mark.parametrize("low", [float("nan"), float("inf"), 0, -1, True])
def test_invalid_market_prices_cannot_generate_reduction(low):
    with pytest.raises(ValueError):
        support_break_reduction(low, 12.57, 12.43, 12.68)


def test_inconsistent_low_and_latest_price_are_rejected():
    with pytest.raises(ValueError):
        support_break_reduction(12.60, 12.57, 12.43, 12.68)


def closing(**overrides):
    args = dict(
        observed_at=datetime.fromisoformat("2025-05-15T14:30:00+08:00"),
        day_low=12.42,
        current_price=12.70,
        support_low=12.43,
        support_close=12.68,
        original_quantity=10000,
        filled_quantity=0,
        pending_quantity=0,
        sellable_quantity=10000,
    )
    args.update(overrides)
    return closing_reduction_intent(**args)


def test_sell_35_then_only_add_30_when_price_also_breaks():
    first = closing()
    assert (first.target_fraction, first.order_quantity) == (0.35, 3500)
    second = closing(current_price=12.57, filled_quantity=3500, sellable_quantity=6500, previous_target=0.35)
    assert (second.target_fraction, second.target_quantity, second.order_quantity) == (0.65, 6500, 3000)
    assert closing(current_price=12.57, filled_quantity=6500, sellable_quantity=3500, previous_target=0.65) is None


def test_pending_orders_are_reserved_and_rejections_can_retry_without_fictitious_fills():
    assert closing(pending_quantity=3500) is None
    assert closing(current_price=12.57, pending_quantity=3500).order_quantity == 3000
    assert closing(current_price=12.57, filled_quantity=1000, pending_quantity=5500, sellable_quantity=9000) is None
    assert (
        closing(current_price=12.57, filled_quantity=1000, sellable_quantity=9000, previous_target=0.65).order_quantity
        == 5500
    )


@pytest.mark.parametrize("clock", ["14:29:59+08:00", "15:00:00+08:00", "15:01:00+08:00", "06:29:59+00:00"])
def test_outside_closing_window_never_generates_an_order(clock):
    assert closing(observed_at=datetime.fromisoformat("2025-05-15T" + clock)) is None


def test_utc_quote_is_converted_and_daily_timestamp_is_rejected():
    assert closing(observed_at=datetime.fromisoformat("2025-05-15T06:30:00+00:00")).order_quantity == 3500
    with pytest.raises(ValueError, match="timezone"):
        closing(observed_at=datetime(2025, 5, 15))


def test_unsellable_and_small_lot_do_not_force_liquidation():
    assert closing(sellable_quantity=0) is None
    assert closing(original_quantity=100, sellable_quantity=100) is None
    assert closing(sellable_quantity=1000).order_quantity == 1000
    assert closing(original_quantity=9300, sellable_quantity=9300, current_price=12.57).order_quantity == 6000

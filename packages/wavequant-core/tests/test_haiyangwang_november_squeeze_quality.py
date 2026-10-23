"""用户报价与已保存同源行情：资格与买点复用相同轧空K线质量。"""
from dataclasses import replace
from datetime import datetime

from wavequant.domain.models.model import Bar
from wavequant.domain.market_state.candle_strength import medium_bullish_body
from wavequant.domain.market_structure.squeeze_alternation import _volume_squeeze_close
from wavequant.domain.strategies.attack_quality import squeeze_buy_rejection


def november_bars() -> list[Bar]:
    # 11/3、11/4来自既有同源研究JSON；11/2和11/5使用用户报价。
    return [
        Bar(datetime(2021, 11, 1), 'sz.002724', 19.2572386081, 19.7811581720,
            18.9888895631, 19.6533729125, 1322677),
        Bar(datetime(2021, 11, 2), 'sz.002724', 19.54, 20.18, 19.23, 19.32, 1382500),
        Bar(datetime(2021, 11, 3), 'sz.002724', 19.3211312378, 19.7428225942,
            19.3211312378, 19.4233594454, 1303800),
        Bar(datetime(2021, 11, 4), 'sz.002724', 19.4233594454, 20.0239501651,
            19.4105809195, 19.9472790094, 1695100),
        Bar(datetime(2021, 11, 5), 'sz.002724', 20.13, 21.11, 19.56, 20.87, 4038400),
    ]


def test_november5_gap_squeeze_does_not_require_half_range_body():
    bars = november_bars()
    assert not medium_bullish_body(bars[-1])
    assert bars[-1].open > bars[-2].high
    assert squeeze_buy_rejection(bars[-1], bars[-2], bars[0]) is None
    assert _volume_squeeze_close(bars, 1, 4)
    assert not _volume_squeeze_close(bars[:-1], 1, 4)


def test_gap_exception_still_rejects_bearish_no_volume_and_unrecovered_n_close():
    bars = november_bars()
    for change in (dict(close=bars[-1].open), dict(volume=0), dict(close=19.32)):
        changed = [*bars[:-1], replace(bars[-1], **change)]
        assert not _volume_squeeze_close(changed, 1, 4)


def test_double_volume_squeeze_body_exact_45_percent_and_rejection_boundaries():
    from wavequant.domain.strategies.attack_quality import squeeze_bullish_body

    previous = Bar(datetime(2021, 11, 4), 'TEST', 99, 101, 98, 100, 100)
    current = Bar(datetime(2021, 11, 5), 'TEST', 100, 106, 96, 104.5, 200)
    assert squeeze_bullish_body(current, previous)
    assert not medium_bullish_body(current)
    assert squeeze_buy_rejection(current, previous) is None
    assert not squeeze_bullish_body(replace(current, close=104.499), previous)
    assert not squeeze_bullish_body(replace(current, volume=199), previous)
    assert not squeeze_bullish_body(current, replace(previous, volume=0))
    assert not squeeze_bullish_body(current, None)
    assert not squeeze_bullish_body(current, replace(previous, timestamp=current.timestamp))
    assert squeeze_bullish_body(replace(current, close=105, volume=150), previous)
    # 小实体不能只凭占振幅45%放行，原2%开盘价门槛继续生效。
    assert not squeeze_bullish_body(replace(current, high=101, low=99, close=100.9), previous)

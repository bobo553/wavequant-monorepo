"""Track dated pullback-resumption evidence separately from fresh record events.

The six-regime observer is unchanged. This explicit strategy hypothesis tests
the lecture's 'pull back without breaking squeeze low, then rise again' branch.
"""
from dataclasses import dataclass

from .market_regime import MarketRegime


@dataclass(frozen=True)
class SqueezeResumption:
    bar_index: int
    prior_confirmation_index: int
    attack_index: int
    defense: float
    local_resistance: float
    regime: MarketRegime = MarketRegime.BULL


def observe_squeeze_resumption(bars, i, *, frame, offset, attack_index, defense):
    """Require a real earlier squeeze, held defense, pullback AND fresh bounce.

    Caller still enforces the full bearish-to-bullish transition, same bullish
    episode on attack/current dates, volume, target/risk and order execution.
    No historical label alone and no rolling-stop tightening are permitted.
    """
    if frame.bar_index+offset != i:
        raise ValueError('regime frame must belong to observation date')
    if (frame.regime is not None or frame.last_confirmed_regime not in
            (MarketRegime.BULL, MarketRegime.STRONG_BULL) or frame.last_confirmed_index is None
            or frame.first_defense_breach_index is not None or frame.first_wave_breach_index is not None):
        return None
    known = offset+frame.last_confirmed_index
    if not attack_index+2 <= known < i-1:
        return None
    bar, prev, confirmed = bars[i], bars[i-1], bars[known]
    if (prev.close >= confirmed.close or prev.low < defense or bar.low < defense
            or bar.low < prev.low or bar.close <= prev.high or bar.close <= bar.open):
        return None
    return SqueezeResumption(i, known, attack_index, defense, prev.high)

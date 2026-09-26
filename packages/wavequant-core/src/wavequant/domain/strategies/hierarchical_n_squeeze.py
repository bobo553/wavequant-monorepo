"""Confirm a larger positive N at its known last-fall-high breakout."""

from ..market_structure.price_action import Direction


def hierarchical_n_squeeze(bars, candidate):
    """Require a forceful, volume-backed close at or above the N neckline.

    The N neckline is the confirmed high before its later pullback low.  An
    intraday probe past that high followed by a close back at it still counts
    as a held squeeze; a close below it does not.  The candidate's structural
    points must already be known on the attack date.
    """
    setup = candidate['setup']
    attack = candidate['attack']
    if (setup.direction != Direction.UP or candidate['n_level'] < 1
            or candidate['known_at'] > attack or attack <= setup.pullback.index
            or attack >= len(bars)):
        return None
    bar, previous = bars[attack], bars[attack - 1]
    key = candidate['n'].anchors.neckline_extreme
    span = bar.high - bar.low
    if (bar.high <= key or bar.close < key or previous.close >= key
            or bar.close <= bar.open or bar.volume <= previous.volume
            or span <= 0 or (bar.close - bar.low) / span < .75):
        return None
    return dict(
        trend_level=candidate['n_level'],
        trend_key_date=bars[setup.neckline.index].timestamp.date().isoformat(),
        trend_key_high=key,
        trend_pullback_date=bars[setup.pullback.index].timestamp.date().isoformat(),
        trend_attack_date=bar.timestamp.date().isoformat(),
        n_origin_date=bars[setup.origin.index].timestamp.date().isoformat(),
    )

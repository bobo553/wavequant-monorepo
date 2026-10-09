"""Cache drawing artifacts for an immutable prefix and trend rule selection."""
from dataclasses import asdict
import hashlib

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.infrastructure.persistence.artifact_cache import canonical


def cached_geometry(cache,bars,price_basis,engine,*,n_target_trend_confirmation_enabled: bool = False):
    if type(n_target_trend_confirmation_enabled) is not bool:
        raise ValueError('n_target_trend_confirmation_enabled must be a boolean')
    rows=[dict(asdict(b),timestamp=b.timestamp.isoformat()) for b in bars]
    key=dict(bars_sha256=hashlib.sha256(canonical(rows).encode()).hexdigest(),
             price_basis=price_basis,engine=engine,
             n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
    with cache.lock(cache.key('geometry',key)):
        result=cache.get('geometry',key)
        if result is None:
            drawing=lecture_drawing(bars)
            first=reversal_trends(drawing,bars,n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
            second=secondary_trends(first,bars,n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
            result=dict(lecture_drawing=drawing,reversal_trends=first,
                        secondary_trends=second,tertiary_trends=tertiary_trends(second,bars,
                            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled))
            cache.put('geometry',key,result)
        return result

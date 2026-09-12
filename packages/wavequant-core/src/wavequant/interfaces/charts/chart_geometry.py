"""Present strategy-independent drawing artifacts for an immutable bar prefix."""
from dataclasses import asdict
import hashlib

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.infrastructure.persistence.artifact_cache import canonical


def cached_geometry(cache,bars,price_basis,engine):
    rows=[dict(asdict(b),timestamp=b.timestamp.isoformat()) for b in bars]
    key=dict(bars_sha256=hashlib.sha256(canonical(rows).encode()).hexdigest(),
             price_basis=price_basis,engine=engine)
    with cache.lock(cache.key('geometry',key)):
        result=cache.get('geometry',key)
        if result is None:
            drawing=lecture_drawing(bars)
            first=reversal_trends(drawing,bars);second=secondary_trends(first,bars)
            result=dict(lecture_drawing=drawing,reversal_trends=first,
                        secondary_trends=second,tertiary_trends=tertiary_trends(second,bars))
            cache.put('geometry',key,result)
        return result

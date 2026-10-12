"""Cache drawing artifacts for an immutable prefix and trend rule selection."""
from dataclasses import asdict
import hashlib

from wavequant.domain.market_structure.trend_pipeline import calculate_trend_hierarchy
from wavequant.infrastructure.persistence.artifact_cache import canonical


def cached_geometry(cache,bars,price_basis,engine,*,n_target_trend_confirmation_enabled: bool = False):
    if type(n_target_trend_confirmation_enabled) is not bool:
        raise ValueError('n_target_trend_confirmation_enabled must be a boolean')
    rows=[dict(asdict(b),timestamp=b.timestamp.isoformat()) for b in bars]
    key=dict(bars_sha256=hashlib.sha256(canonical(rows).encode()).hexdigest(),
             price_basis=price_basis,engine=engine, computation_policy='complete_level_polyline_owned_closed_source_v120',
             n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
    with cache.lock(cache.key('geometry',key)):
        result=cache.get('geometry',key)
        if result is None:
            # 缓存与无缓存图表共用完整层级编排，不能保存旧逐路径/跨级中间结果。
            result=calculate_trend_hierarchy(bars,
                n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
            cache.put('geometry',key,result)
        return result

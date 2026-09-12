"""Summarize scanner events into a compact, presentation-neutral funnel.

The funnel intentionally counts evidence emitted by the strategy instead of
re-evaluating the strategy. Keeping this aggregation in the application layer
prevents dashboards and reports from inventing subtly different rule semantics.
"""
from collections import Counter


def funnel(view: dict, lookback: int) -> dict:
    """Aggregate dated strategy evidence inside the requested trailing window.

    The returned zero/one flags mean that qualifying evidence was observed, not
    that a trade should be placed. In particular, ``has_squeeze`` is derived from
    an emitted regime-confirmation event so this reporting helper cannot bypass
    the strategy's causal confirmation rules.
    """

    bars = view['bars']
    cutoff = bars[-min(lookback, len(bars))]['time'] if bars else '9999'
    events = [event for event in view.get('audit', []) if event['timestamp'][:10] >= cutoff]
    counts = Counter(event['event'] for event in events)
    rejection_reasons = Counter(
        event.get('reason', 'unknown')
        for event in events
        if event['event'] in ('entry_rejected', 'entry_preflight_rejected')
    )

    has_long_signal = any(
        signal['side'] == 'LONG'
        and signal.get('time', signal.get('timestamp', '')[:10]) >= cutoff
        for signal in view['signals']
    )
    has_confirmed_squeeze = any(
        event['event'] == 'regime_confirmation'
        and event.get('regime') in ('轧空', '强轧空')
        for event in events
    )

    return {
        'events': dict(counts),
        'rejections': dict(rejection_reasons),
        'no_long_signal': int(not has_long_signal),
        'structure_interrupted': int(bool(counts['strict_structure_interrupted'])),
        'has_n': int(bool(counts['n_completed'])),
        'has_squeeze': int(has_confirmed_squeeze),
    }

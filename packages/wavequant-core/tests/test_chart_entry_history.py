from wavequant.domain.strategies.chart_entry_history import chart_entry_history
from tests.test_lecture_strategy import history


def test_chart_entry_contexts_are_prefix_stable():
    bars = history(18, 230)
    full, events = chart_entry_history(bars)
    assert events
    for stop in (70, 110, 170, 210):
        short, short_events = chart_entry_history(bars[: stop + 1])
        assert short == {i: contexts for i, contexts in full.items() if i <= stop}
        assert short_events == [event for event in events if event["bar_index"] <= stop]
    for i, contexts in full.items():
        for ctx in contexts:
            assert ctx.origin_index < ctx.flip_high_index < ctx.alternation_low_index
            assert ctx.flip_index <= ctx.alternation_index <= i
            assert (
                0 < (ctx.flip_high_price - ctx.alternation_low_price) / (ctx.flip_high_price - ctx.origin_price) < 2 / 3
            )

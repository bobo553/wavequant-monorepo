"""开盘小缺口撞入历史下降缺口的因果买入门禁。"""

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from ..models.model import Bar


@dataclass(frozen=True)
class _Gap:
    index: int
    lower: Fraction
    upper: Fraction


class GapEntryGate:
    """按时间推进未回补缺口栈；每个缺口只入栈、出栈一次。"""

    def __init__(self) -> None:
        self.index = -1
        self._gaps: list[_Gap] = []
        self.restrictions: dict[int, dict[str, object]] = {}

    def advance(self, bars: Sequence[Bar], index: int) -> dict[str, object] | None:
        if index < self.index:
            return self.restrictions.get(index)
        for day in range(self.index + 1, index + 1):
            bar = bars[day]
            if not day:
                continue
            previous = bars[day - 1]
            if bar.symbol != previous.symbol or bar.timestamp <= previous.timestamp:
                raise ValueError('gap entry gate requires ordered single-symbol bars')
            opened = Fraction(str(bar.open))
            prior_high, prior_low = Fraction(str(previous.high)), Fraction(str(previous.low))
            # 先检查开盘，再让当天完整行情回补历史缺口，防止日内高点改写开盘决定。
            while self._gaps and opened >= self._gaps[-1].upper:
                self._gaps.pop()
            if self._gaps and opened > prior_high:
                gap = self._gaps[-1]
                width = opened - prior_high
                if gap.lower <= opened < gap.upper and width < gap.upper - gap.lower:
                    self.restrictions[day] = dict(reason='up_gap_smaller_than_adjacent_down_gap',
                        down_gap_index=gap.index,
                        down_gap_date=bars[gap.index].timestamp.date().isoformat(),
                        down_gap_lower=float(gap.lower), down_gap_upper=float(gap.upper),
                        down_gap_width=float(gap.upper - gap.lower), up_gap_width=float(width),
                        observed_open=bar.open, previous_high=previous.high,
                        decision_timestamp=bar.timestamp.replace(hour=9, minute=30).isoformat())
            high = Fraction(str(bar.high))
            while self._gaps and high >= self._gaps[-1].upper:
                self._gaps.pop()
            if opened < prior_low and bar.close < bar.open and high < prior_low:
                self._gaps.append(_Gap(day, opened, prior_low))
        self.index = max(self.index, index)
        return self.restrictions.get(index)

"""一级趋势底部成功 N 的永久归属；资格窗口不产生新的测幅身份。"""

from bisect import bisect_left, bisect_right, insort
from collections.abc import Sequence
from dataclasses import dataclass

from ..models.model import Bar
from .bottom_n_targets import DeclineStart
from .trend_market_index import _MarketRangeIndex


STRUCTURAL_N_POLICY = 'level_one_successful_bottom_n_v2'


@dataclass(frozen=True)
class StructuralNSelection:
    selected: bool
    owner: str | None
    trend_id: str | None
    decline_index: int | None
    bottom_index: int | None
    reason: str


class StructuralNOwnership:
    """只消费已确认一级下降起点；成功后的归属不因破位、重开窗而释放。"""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self.bars = tuple(bars)
        self.market = _MarketRangeIndex.build(self.bars)
        self.declines: dict[int, list[int]] = {}
        self.owners: dict[tuple[int, int], tuple[str, int]] = {}
        self.owner_starts: dict[int, list[int]] = {}
        self.index = -1
        self.epoch = 0

    def observe(self, index: int, epoch: int, declines: Sequence[DeclineStart]) -> None:
        if type(index) is not int or not self.index <= index < len(self.bars):
            raise ValueError('structural N ownership requires a known market prefix')
        if type(epoch) is not int or epoch < self.epoch:
            raise ValueError('structural N ownership requires a current epoch')
        if any(not 0 <= decline.index <= decline.known_at <= index for decline in declines):
            raise ValueError('structural N ownership requires known level-one declines')
        self.index, self.epoch = index, epoch
        starts = self.declines.setdefault(epoch, [])
        for decline in declines:
            position = bisect_left(starts, decline.index)
            if position == len(starts) or starts[position] != decline.index:
                insort(starts, decline.index)

    def select(self, *, epoch: int, origin: int, attack: int, identity: str) -> StructuralNSelection:
        if (type(origin) is not int or type(attack) is not int or epoch != self.epoch
                or not 0 <= origin < attack <= self.index or not identity):
            raise ValueError('structural N selection requires a completed known N in the current epoch')
        starts = self.declines.get(epoch, [])
        position = bisect_left(starts, origin) - 1
        if position < 0:
            return StructuralNSelection(False, None, None, None, None, 'confirmed_decline_unavailable')
        decline = starts[position]
        # 后知的下降证明位于已冻结 N 内部时，不能把同一启动段拆成第二个名额。
        owned_starts = self.owner_starts.setdefault(epoch, [])
        earlier = bisect_right(owned_starts, decline) - 1
        if earlier >= 0:
            prior_decline = owned_starts[earlier]
            if decline <= self.owners[epoch, prior_decline][1]:
                decline = prior_decline
        trend_id = f'level-one:{epoch}:{decline}'
        owner = self.owners.get((epoch, decline))
        if owner is not None:
            return StructuralNSelection(False, owner[0], trend_id, decline, None, 'trend_already_has_successful_n')
        following = bisect_right(starts, origin)
        if following < len(starts) and starts[following] <= attack:
            return StructuralNSelection(False, None, trend_id, decline, None, 'crosses_confirmed_decline')
        bottom = (self.market.extreme_position(decline + 1, attack + 1, high=False)
                  if self.market is not None else
                  min(range(decline + 1, attack + 1), key=lambda day: self.bars[day].low))
        if origin != bottom:
            return StructuralNSelection(False, None, trend_id, decline, bottom, 'origin_is_not_decline_floor')
        self.owners[epoch, decline] = identity, attack
        insort(owned_starts, decline)
        return StructuralNSelection(True, identity, trend_id, decline, bottom, 'first_successful_floor_n')

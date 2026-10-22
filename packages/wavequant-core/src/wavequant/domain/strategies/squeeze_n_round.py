"""首个 N 持有轧空轮次；已消费的轮次不能因候选更名或卖出而重开。"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import cast

from .trading_intervals import confirmed_alternation_event


@dataclass(frozen=True)
class SqueezeN:
    origin: int
    neckline: int
    pullback: int
    attack: int
    known_at: int
    defense: float

    @property
    def identity(self) -> tuple[int, int, int, int]:
        return self.origin, self.neckline, self.pullback, self.attack


def joint_squeeze_confirmations(
    events: Iterable[Mapping[str, object]], level: int,
) -> set[tuple[str, int, tuple[int, int, int, int]]]:
    """仅已发布的同级同路径资格证明中的原 N 可跨越自己的资格起点。"""
    result: set[tuple[str, int, tuple[int, int, int, int]]] = set()
    for event in events:
        path, now, proof = event.get('source_path'), event.get('bar_index'), event.get('confirmation_evidence')
        if (event.get('trend_level') != level or event.get('state') != '空多交替'
                or event.get('confirmation_rule') not in (
                    'positive_n_and_squeeze_after_qualified_b',
                    'double_volume_positive_n_squeeze_whole_flip_pullback')
                or not confirmed_alternation_event(event) or not isinstance(path, str)
                or type(now) is not int or not isinstance(proof, Mapping)):
            continue
        coordinates = tuple(proof.get(name) for name in ('n_origin', 'n_neckline', 'n_pullback', 'attack'))
        if any(type(value) is not int for value in coordinates):
            continue
        identity = cast(tuple[int, int, int, int], coordinates)
        origin, neckline, pullback, attack = identity
        if (0 <= origin <= neckline <= pullback <= attack <= now and origin < pullback
                and proof.get('source_path') == path and proof.get('trend_level') == level
                and proof.get('bar_index') == now and proof.get('candidate_index') == now):
            result.add((path, now, identity))
    return result


@dataclass(frozen=True)
class SqueezeRoundState:
    owner: SqueezeN | None = None
    selected_at: int | None = None
    emitted_at: int | None = None
    broken_at: int = -1

    def evidence(self) -> dict[str, object]:
        owner = self.owner
        return dict(
            squeeze_owner_origin=owner.origin if owner else None,
            squeeze_owner_neckline=owner.neckline if owner else None,
            squeeze_owner_pullback=owner.pullback if owner else None,
            squeeze_owner_attack=owner.attack if owner else None,
            squeeze_owner_known_at=owner.known_at if owner else None,
            squeeze_owner_defense=owner.defense if owner else None,
            squeeze_owner_selected_at=self.selected_at,
            squeeze_owner_emitted_at=self.emitted_at,
            squeeze_round_broken_at=self.broken_at,
        )

    @classmethod
    def before(cls, audit: Iterable[Mapping[str, object]], index: int) -> 'SqueezeRoundState':
        """分钟只继承前一日已发布的轮次，不借用当日收盘破低或消费结果。"""
        rows = [row for row in audit if row.get('event') == 'squeeze_round_changed'
                and _integer(row, 'bar_index') < index]
        if not rows:
            return cls()
        row = max(enumerate(rows), key=lambda item: (_integer(item[1], 'bar_index'), item[0]))[1]
        owner = None
        if row['squeeze_owner_attack'] is not None:
            owner = SqueezeN(
                _integer(row, 'squeeze_owner_origin'), _integer(row, 'squeeze_owner_neckline'),
                _integer(row, 'squeeze_owner_pullback'), _integer(row, 'squeeze_owner_attack'),
                _integer(row, 'squeeze_owner_known_at'), _number(row, 'squeeze_owner_defense'))
        return cls(owner,
            _integer(row, 'squeeze_owner_selected_at') if owner else None,
            _integer(row, 'squeeze_owner_emitted_at') if row['squeeze_owner_emitted_at'] is not None else None,
            _integer(row, 'squeeze_round_broken_at'))


def _integer(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if type(value) is not int:
        raise ValueError(f'{name} must be an integer')
    return value


def _number(row: Mapping[str, object], name: str) -> float:
    value = row[name]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f'{name} must be numeric')
    return float(value)


@dataclass
class SqueezeNRound:
    state: SqueezeRoundState = SqueezeRoundState()

    def close_unemitted(self) -> dict[str, object] | None:
        """未产生买点的候选随资格关闭；已消费轮次仍必须等真实破低。"""
        if self.state.owner is None or self.state.emitted_at is not None:
            return None
        prior = self.state.owner
        self.state = SqueezeRoundState(broken_at=self.state.broken_at)
        return dict(reason='unemitted_first_n_qualification_closed',
            retired_owner_attack=prior.attack, **self.state.evidence())

    def observe_low(self, index: int, low: float) -> dict[str, object] | None:
        owner = self.state.owner
        if owner is None or self.state.selected_at is None or index <= self.state.selected_at:
            return None
        if low >= owner.defense:
            return None
        prior = self.state
        self.state = SqueezeRoundState(broken_at=index)
        return dict(reason='first_n_squeeze_defense_broken',
            broken_owner_attack=owner.attack, broken_owner_defense=owner.defense,
            broken_owner_emitted_at=prior.emitted_at, observed_low=low, **self.state.evidence())

    def select(self, index: int, candidates: Iterable[SqueezeN]) -> dict[str, object] | None:
        if self.state.owner is not None:
            return None
        available = [n for n in candidates
            if self.state.broken_at < n.attack <= index and n.known_at <= index
            and (self.state.broken_at < 0 or n.pullback >= self.state.broken_at)]
        if not available:
            return None
        owner = min(available, key=lambda n: (max(n.attack, n.known_at), n.attack, n.identity))
        self.state = replace(self.state, owner=owner, selected_at=index)
        return dict(reason='first_n_selected', **self.state.evidence())

    def rejection(self, candidate: SqueezeN) -> str | None:
        owner = self.state.owner
        if owner is None:
            return 'squeeze_waiting_for_new_n_after_defense_break'
        if self.state.emitted_at is not None:
            return 'first_n_squeeze_already_emitted'
        if candidate.identity != owner.identity:
            return 'squeeze_requires_first_n'
        return None

    def consume(self, candidate: SqueezeN, index: int) -> dict[str, object]:
        if self.rejection(candidate) is not None:
            raise ValueError('only the unconsumed first N can emit a squeeze entry')
        self.state = replace(self.state, emitted_at=index)
        return dict(reason='first_n_squeeze_emitted', **self.state.evidence())

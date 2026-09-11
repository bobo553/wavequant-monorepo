"""Bitemporal instrument facts; absent facts never imply tradability."""
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
import json

from .event_store import utc


@dataclass(frozen=True)
class SecurityFact:
    symbol: str
    effective_from: str
    effective_to: str | None
    known_at: str
    sector: str
    active: bool
    suspended: bool
    st: bool
    buy_lot: int
    tick_size: str
    settlement_days: int
    source: str

    def __post_init__(self):
        first = date.fromisoformat(self.effective_from)
        if self.effective_to is not None and date.fromisoformat(self.effective_to) <= first:
            raise ValueError('effective interval must be half-open and ordered')
        utc(self.known_at)
        if not self.symbol or not self.sector or not self.source:
            raise ValueError('symbol, sector and evidence source required')
        if any(type(getattr(self,k)) is not bool for k in ('active','suspended','st')):
            raise ValueError('explicit status booleans required')
        if type(self.buy_lot) is not int or self.buy_lot <= 0:
            raise ValueError('positive raw-share buy lot required')
        tick = Decimal(self.tick_size)
        if not tick.is_finite() or tick <= 0:
            raise ValueError('positive finite price tick required')
        if type(self.settlement_days) is not int or self.settlement_days not in (0,1):
            raise ValueError('only explicit T+0/T+1 sell availability supported')


class SecurityMaster:
    def __init__(self, facts=()):
        self.facts = tuple(facts)
        if any(not isinstance(f, SecurityFact) for f in self.facts):
            raise ValueError('typed security facts required')
        keys = [(f.symbol,f.effective_from,utc(f.known_at)) for f in self.facts]
        if len(keys) != len(set(keys)):
            raise ValueError('duplicate/conflicting fact identity')

    @classmethod
    def load(cls, path):
        with open(path, encoding='utf-8') as handle:
            raw = json.load(handle)
        return cls(SecurityFact(**r) for r in raw)

    def at(self, symbol, session, asof):
        date.fromisoformat(session)
        cutoff = utc(asof)
        rows = [f for f in self.facts if f.symbol==symbol and f.effective_from<=session
                and utc(f.known_at)<=cutoff]
        # A later effective snapshot supersedes earlier status; corrections to
        # that snapshot apply only after the correction's actual known_at.
        latest=max(rows,key=lambda f:(f.effective_from,utc(f.known_at)),default=None)
        # An expired newer snapshot must not resurrect an older open-ended one.
        return latest if latest is not None and (latest.effective_to is None or session<latest.effective_to) else None

    def universe(self, session, asof, *, exclude_st=True):
        selected = []
        for symbol in sorted({f.symbol for f in self.facts}):
            f = self.at(symbol,session,asof)
            if f is not None and f.active and not f.suspended and (not exclude_st or not f.st):
                selected.append(symbol)
        return selected

    def snapshot(self):
        return [asdict(f) for f in self.facts]

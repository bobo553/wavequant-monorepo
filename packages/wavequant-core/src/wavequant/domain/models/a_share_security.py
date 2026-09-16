"""A-share board rules used by every execution-data adapter.

The symbol-to-board mapping belongs in the domain rather than an individual
provider: TDX, AkShare and future feeds may spell fields differently, but the
exchange board and its order/price-limit constraints are the same.  Historical
ST status is deliberately *not* inferred here; callers must supply a verified
point-in-time flag or refuse ST execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
import re


@dataclass(frozen=True)
class AShareSecuritySpec:
    """Execution constraints for one established A-share security session."""

    market: str
    board: str
    board_label: str
    price_limit_rate: Decimal
    minimum_buy_shares: int
    buy_share_step: int

    def to_dict(self) -> dict:
        value = asdict(self)
        value["price_limit_rate"] = float(self.price_limit_rate)
        return value


def a_share_security_spec(symbol: str, session: date | None = None) -> AShareSecuritySpec:
    """Resolve supported board rules without depending on a market-data source.

    ChiNext changed its ordinary daily price limit from 10% to 20% on
    2020-08-24.  IPO sessions remain outside the backtest model, so the
    no-price-limit listing windows do not appear here.
    """

    when = session or date.today()
    if re.fullmatch(r"sh\.60\d{4}", symbol):
        return AShareSecuritySpec("sh", "sh_main", "沪市主板", Decimal("0.10"), 100, 100)
    if re.fullmatch(r"sz\.00\d{4}", symbol):
        return AShareSecuritySpec("sz", "sz_main", "深市主板", Decimal("0.10"), 100, 100)
    if re.fullmatch(r"sz\.30\d{4}", symbol):
        rate = Decimal("0.20") if when >= date(2020, 8, 24) else Decimal("0.10")
        return AShareSecuritySpec("sz", "chinext", "创业板", rate, 100, 100)
    if re.fullmatch(r"sh\.68\d{4}", symbol):
        # STAR accepts orders from 200 shares and then one-share increments.
        return AShareSecuritySpec("sh", "star", "科创板", Decimal("0.20"), 200, 1)
    if re.fullmatch(r"bj\.(?:43|82|83|87|88|92)\d{4}", symbol):
        # BSE accepts orders from 100 shares and then one-share increments.
        return AShareSecuritySpec("bj", "bse", "北交所", Decimal("0.30"), 100, 1)
    raise ValueError(f"{symbol}: not a supported SH/SZ/BJ A-share symbol")


def is_supported_a_share(symbol: str) -> bool:
    try:
        a_share_security_spec(symbol)
    except ValueError:
        return False
    return True

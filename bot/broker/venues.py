"""Venue profiles: the rules and costs of a real venue, as data. A paper
account copies one of these, so its fills, fees and restrictions match
the place the money would really go."""

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


@dataclass(frozen=True)
class VenueProfile:
    key: str
    label: str
    kind: str  # "spot" | "cfd"
    quote_asset: str
    maker_fee: Decimal
    taker_fee: Decimal
    long_only: bool
    max_leverage: Decimal
    qty_step: Decimal
    min_qty: Decimal
    min_notional: Decimal
    # where paper quotes come from: "quidax" (real book) or "binance"
    # (price reference only, with `spread` applied around it)
    quote_source: str
    # total bid/ask spread as a fraction of price, used only when the quote
    # source has no real book (the Exness CFD profile)
    spread: Decimal
    # CFD overnight financing, as a fraction of notional per day, charged
    # on both long and short positions
    daily_swap_rate: Decimal
    supports_live: bool

    def round_qty(self, qty: Decimal) -> Decimal:
        return (qty / self.qty_step).to_integral_value(rounding=ROUND_DOWN) * self.qty_step

    def base_asset(self, symbol: str) -> str:
        return symbol.split("/")[0]


VENUES: dict[str, VenueProfile] = {
    # Quidax spot (spec Section 10): 0.1% maker and taker on the order
    # book. Min size and step are unverified placeholders until the live
    # connector is built (Phase 4) and checked against real rejections.
    "quidax_spot": VenueProfile(
        key="quidax_spot",
        label="Quidax spot",
        kind="spot",
        quote_asset="USDT",
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        long_only=True,
        max_leverage=Decimal(1),
        qty_step=Decimal("0.000001"),
        min_qty=Decimal("0.00001"),
        min_notional=Decimal("1"),
        quote_source="quidax",
        spread=Decimal(0),
        daily_swap_rate=Decimal(0),
        supports_live=True,
    ),
    # Exness BTCUSD CFD, paper only, for practising the manual MT5 style
    # in the app. Spread and swap are untested placeholders (like
    # config.yaml's recommend fee/slippage), not measured from an account.
    "exness_cfd": VenueProfile(
        key="exness_cfd",
        label="Exness CFD (paper)",
        kind="cfd",
        quote_asset="USDT",
        maker_fee=Decimal(0),
        taker_fee=Decimal(0),
        long_only=False,
        max_leverage=Decimal(2),
        qty_step=Decimal("0.01"),
        min_qty=Decimal("0.01"),
        min_notional=Decimal(0),
        quote_source="binance",
        spread=Decimal("0.0003"),
        daily_swap_rate=Decimal("0.0005"),
        supports_live=False,
    ),
}


def get_venue(key: str) -> VenueProfile:
    try:
        return VENUES[key]
    except KeyError:
        raise ValueError(f"unknown venue {key!r}; known: {', '.join(VENUES)}") from None

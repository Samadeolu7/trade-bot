"""The one interface every order goes through, whether a person or a bot
placed it and whether the account is paper or live.

The platform only ever talks to a `Broker`, so moving an account from
paper to live swaps the implementation (PaperBroker -> QuidaxBroker) and
nothing above it. Money amounts are Decimal on this path; strategy math
stays float and is converted at the order boundary."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from bot.broker.venues import VenueProfile

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
# "open": resting on the book. "filled"/"cancelled"/"rejected" are final.
# A market order the book couldn't fully fill ends "cancelled" with
# partial fills attached, the way a real exchange cancels the remainder.
OrderStatus = Literal["open", "filled", "cancelled", "rejected"]


class BrokerError(Exception):
    """The venue couldn't be reached or answered with something unusable.
    Distinct from a rejection, which is a valid answer ("insufficient
    funds") and comes back as an OrderResult."""


@dataclass(frozen=True)
class OrderRequest:
    client_id: str
    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None


@dataclass(frozen=True)
class BrokerFill:
    price: Decimal
    quantity: Decimal
    fee: Decimal  # always in the venue's quote asset
    liquidity: Literal["maker", "taker"]
    time: datetime


@dataclass
class OrderResult:
    venue_order_id: str
    status: OrderStatus
    fills: list[BrokerFill] = field(default_factory=list)
    reject_reason: str = ""

    @property
    def filled_quantity(self) -> Decimal:
        return sum((f.quantity for f in self.fills), Decimal(0))


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    time: datetime

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Depth:
    """Price levels, best first: bids descending, asks ascending."""
    bids: list[tuple[Decimal, Decimal]]
    asks: list[tuple[Decimal, Decimal]]


class Broker(ABC):
    venue: VenueProfile

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, venue_order_id: str) -> OrderResult: ...

    @abstractmethod
    def get_order(self, venue_order_id: str) -> OrderResult: ...

    @abstractmethod
    def get_balances(self) -> dict[str, Decimal]:
        """Asset -> total balance, including amounts reserved by open orders."""

    @abstractmethod
    def quote(self, symbol: str) -> Quote: ...

    def poll(self) -> list[tuple[str, OrderResult]]:
        """Called by the engine every tick. Returns (venue_order_id, result)
        for resting orders whose state changed since the last poll. A live
        broker answers this from the venue; the paper broker fills resting
        limits against the current quote."""
        return []

"""A simulated venue that follows a real venue's rules.

Market orders walk the venue's real order book when one is available
(Quidax), so a large order pays for the depth it eats. Resting limits
fill at their limit price once the opposite side of the quote touches
them. Fees, minimum size, step size and long-only are taken from the
venue profile, and orders the real venue would refuse (insufficient
funds, a short on spot) are rejected rather than quietly adjusted.

Balances and resting orders live in a PaperStore: in memory for tests,
database tables in the platform. A PaperBroker holds no state itself."""

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from bot.broker.base import (
    Broker,
    BrokerFill,
    Depth,
    OrderRequest,
    OrderResult,
    Quote,
)
from bot.broker.venues import VenueProfile

ZERO = Decimal(0)


@dataclass
class PaperOrder:
    venue_order_id: str
    client_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    status: str
    fills: list[BrokerFill] = field(default_factory=list)
    reject_reason: str = ""

    @property
    def filled_quantity(self) -> Decimal:
        return sum((f.quantity for f in self.fills), ZERO)

    @property
    def remaining(self) -> Decimal:
        return self.quantity - self.filled_quantity

    def result(self) -> OrderResult:
        return OrderResult(self.venue_order_id, self.status, list(self.fills), self.reject_reason)


class PaperStore(Protocol):
    def get_balances(self) -> dict[str, Decimal]: ...

    def apply_balance_deltas(self, deltas: dict[str, Decimal]) -> None: ...

    def next_order_id(self) -> str: ...

    def save_order(self, order: PaperOrder) -> None: ...

    def get_order(self, venue_order_id: str) -> PaperOrder | None: ...

    def open_orders(self) -> list[PaperOrder]: ...


class InMemoryPaperStore:
    def __init__(self, balances: dict[str, Decimal] | None = None):
        self.balances = {k: Decimal(str(v)) for k, v in (balances or {}).items()}
        self.orders: dict[str, PaperOrder] = {}
        self._ids = itertools.count(1)

    def get_balances(self) -> dict[str, Decimal]:
        return dict(self.balances)

    def apply_balance_deltas(self, deltas: dict[str, Decimal]) -> None:
        for asset, delta in deltas.items():
            self.balances[asset] = self.balances.get(asset, ZERO) + delta

    def next_order_id(self) -> str:
        return f"paper-{next(self._ids)}"

    def save_order(self, order: PaperOrder) -> None:
        self.orders[order.venue_order_id] = order

    def get_order(self, venue_order_id: str) -> PaperOrder | None:
        return self.orders.get(venue_order_id)

    def open_orders(self) -> list[PaperOrder]:
        return [o for o in self.orders.values() if o.status == "open"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PaperBroker(Broker):
    def __init__(self, venue: VenueProfile, store: PaperStore, quotes):
        self.venue = venue
        self.store = store
        self.quotes = quotes

    # --- Broker interface -------------------------------------------------

    def place_order(self, request: OrderRequest) -> OrderResult:
        order = PaperOrder(
            venue_order_id=self.store.next_order_id(),
            client_id=request.client_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            limit_price=request.limit_price,
            status="open",
        )
        reason = self._validate(request)
        if reason:
            return self._reject(order, reason)

        quote = self.quotes.quote(request.symbol)
        limit = request.limit_price
        if request.order_type == "limit" and not self._marketable(request.side, limit, quote):
            reason = self._funds_problem(request.symbol, request.side, request.quantity, limit, self.venue.maker_fee, quote)
            if reason:
                return self._reject(order, reason)
            self.store.save_order(order)
            return order.result()

        levels = self._levels(request.side, quote)
        fills = self._walk(request.side, levels, request.quantity, limit)
        if not fills:
            return self._reject(order, "no liquidity at an acceptable price")

        filled = sum((q for _, q in fills), ZERO)
        notional = sum((p * q for p, q in fills), ZERO)
        reason = self._funds_problem(
            request.symbol, request.side, filled, notional / filled, self.venue.taker_fee, quote
        )
        if reason:
            return self._reject(order, reason)
        if self.venue.min_notional and notional < self.venue.min_notional:
            return self._reject(order, f"order value below the venue minimum of {self.venue.min_notional}")

        now = _now()
        for price, qty in fills:
            fill = BrokerFill(price, qty, price * qty * self.venue.taker_fee, "taker", now)
            self._apply_fill(order, fill)

        if order.remaining > 0:
            # a limit keeps its unfilled remainder on the book; a market
            # order's remainder is cancelled, as a real exchange would
            order.status = "open" if request.order_type == "limit" else "cancelled"
        else:
            order.status = "filled"
        self.store.save_order(order)
        return order.result()

    def cancel_order(self, venue_order_id: str) -> OrderResult:
        order = self.store.get_order(venue_order_id)
        if order is None:
            return OrderResult(venue_order_id, "rejected", reject_reason="unknown order")
        if order.status == "open":
            order.status = "cancelled"
            self.store.save_order(order)
        return order.result()

    def get_order(self, venue_order_id: str) -> OrderResult:
        order = self.store.get_order(venue_order_id)
        if order is None:
            return OrderResult(venue_order_id, "rejected", reject_reason="unknown order")
        return order.result()

    def get_balances(self) -> dict[str, Decimal]:
        return self.store.get_balances()

    def quote(self, symbol: str) -> Quote:
        return self.quotes.quote(symbol)

    def poll(self) -> list[tuple[str, OrderResult]]:
        changed = []
        quotes: dict[str, Quote] = {}
        for order in self.store.open_orders():
            if order.symbol not in quotes:
                quotes[order.symbol] = self.quotes.quote(order.symbol)
            quote = quotes[order.symbol]
            if not self._marketable(order.side, order.limit_price, quote):
                continue
            qty = order.remaining
            reason = self._funds_problem(
                order.symbol, order.side, qty, order.limit_price, self.venue.maker_fee, quote, excluding=order
            )
            if reason:
                order.status = "cancelled"
                order.reject_reason = f"cancelled when it would have filled: {reason}"
            else:
                price = order.limit_price
                self._apply_fill(order, BrokerFill(price, qty, price * qty * self.venue.maker_fee, "maker", _now()))
                order.status = "filled"
            self.store.save_order(order)
            changed.append((order.venue_order_id, order.result()))
        return changed

    # --- paper-only -------------------------------------------------------

    def deposit(self, asset: str, amount: Decimal) -> None:
        self.store.apply_balance_deltas({asset: amount})

    def charge_financing(self, symbol: str, days: Decimal = Decimal(1)) -> Decimal:
        """CFD overnight swap on whatever position is open. Returns the
        amount charged (in the quote asset), zero for spot venues."""
        if self.venue.kind != "cfd" or not self.venue.daily_swap_rate:
            return ZERO
        position = self.store.get_balances().get(self.venue.base_asset(symbol), ZERO)
        if position == 0:
            return ZERO
        charge = abs(position) * self.quotes.quote(symbol).mid * self.venue.daily_swap_rate * days
        self.store.apply_balance_deltas({self.venue.quote_asset: -charge})
        return charge

    # --- internals --------------------------------------------------------

    def _validate(self, request: OrderRequest) -> str:
        venue = self.venue
        if request.quantity <= 0:
            return "quantity must be positive"
        if request.quantity < venue.min_qty:
            return f"quantity below the venue minimum of {venue.min_qty}"
        if venue.round_qty(request.quantity) != request.quantity:
            return f"quantity must be a multiple of {venue.qty_step}"
        if request.order_type == "limit" and (request.limit_price is None or request.limit_price <= 0):
            return "a limit order needs a positive limit price"
        return ""

    def _reject(self, order: PaperOrder, reason: str) -> OrderResult:
        order.status = "rejected"
        order.reject_reason = reason
        self.store.save_order(order)
        return order.result()

    @staticmethod
    def _marketable(side: str, limit: Decimal | None, quote: Quote) -> bool:
        if limit is None:
            return True
        return limit >= quote.ask if side == "buy" else limit <= quote.bid

    def _levels(self, side: str, quote: Quote) -> list[tuple[Decimal, Decimal]]:
        depth: Depth | None = self.quotes.depth(quote.symbol)
        if depth is not None:
            return depth.asks if side == "buy" else depth.bids
        # no visible book (CFD): the quoted price is available in any size
        return [(quote.ask if side == "buy" else quote.bid, Decimal("Infinity"))]

    @staticmethod
    def _walk(side: str, levels, quantity: Decimal, limit: Decimal | None) -> list[tuple[Decimal, Decimal]]:
        """Takes liquidity best-first until `quantity` is filled, the book
        runs out, or (for a limit) the next level is past the limit."""
        fills = []
        remaining = quantity
        for price, available in levels:
            if remaining <= 0:
                break
            if limit is not None and (price > limit if side == "buy" else price < limit):
                break
            take = min(remaining, available)
            fills.append((price, take))
            remaining -= take
        return fills

    def _reserved(self, excluding: PaperOrder | None = None) -> dict[str, Decimal]:
        reserved: dict[str, Decimal] = {}
        if self.venue.kind != "spot":
            return reserved
        for order in self.store.open_orders():
            if excluding is not None and order.venue_order_id == excluding.venue_order_id:
                continue
            if order.side == "buy":
                amount = order.remaining * order.limit_price * (1 + self.venue.maker_fee)
                reserved[self.venue.quote_asset] = reserved.get(self.venue.quote_asset, ZERO) + amount
            else:
                base = self.venue.base_asset(order.symbol)
                reserved[base] = reserved.get(base, ZERO) + order.remaining
        return reserved

    def _funds_problem(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        fee_rate: Decimal,
        quote: Quote,
        excluding: PaperOrder | None = None,
    ) -> str:
        venue = self.venue
        base = venue.base_asset(symbol)
        balances = self.store.get_balances()
        reserved = self._reserved(excluding)
        cash = balances.get(venue.quote_asset, ZERO) - reserved.get(venue.quote_asset, ZERO)
        held = balances.get(base, ZERO) - reserved.get(base, ZERO)
        notional = qty * price
        fee = notional * fee_rate
        cash_after = cash - notional - fee if side == "buy" else cash + notional - fee
        held_after = held + qty if side == "buy" else held - qty

        if venue.long_only and held_after < 0:
            return f"{venue.label} is long-only: can't sell more {base} than is held ({held})"
        if venue.kind == "spot":
            if cash_after < 0:
                return f"insufficient {venue.quote_asset}: need {notional + fee:.2f}, have {cash:.2f}"
            return ""
        mark = quote.mid
        equity_after = cash_after + held_after * mark
        margin = abs(held_after) * mark / venue.max_leverage
        if equity_after < margin:
            return f"insufficient margin: position needs {margin:.2f}, equity would be {equity_after:.2f}"
        return ""

    def _apply_fill(self, order: PaperOrder, fill: BrokerFill) -> None:
        base = self.venue.base_asset(order.symbol)
        notional = fill.price * fill.quantity
        if order.side == "buy":
            deltas = {base: fill.quantity, self.venue.quote_asset: -notional - fill.fee}
        else:
            deltas = {base: -fill.quantity, self.venue.quote_asset: notional - fill.fee}
        self.store.apply_balance_deltas(deltas)
        order.fills.append(fill)

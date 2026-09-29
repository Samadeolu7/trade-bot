"""Picks the broker for an account. Everything above this module works
the same for paper and live accounts."""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from django.db.models import F

from bot.broker.base import Broker, BrokerError, BrokerFill
from bot.broker.paper import PaperBroker, PaperOrder
from bot.broker.quotes import quote_source_for
from bot.broker.venues import get_venue
from trading.models import PaperBalance, PaperVenueOrder, TradingAccount

_quote_sources: dict = {}


def quote_source(venue_key: str):
    """One shared source per venue per process, so its short cache is
    shared by every account and bot on that venue."""
    if venue_key not in _quote_sources:
        _quote_sources[venue_key] = quote_source_for(get_venue(venue_key))
    return _quote_sources[venue_key]


def set_quote_source(venue_key: str, source) -> None:
    """Tests and replays swap in a StaticQuoteSource here."""
    _quote_sources[venue_key] = source


def _fill_to_json(fill: BrokerFill) -> dict:
    return {
        "price": str(fill.price), "quantity": str(fill.quantity), "fee": str(fill.fee),
        "liquidity": fill.liquidity, "time": fill.time.isoformat(),
    }


def _fill_from_json(data: dict) -> BrokerFill:
    return BrokerFill(
        Decimal(data["price"]), Decimal(data["quantity"]), Decimal(data["fee"]),
        data["liquidity"], datetime.fromisoformat(data["time"]),
    )


class DbPaperStore:
    """PaperStore backed by the paper venue tables. Callers run inside a
    transaction, so a fill's balance changes and our own book entries
    commit together or not at all."""

    def __init__(self, account: TradingAccount):
        self.account = account

    def get_balances(self) -> dict[str, Decimal]:
        return {b.asset: b.amount for b in PaperBalance.objects.filter(account=self.account)}

    def apply_balance_deltas(self, deltas: dict[str, Decimal]) -> None:
        for asset, delta in deltas.items():
            PaperBalance.objects.get_or_create(account=self.account, asset=asset)
            PaperBalance.objects.filter(account=self.account, asset=asset).update(amount=F("amount") + delta)

    def next_order_id(self) -> str:
        return f"paper-{uuid4().hex[:20]}"

    def save_order(self, order: PaperOrder) -> None:
        PaperVenueOrder.objects.update_or_create(
            venue_order_id=order.venue_order_id,
            defaults={
                "account": self.account,
                "client_id": order.client_id,
                "symbol": order.symbol,
                "side": order.side,
                "order_type": order.order_type,
                "quantity": order.quantity,
                "limit_price": order.limit_price,
                "status": order.status,
                "fills": [_fill_to_json(f) for f in order.fills],
                "reject_reason": order.reject_reason,
            },
        )

    def _to_paper_order(self, row: PaperVenueOrder) -> PaperOrder:
        return PaperOrder(
            venue_order_id=row.venue_order_id,
            client_id=row.client_id,
            symbol=row.symbol,
            side=row.side,
            order_type=row.order_type,
            quantity=row.quantity,
            limit_price=row.limit_price,
            status=row.status,
            fills=[_fill_from_json(f) for f in row.fills],
            reject_reason=row.reject_reason,
        )

    def get_order(self, venue_order_id: str) -> PaperOrder | None:
        row = PaperVenueOrder.objects.filter(account=self.account, venue_order_id=venue_order_id).first()
        return self._to_paper_order(row) if row else None

    def open_orders(self) -> list[PaperOrder]:
        rows = PaperVenueOrder.objects.filter(account=self.account, status="open").order_by("id")
        return [self._to_paper_order(r) for r in rows]


def get_broker(account: TradingAccount) -> Broker:
    if account.mode == TradingAccount.Mode.PAPER:
        return PaperBroker(account.venue_profile, DbPaperStore(account), quote_source(account.venue))
    # Phase 4: QuidaxBroker for live quidax_spot accounts
    raise BrokerError(f"no live connector for {account.venue_profile.label} yet")

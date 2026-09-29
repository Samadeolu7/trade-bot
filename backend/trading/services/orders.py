"""Order management: the single path every order takes, from a person's
trade ticket, a bot, a stop being hit or the kill switch.

1. Book checks: the order must fit the book it's for (a bot spends only
   its allocation and sells only what it bought; the manual book can't
   spend the bots' cash).
2. The broker (paper or live) accepts, fills or rejects it.
3. Fills are booked: Fill rows, ledger entries and the book's position,
   in one transaction with the paper venue's own balance changes.

Refusals are recorded as rejected orders rather than silently dropped,
so they show up in the order history like they would at a real venue."""

import logging
from decimal import Decimal

from django.db import transaction

from bot.broker.base import BrokerError, OrderRequest, OrderResult
from core.audit import audit
from trading.events import account_group, publish
from trading.models import MANUAL_BOOK, Bot, Fill, LedgerEntry, Order, Position, TradingAccount
from trading.services import books
from trading.services.brokers import get_broker, quote_source

logger = logging.getLogger(__name__)
ZERO = Decimal(0)


class OrderError(Exception):
    """The request itself is invalid (bad size, no permission, halted
    account). Not recorded as an order."""


def _position(account: TradingAccount, book: str, symbol: str, bot: Bot | None = None) -> Position:
    position, _ = Position.objects.select_for_update().get_or_create(
        account=account, book=book, symbol=symbol, defaults={"bot": bot}
    )
    return position


def _reduces(position_qty: Decimal, side: str, quantity: Decimal) -> bool:
    if side == "sell":
        return position_qty > 0 and quantity <= position_qty
    return position_qty < 0 and quantity <= -position_qty


def _book_reserved(account, book, venue) -> dict[str, Decimal]:
    """What this book's own resting orders have already spoken for."""
    reserved: dict[str, Decimal] = {}
    for o in Order.objects.filter(account=account, book=book, status=Order.Status.OPEN):
        remaining = o.quantity - o.filled_quantity
        if o.side == "buy":
            amount = remaining * o.limit_price * (1 + venue.maker_fee)
            reserved[venue.quote_asset] = reserved.get(venue.quote_asset, ZERO) + amount
        else:
            base = venue.base_asset(o.symbol)
            reserved[base] = reserved.get(base, ZERO) + remaining
    return reserved


def _book_problem(account, book, symbol, side, quantity, price, position_qty) -> str:
    venue = account.venue_profile
    base = venue.base_asset(symbol)
    balances = books.book_balances(account, book)
    reserved = _book_reserved(account, book, venue) if venue.kind == "spot" else {}
    cash = balances.get(venue.quote_asset, ZERO) - reserved.get(venue.quote_asset, ZERO)
    held = balances.get(base, ZERO) - reserved.get(base, ZERO)
    fee = quantity * price * venue.taker_fee
    if venue.kind == "spot":
        if side == "buy" and quantity * price + fee > cash:
            return f"not enough {venue.quote_asset} in this book: need {quantity * price + fee:.2f}, have {cash:.2f}"
        if side == "sell" and quantity > held:
            return f"this book holds {held} {base}; spot can't sell more than that"
        return ""
    new_position = position_qty + (quantity if side == "buy" else -quantity)
    if venue.long_only and new_position < 0:
        return f"{venue.label} is long-only"
    cash_after = cash - quantity * price - fee if side == "buy" else cash + quantity * price - fee
    equity_after = cash_after + new_position * price
    margin = abs(new_position) * price / venue.max_leverage
    if equity_after < margin:
        return f"not enough margin in this book: need {margin:.2f}, equity would be {equity_after:.2f}"
    return ""


def submit_order(
    account: TradingAccount,
    *,
    book: str,
    symbol: str,
    side: str,
    quantity,
    source: str,
    order_type: str = "market",
    limit_price=None,
    bot: Bot | None = None,
    user=None,
    reason: str = "",
    stop_price=None,
    take_profit=None,
    request=None,
) -> Order:
    venue = account.venue_profile
    if side not in ("buy", "sell"):
        raise OrderError("side must be buy or sell")
    if order_type not in ("market", "limit"):
        raise OrderError("order type must be market or limit")
    quantity = venue.round_qty(Decimal(str(quantity)))
    if quantity <= 0:
        raise OrderError(f"quantity rounds to zero at the venue's step of {venue.qty_step}")
    limit_price = Decimal(str(limit_price)) if limit_price is not None else None
    if order_type == "limit" and (limit_price is None or limit_price <= 0):
        raise OrderError("a limit order needs a positive limit price")
    if not account.is_active:
        raise OrderError("this account is disabled")

    with transaction.atomic():
        position = _position(account, book, symbol, bot)
        reducing = _reduces(position.quantity, side, quantity)
        if account.halted and not reducing:
            raise OrderError(f"account is halted ({account.halted_reason or 'kill switch'}): only exits are allowed")

        order = Order.objects.create(
            account=account, book=book, bot=bot, placed_by=user, source=source, symbol=symbol,
            side=side, order_type=order_type, quantity=quantity, limit_price=limit_price,
            reason=reason[:500], status=Order.Status.PENDING,
            stop_price=Decimal(str(stop_price)) if stop_price is not None else None,
            take_profit=Decimal(str(take_profit)) if take_profit is not None else None,
        )

        try:
            quote = quote_source(account.venue).quote(symbol)
        except BrokerError as exc:
            return _reject(order, f"venue price unavailable: {exc}", request)
        check_price = limit_price if order_type == "limit" else (quote.ask if side == "buy" else quote.bid)
        problem = _book_problem(account, book, symbol, side, quantity, check_price, position.quantity)
        if problem:
            return _reject(order, problem, request)

        try:
            result = get_broker(account).place_order(
                OrderRequest(str(order.client_id), symbol, side, order_type, quantity, limit_price)
            )
        except BrokerError as exc:
            return _reject(order, f"venue error: {exc}", request)
        apply_result(order, result)

    audit(
        "order.submitted", request=request, user=user, actor_label=f"bot:{bot.name}" if bot else "",
        account=account, target=f"order:{order.pk}", side=side, quantity=str(quantity), order_type=order_type,
        status=order.status, source=source,
    )
    return order


def _reject(order: Order, reason: str, request=None) -> Order:
    order.status = Order.Status.REJECTED
    order.reject_reason = reason[:300]
    order.save(update_fields=["status", "reject_reason", "updated_at"])
    publish(account_group(order.account_id), "order", {"id": order.pk, "status": order.status})
    return order


def apply_result(order: Order, result: OrderResult) -> Order:
    """Books whatever is new in `result` (fills not seen before) and moves
    the order to the broker's status. Safe to call repeatedly with the
    same result, which is how resting orders are synced."""
    account = order.account
    venue = account.venue_profile
    base = venue.base_asset(order.symbol)
    new_fills = result.fills[order.fills.count():]

    if new_fills:
        position = _position(account, order.book, order.symbol, order.bot)
        for f in new_fills:
            Fill.objects.create(order=order, price=f.price, quantity=f.quantity, fee=f.fee,
                                liquidity=f.liquidity, time=f.time)
            notional = f.price * f.quantity
            sign = 1 if order.side == "buy" else -1
            books.post(account, order.book, LedgerEntry.Kind.TRADE, base, sign * f.quantity, order)
            books.post(account, order.book, LedgerEntry.Kind.TRADE, venue.quote_asset, -sign * notional, order)
            if f.fee:
                books.post(account, order.book, LedgerEntry.Kind.FEE, venue.quote_asset, -f.fee, order)
            _update_position(position, sign * f.quantity, f.price, f.time)
        if position.quantity != 0:
            if order.stop_price is not None:
                position.stop_price = order.stop_price
            if order.take_profit is not None:
                position.take_profit = order.take_profit
        position.save()
        publish(account_group(account.pk), "position", {"id": position.pk})

    fills = list(order.fills.all())
    filled = sum((f.quantity for f in fills), ZERO)
    order.filled_quantity = filled
    order.fees = sum((f.fee for f in fills), ZERO)
    order.average_price = (sum((f.price * f.quantity for f in fills), ZERO) / filled) if filled else None
    order.venue_order_id = result.venue_order_id or order.venue_order_id
    order.status = result.status
    order.reject_reason = result.reject_reason[:300]
    order.save()
    publish(account_group(account.pk), "order", {"id": order.pk, "status": order.status})
    return order


def _update_position(position: Position, delta: Decimal, price: Decimal, when) -> None:
    q0, a0 = position.quantity, position.average_price
    q1 = q0 + delta
    if q0 == 0 or (q0 > 0) == (delta > 0):
        position.average_price = (abs(q0) * a0 + abs(delta) * price) / abs(q1)
        if q0 == 0:
            position.opened_at = when
    else:
        closing = min(abs(delta), abs(q0))
        direction = 1 if q0 > 0 else -1
        position.realized_pnl += closing * (price - a0) * direction
        if q1 == 0:
            position.average_price = ZERO
            position.opened_at = None
            position.stop_price = None
            position.take_profit = None
        elif (q1 > 0) != (q0 > 0):
            # flipped through zero: the remainder is a new position
            position.average_price = price
            position.opened_at = when
            position.stop_price = None
            position.take_profit = None
    position.quantity = q1


def cancel_order(order: Order, user=None, request=None) -> Order:
    if order.status != Order.Status.OPEN:
        raise OrderError(f"order is {order.status}, not open")
    with transaction.atomic():
        result = get_broker(order.account).cancel_order(order.venue_order_id)
        apply_result(order, result)
    audit("order.cancelled", request=request, user=user, account=order.account, target=f"order:{order.pk}")
    return order


def sync_open_orders(account: TradingAccount) -> int:
    """Books fills on resting orders. Returns how many orders changed."""
    with transaction.atomic():
        changes = get_broker(account).poll()
        for venue_order_id, result in changes:
            order = Order.objects.filter(account=account, venue_order_id=venue_order_id).first()
            if order is None:
                logger.warning("venue order %s has no matching order in our books", venue_order_id)
                continue
            apply_result(order, result)
    return len(changes)


def close_position(position: Position, *, source: str, user=None, reason: str = "", request=None) -> Order | None:
    if position.quantity == 0:
        return None
    return submit_order(
        position.account, book=position.book, symbol=position.symbol,
        side="sell" if position.quantity > 0 else "buy", quantity=abs(position.quantity),
        source=source, bot=position.bot, user=user, reason=reason, request=request,
    )


def set_protection(position: Position, *, stop_price=None, take_profit=None, user=None, request=None) -> Position:
    """Stops and take-profits are held by the engine, not the venue
    (Quidax has no stop orders). A level already beyond the current price
    would fire on the next engine tick, so it's refused here instead."""
    if position.quantity == 0:
        raise OrderError("no open position to protect")
    mid = quote_source(position.account.venue).quote(position.symbol).mid
    stop = Decimal(str(stop_price)) if stop_price is not None else None
    tp = Decimal(str(take_profit)) if take_profit is not None else None
    long = position.quantity > 0
    if stop is not None and (stop >= mid if long else stop <= mid):
        raise OrderError(f"a {'long' if long else 'short'} stop must be {'below' if long else 'above'} the price ({mid:.2f})")
    if tp is not None and (tp <= mid if long else tp >= mid):
        raise OrderError(f"a {'long' if long else 'short'} take-profit must be {'above' if long else 'below'} the price ({mid:.2f})")
    position.stop_price = stop
    position.take_profit = tp
    position.save(update_fields=["stop_price", "take_profit", "updated_at"])
    audit("position.protection", request=request, user=user, account=position.account,
          target=f"position:{position.pk}", stop=str(stop), take_profit=str(tp))
    publish(account_group(position.account_id), "position", {"id": position.pk})
    return position


def fund_paper_account(account: TradingAccount, amount, *, user=None, request=None, withdraw: bool = False) -> None:
    """Paper money in or out of the manual book, mirrored on the paper
    venue so the books and the venue always agree."""
    if account.mode != TradingAccount.Mode.PAPER:
        raise OrderError("deposits and withdrawals on live accounts happen at the venue")
    amount = Decimal(str(amount))
    if amount <= 0:
        raise OrderError("amount must be positive")
    with transaction.atomic():
        if withdraw:
            cash = books.book_balances(account, MANUAL_BOOK).get(account.quote_asset, ZERO)
            if amount > cash:
                raise OrderError(f"the manual book has only {cash:.2f} {account.quote_asset}")
        signed = -amount if withdraw else amount
        get_broker(account).deposit(account.quote_asset, signed)
        books.post(account, MANUAL_BOOK, LedgerEntry.Kind.WITHDRAWAL if withdraw else LedgerEntry.Kind.DEPOSIT,
                   account.quote_asset, signed, note="paper funds")
    audit("account.withdraw" if withdraw else "account.deposit", request=request, user=user, account=account,
          target=f"account:{account.pk}", amount=str(amount))
    publish(account_group(account.pk), "balances", {})


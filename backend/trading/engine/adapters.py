"""Turns a strategy's reading of a completed bar into orders, the live
counterpart of the backtest engines:

- signal strategies (donchian, regime_switched, ...): modelled on
  bot/shadow/runner.py's shadow_poll_once. Flat: ask generate_signal and
  enter with the engine holding the stop. In a position: trail the stop
  with trail_stop. Exits happen when the engine sees the stop or target
  hit on the live price (engine.check_protections), not at bar close.
- exposure strategies (donchian_ensemble): modelled on
  bot/shadow/exposure_runner.py. Compare the target weight with what the
  book actually holds and trade the difference when it exceeds the
  strategy's rebalance threshold, using the same rule as
  bot.backtest.exposure.rebalance.

Each returns an Outcome that becomes the bar's BotDecision."""

import json
import math
from dataclasses import dataclass, field
from decimal import Decimal

import pandas as pd

from bot.backtest.engine import position_size
from bot.broker.base import Quote
from trading.models import Bot, BotDecision, Order, Position
from trading.services import books
from trading.services.orders import submit_order

ZERO = Decimal(0)


@dataclass
class Outcome:
    action: str
    reason: str
    diagnosis: dict = field(default_factory=dict)
    order: Order | None = None


def jsonable(data: dict) -> dict:
    def default(value):
        if isinstance(value, float) and math.isnan(value):
            return None
        if hasattr(value, "item"):  # numpy scalars
            return value.item()
        return str(value)

    return json.loads(json.dumps(data, default=default).replace("NaN", "null"))


def _diagnose(strategy, df: pd.DataFrame) -> dict:
    try:
        return jsonable(strategy.diagnose(df))
    except Exception as exc:  # a diagnosis is context, never a reason to skip the bar
        return {"diagnose_error": str(exc)}


def _affordable(cash: Decimal, price: Decimal, fee: Decimal) -> Decimal:
    return cash / (price * (1 + fee)) if price > 0 else ZERO


def _order_outcome(order: Order, action: str, reason: str, diagnosis: dict) -> Outcome:
    if order.status == Order.Status.REJECTED:
        return Outcome(BotDecision.Action.BLOCKED, f"order rejected: {order.reject_reason}", diagnosis, order)
    return Outcome(action, reason, diagnosis, order)


def evaluate_signal_bot(bot: Bot, strategy, df: pd.DataFrame, quote: Quote) -> Outcome:
    account = bot.account
    venue = account.venue_profile
    diagnosis = _diagnose(strategy, df)
    position = Position.objects.filter(account=account, book=bot.book, symbol=bot.symbol).first()

    if position is not None and position.quantity != 0:
        direction = position.direction
        since = f" since {position.opened_at:%Y-%m-%d %H:%M}" if position.opened_at else ""
        detail = f"holding {direction} {position.quantity}{since}"
        if position.stop_price is not None:
            new_stop = strategy.trail_stop(df, direction, float(position.stop_price))
            new_stop = Decimal(str(round(new_stop, 2)))
            improved = new_stop > position.stop_price if direction == "long" else new_stop < position.stop_price
            if improved:
                detail += f"; stop moved {position.stop_price:.2f} -> {new_stop:.2f}"
                position.stop_price = new_stop
                position.save(update_fields=["stop_price", "updated_at"])
            else:
                detail += f"; stop {position.stop_price:.2f}"
        return Outcome(BotDecision.Action.HOLD, detail, diagnosis)

    signal = strategy.generate_signal(df)
    if signal is None or signal.direction == "flat":
        reason = diagnosis.get("near_miss_reason") or "no entry signal"
        return Outcome(BotDecision.Action.NONE, reason, diagnosis)
    if signal.direction == "short" and venue.long_only:
        return Outcome(BotDecision.Action.SKIP, f"short signal skipped: {venue.label} is long-only ({signal.reason})", diagnosis)
    if account.halted:
        return Outcome(BotDecision.Action.BLOCKED, f"{signal.direction} signal blocked: account halted", diagnosis)

    balances = books.book_balances(account, bot.book)
    cash = balances.get(venue.quote_asset, ZERO)
    equity = books.equity(balances, venue.quote_asset, {venue.base_asset(bot.symbol): quote.mid})
    size = Decimal(str(position_size(float(equity), bot.risk_pct, signal.entry_price, signal.stop_loss)))
    price = quote.ask if signal.direction == "long" else quote.bid
    size = min(size, _affordable(cash, price, venue.taker_fee) if venue.kind == "spot" else size)
    qty = venue.round_qty(size)
    if qty < venue.min_qty:
        return Outcome(BotDecision.Action.SKIP, f"{signal.direction} signal skipped: size {qty} is below the venue minimum", diagnosis)

    order = submit_order(
        account, book=bot.book, symbol=bot.symbol, side="buy" if signal.direction == "long" else "sell",
        quantity=qty, source=Order.Source.BOT, bot=bot, reason=signal.reason,
        stop_price=round(signal.stop_loss, 2),
        take_profit=round(signal.take_profit, 2) if signal.take_profit is not None else None,
    )
    diagnosis = {**diagnosis, "signal_context": jsonable(signal.context)}
    return _order_outcome(order, BotDecision.Action.ENTER, f"{signal.direction}: {signal.reason}", diagnosis)


def evaluate_exposure_bot(bot: Bot, strategy, df: pd.DataFrame, quote: Quote) -> Outcome:
    account = bot.account
    venue = account.venue_profile
    base = venue.base_asset(bot.symbol)
    want = float(strategy.target_weights(df).iloc[-1])
    want = 0.0 if math.isnan(want) else want
    threshold = getattr(strategy, "rebalance_threshold", 0.0)

    balances = books.book_balances(account, bot.book)
    cash = balances.get(venue.quote_asset, ZERO)
    held_qty = balances.get(base, ZERO)
    mid = quote.mid
    equity = cash + held_qty * mid
    held = float(held_qty * mid / equity) if equity > 0 else 0.0
    diagnosis = {**_diagnose(strategy, df), "target_weight": round(want, 4), "held_weight": round(held, 4)}

    if not (abs(want - held) > threshold or (want == 0.0 and held > 0.0)):
        return Outcome(
            BotDecision.Action.HOLD,
            f"target {want:.1%} vs held {held:.1%}: within the {threshold:.0%} rebalance threshold",
            diagnosis,
        )
    if account.halted and want > held:
        return Outcome(BotDecision.Action.BLOCKED, "increase blocked: account halted", diagnosis)

    if want == 0.0:
        side, qty = "sell", held_qty
    elif want > held:
        side = "buy"
        qty = min(Decimal(str(want - held)) * equity / quote.ask, _affordable(cash, quote.ask, venue.taker_fee))
    else:
        side = "sell"
        qty = min(Decimal(str(held - want)) * equity / quote.bid, held_qty)
    qty = venue.round_qty(qty)
    if qty < venue.min_qty:
        return Outcome(BotDecision.Action.SKIP, f"rebalance to {want:.1%} skipped: size {qty} is below the venue minimum", diagnosis)

    reason = f"rebalance {held:.1%} -> {want:.1%} of capital"
    order = submit_order(account, book=bot.book, symbol=bot.symbol, side=side, quantity=qty,
                         source=Order.Source.BOT, bot=bot, reason=reason)
    return _order_outcome(order, BotDecision.Action.REBALANCE, reason, diagnosis)

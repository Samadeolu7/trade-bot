"""Evaluates recommendation feeds on each completed bar: the platform's
version of bot/recommend/runner.py. It reuses the same functions that runner
used (open_position/check_exit/close_position, trail_stop, and for the
ensemble mark_to_market/rebalance), so a feed makes the same calls the CLI
recommend process would have: exits at bar close against the bar's
high/low, costs from config.yaml's recommend section."""

import logging
import math

import pandas as pd
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from alerts.models import AlertRule
from alerts.service import notify
from bot.backtest.engine import check_exit, close_position, open_position
from bot.backtest.exposure import mark_to_market, rebalance
from bot.data.sentiment import fetch_fear_greed_index
from bot.shadow.runner import drop_incomplete_bar
from bot.strategy.registry import build_strategy
from market.candles import candles_df, funding_df
from market.health import check_series
from recommendations.messages import describe
from recommendations.models import Feed, Recommendation, SignalSwitch
from trading.engine.adapters import jsonable
from trading.services.bots import strategy_config_with

logger = logging.getLogger(__name__)

FEAR_GREED_KEY = "fear_greed"
SIGNAL_HISTORY_BARS = 500


def fear_greed() -> str:
    """Crypto Fear & Greed reading, refreshed at most every 6 hours (the
    index updates daily). Context for entries only, never a trading input."""
    value = cache.get(FEAR_GREED_KEY)
    if value is not None:
        return value
    try:
        latest = fetch_fear_greed_index(limit=1)[0]
        value = f"{latest['value']} ({latest['value_classification']})"
    except Exception:
        logger.warning("Fear & Greed unavailable", exc_info=True)
        value = ""
    cache.set(FEAR_GREED_KEY, value, 6 * 3600 if value else 600)
    return value


def build_feed_strategy(feed: Feed, funding=None):
    return build_strategy(feed.strategy, strategy_config_with(feed.params), funding_df=funding)


def _fmt(x) -> str:
    return f"{x:,.2f}"


def _pair(feed: Feed) -> str:
    return feed.symbol.replace("/USDT", "USD").replace("/", "")


def _evaluate_signal(feed: Feed, strategy, df: pd.DataFrame, bar_time) -> list[Recommendation]:
    bar = df.iloc[-1]
    events = []
    if feed.direction:
        position = {
            "direction": feed.direction, "entry_price": feed.entry_price, "stop": feed.stop,
            "take_profit": feed.take_profit, "size": 1.0, "entry_fee": feed.entry_price * feed.fee,
        }
        new_stop = strategy.trail_stop(df, feed.direction, feed.stop)
        if new_stop != feed.stop:
            events.append(Recommendation(
                feed=feed, kind=Recommendation.Kind.STOP_UPDATE, bar_time=bar_time, direction=feed.direction,
                price=float(bar["close"]), stop=float(new_stop), context={"old_stop": float(feed.stop)},
                reason=f"trailing stop moved from {_fmt(feed.stop)} to {_fmt(new_stop)}",
            ))
            feed.stop = float(new_stop)
            position["stop"] = feed.stop
        exit_price, exit_reason = check_exit(position, bar)
        if exit_price is not None:
            pnl, effective_exit = close_position(position, exit_price, feed.fee, feed.slippage)
            pnl_pct = pnl / feed.entry_price * 100
            events.append(Recommendation(
                feed=feed, kind=Recommendation.Kind.EXIT, bar_time=bar_time, direction=feed.direction,
                price=float(effective_exit), pnl_pct=float(pnl_pct),
                reason=f"{exit_reason.replace('_', ' ')} at {_fmt(effective_exit)} (entry {_fmt(feed.entry_price)})",
            ))
            feed.direction, feed.entry_price, feed.entry_time = "", None, None
            feed.stop, feed.take_profit, feed.entry_context = None, None, {}

    if feed.direction:
        # still in the call: a fresh entry signal is reported once per call
        # and direction, but the call itself doesn't change
        signal = strategy.generate_signal(df)
        if signal is not None and signal.direction in ("long", "short"):
            key = f"{feed.entry_time.isoformat() if feed.entry_time else ''}:{signal.direction}"[:80]
            if key != feed.repeat_signal_key:
                feed.repeat_signal_key = key
                events.append(Recommendation(
                    feed=feed, kind=Recommendation.Kind.SIGNAL_AGAIN, bar_time=bar_time,
                    direction=signal.direction, price=float(signal.entry_price), stop=float(signal.stop_loss),
                    reason=signal.reason, context=jsonable(signal.context), fear_greed=fear_greed(),
                ))
    else:
        signal = strategy.generate_signal(df)
        if signal is not None and signal.direction != "flat":
            opened = open_position(signal, size=1.0, fee=feed.fee, slippage=feed.slippage, owner=strategy)
            context = jsonable(signal.context)
            events.append(Recommendation(
                feed=feed, kind=Recommendation.Kind.ENTRY, bar_time=bar_time, direction=signal.direction,
                price=float(opened["entry_price"]), stop=float(signal.stop_loss),
                take_profit=float(signal.take_profit) if signal.take_profit is not None else None,
                reason=signal.reason, context=context, fear_greed=fear_greed(),
            ))
            feed.direction = signal.direction
            feed.entry_price = float(opened["entry_price"])
            feed.entry_time = bar_time
            feed.stop = float(signal.stop_loss)
            feed.take_profit = float(signal.take_profit) if signal.take_profit is not None else None
            feed.entry_context = context
    return events


def _evaluate_exposure(feed: Feed, strategy, df: pd.DataFrame, bar_time) -> list[Recommendation]:
    close = float(df["close"].iloc[-1])
    want = float(strategy.target_weights(df).iloc[-1])
    want = 0.0 if math.isnan(want) else want
    held, equity = feed.weight, feed.equity
    if feed.last_close:
        held, equity = mark_to_market(held, equity, feed.last_close, close)
    from_weight = held
    held, equity, traded = rebalance(held, equity, want, getattr(strategy, "rebalance_threshold", 0.0),
                                     feed.fee + feed.slippage)
    feed.weight, feed.equity, feed.last_close = held, equity, close
    if not traded:
        return []
    return [Recommendation(
        feed=feed, kind=Recommendation.Kind.REBALANCE, bar_time=bar_time, price=close,
        from_weight=from_weight, to_weight=held,
        reason=f"resize from {from_weight:.1%} to {held:.1%} of capital",
    )]


def _near_miss(feed: Feed, diagnosis: dict, bar_time, price: float) -> Recommendation | None:
    """Same de-duplication as the CLI: a lasting near-miss alerts once; a new
    one, or a return to none, resets it."""
    key = diagnosis.get("near_miss_key") if diagnosis.get("near_miss") else None
    if (key or "") == feed.near_miss_key:
        return None
    feed.near_miss_key = key or ""
    if key is None:
        return None
    return Recommendation(feed=feed, kind=Recommendation.Kind.NEAR_MISS, bar_time=bar_time, price=price,
                          reason=diagnosis.get("near_miss_reason") or key, context=diagnosis)


def _alert(event: Recommendation) -> None:
    feed = event.feed
    pair = _pair(feed)
    if event.kind in (Recommendation.Kind.ENTRY, Recommendation.Kind.STOP_UPDATE, Recommendation.Kind.EXIT,
                      Recommendation.Kind.REBALANCE):
        lots_before = feed.lots_held
        message = describe(feed, event, feed.last_diagnosis)
        if feed.lots_held != lots_before:
            feed.save(update_fields=["lots_held"])
        if message is not None:
            title, body = message
            notify(AlertRule.Kind.RECOMMENDATION, title, body + "\nAdvisory only; no order placed.")
        return
    if event.kind == Recommendation.Kind.ENTRY:
        title = f"MT5: {event.direction} {pair} ({feed.name}, {feed.timeframe})"
        body = f"Entry {_fmt(event.price)}, stop {_fmt(event.stop)}"
        if event.take_profit:
            body += f", target {_fmt(event.take_profit)}"
        body += f". {event.reason}."
        if event.fear_greed:
            body += f" Fear & Greed {event.fear_greed}."
    elif event.kind == Recommendation.Kind.STOP_UPDATE:
        title = f"MT5: move {pair} stop to {_fmt(event.stop)} ({feed.name})"
        body = f"{event.direction.capitalize()} position; {event.reason}."
    elif event.kind == Recommendation.Kind.EXIT:
        title = f"MT5: exit {event.direction} {pair} ({feed.name})"
        body = f"{event.reason.capitalize()}. Result {event.pnl_pct:+.2f}%."
    elif event.kind == Recommendation.Kind.REBALANCE:
        title = f"MT5: resize {pair} to {event.to_weight:.0%} of capital ({feed.name})"
        body = f"From {event.from_weight:.0%}, at {_fmt(event.price)}."
    elif event.kind == Recommendation.Kind.SIGNAL_AGAIN:
        held = feed.direction
        if event.direction == held:
            title = f"MT5: {held} {pair} signal again ({feed.name}), call unchanged"
            body = f"{event.reason} at {_fmt(event.price)}. Already {held} from {_fmt(feed.entry_price)}, stop {_fmt(feed.stop)}."
        else:
            title = f"MT5: {event.direction} {pair} signal while the call is {held} ({feed.name})"
            body = f"{event.reason} at {_fmt(event.price)}. The {held} call stands until its stop at {_fmt(feed.stop)}."
        notify(AlertRule.Kind.REPEAT_SIGNAL, title, body)
        return
    else:
        notify(AlertRule.Kind.NEAR_MISS, f"Near miss: {feed.name} ({feed.timeframe})", event.reason)
        return
    notify(AlertRule.Kind.RECOMMENDATION, title, body + " Advisory only; no order placed.")


def _open_position_note(feed: Feed) -> str:
    if feed.direction:
        return (f"Your {feed.direction} {_pair(feed)} position stays as it is in MT5, with its Stop Loss at "
                f"{_fmt(feed.stop)}; this app won't move or close it while paused.")
    if feed.weight > 0:
        lots = f"{feed.lots_held:g} lots" if feed.lots_held else f"{feed.weight:.0%} of capital"
        return (f"Your {_pair(feed)} buys ({lots}) stay as they are in MT5; no resize or close alerts "
                "will come while paused.")
    return "You have no open position from this feed."


def halt_feed(feed: Feed, reason: str, bar_time=None, price: float | None = None, by: str = "") -> Recommendation:
    """Kill switch for one feed: records why signals stopped, alerts, and
    makes no further calls until a person resumes it."""
    now = timezone.now()
    with transaction.atomic():
        event = Recommendation.objects.create(
            feed=feed, kind=Recommendation.Kind.SUPPRESSED, bar_time=bar_time or now, price=price,
            reason=reason[:500], context={"by": by} if by else {},
        )
        feed.halted, feed.halt_reason, feed.halted_at = True, reason[:300], now
        feed.status_reason = f"paused: {reason}"[:300]
        feed.save(update_fields=["halted", "halt_reason", "halted_at", "status_reason"])
    try:
        notify(AlertRule.Kind.RECOMMENDATION, f"SIGNALS PAUSED: {feed.name} ({_pair(feed)} {feed.timeframe})",
               f"Reason: {reason}.\n{_open_position_note(feed)}\n"
               "No new alerts from this feed until you check and press Resume on the Recommendations page.")
    except Exception:
        logger.exception("pause alert for feed %s failed", feed.name)
    return event


def resume_feed(feed: Feed, by: str = "", note: str = "") -> Recommendation:
    """Human acknowledgement that clears a halt. The next completed bar is
    evaluated as usual; bars during the pause were not, so stops that
    would have moved then move on the next bar."""
    now = timezone.now()
    with transaction.atomic():
        event = Recommendation.objects.create(
            feed=feed, kind=Recommendation.Kind.RESUMED, bar_time=now,
            reason=f"resumed after: {feed.halt_reason}"[:500], context={"by": by, "note": note[:300]},
        )
        feed.halted, feed.halt_reason, feed.halted_at, feed.status_reason = False, "", None, ""
        feed.save(update_fields=["halted", "halt_reason", "halted_at", "status_reason"])
    return event


def run_feed(feed: Feed, exchange_id: str, funding_config: dict) -> list[Recommendation]:
    switch = SignalSwitch.get()
    if switch.halted or feed.halted:
        reason = (f"paused for all feeds: {switch.reason or 'kill switch'}" if switch.halted
                  else f"paused: {feed.halt_reason or 'kill switch'}")[:300]
        if feed.status_reason != reason:
            feed.status_reason = reason
            feed.save(update_fields=["status_reason"])
        return []
    funding = None
    if feed.strategy == "funding_filtered":
        funding = funding_df(funding_config.get("exchange_id", "binanceusdm"),
                             funding_config.get("symbol", "BTC/USDT:USDT"))
    strategy = build_feed_strategy(feed, funding)
    exposure = hasattr(strategy, "target_weights")
    limit = None if exposure else max(SIGNAL_HISTORY_BARS, strategy.min_lookback + 5)
    df = drop_incomplete_bar(candles_df(exchange_id, feed.symbol, feed.timeframe, limit), feed.timeframe)
    if len(df) < strategy.min_lookback:
        feed.status_reason = f"warming up: {len(df)}/{strategy.min_lookback} completed bars"
        feed.save(update_fields=["status_reason"])
        return []
    bar_time = df.index[-1].to_pydatetime()
    # before the same-bar check, so data that stops arriving is caught too
    health = check_series(df, feed.timeframe)
    if not health.ok:
        return [halt_feed(feed, f"data check failed: {health.summary()}", bar_time, float(df["close"].iloc[-1]))]
    if feed.last_bar_at is not None and bar_time <= feed.last_bar_at:
        return []

    diagnosis = jsonable(strategy.diagnose(df))
    if health.warnings:
        diagnosis["data_warnings"] = health.warnings
    with transaction.atomic():
        if exposure:
            events = _evaluate_exposure(feed, strategy, df, bar_time)
        else:
            events = _evaluate_signal(feed, strategy, df, bar_time)
            if not feed.direction and not events:
                miss = _near_miss(feed, diagnosis, bar_time, float(df["close"].iloc[-1]))
                if miss is not None:
                    events.append(miss)
        for event in events:
            event.save()
        feed.last_diagnosis = diagnosis
        feed.last_bar_at = bar_time
        feed.last_run_at = timezone.now()
        feed.status_reason = ""
        feed.save()
    for event in events:
        try:
            _alert(event)
        except Exception:
            logger.exception("alert for recommendation %s failed", event.pk)
    return events

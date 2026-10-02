"""Heads-ups before a candle closes. Feeds only act on a completed candle,
so a few minutes before each close the forming candle, treated as if it
closed at the current price, shows whether a call is likely and at what
level. A "get ready" alert goes out about 15 minutes ahead; at the close
the usual call follows (marked GO), or a "no trade" note if the price
moved away, so nobody waits for an alert that isn't coming."""

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import ccxt
import pandas as pd
from django.conf import settings
from django.utils import timezone

from alerts.models import AlertRule
from alerts.service import notify
from bot.backtest.exposure import mark_to_market
from market.candles import candles_df
from recommendations.messages import NO_CAPITAL, _lot, _pair, _px, lots_for, position_lots
from recommendations.models import Feed, SignalSwitch

LEAD = timedelta(minutes=15)  # start of the heads-up window before the close
LAST = timedelta(minutes=5)  # too close to the close to be useful after this
NEAR_PCT = 1.0  # within this % of the trigger counts as "may trigger"
HISTORY_BARS = 500


def _period(feed: Feed) -> timedelta:
    return timedelta(seconds=ccxt.Exchange.parse_timeframe(feed.timeframe))


def forming_bar(feed: Feed, now: datetime) -> datetime:
    period = _period(feed).total_seconds()
    return datetime.fromtimestamp(math.floor(now.timestamp() / period) * period, tz=now.tzinfo)


def due(feed: Feed, now: datetime) -> datetime | None:
    """The forming candle's open time if we're inside its heads-up window
    and haven't warned for it yet."""
    bar = forming_bar(feed, now)
    close = bar + _period(feed)
    if not (close - LEAD <= now < close - LAST):
        return None
    if feed.heads_up_bar is not None and feed.heads_up_bar >= bar:
        return None
    return bar


def when(close: datetime) -> str:
    local = close.astimezone(ZoneInfo(settings.ALERT_TIMEZONE))
    return f"{close:%H:%M} UTC ({local:%H:%M} {settings.ALERT_TIMEZONE.split('/')[-1].replace('_', ' ')})"


def _with_close(df: pd.DataFrame, price: float) -> pd.DataFrame:
    """The forming candle as if it closed at `price`."""
    out = df.copy()
    i = out.index[-1]
    out.loc[i, "close"] = price
    out.loc[i, "high"] = max(out.loc[i, "high"], price)
    out.loc[i, "low"] = min(out.loc[i, "low"], price)
    return out


def _preview_signal(feed: Feed, strategy, df: pd.DataFrame, close_at: datetime) -> tuple[str, str] | None:
    pair, price = _pair(feed), float(df["close"].iloc[-1])
    coin = feed.symbol.split("/")[0]
    signal = strategy.generate_signal(df)
    level, condition = None, None
    if signal is None or signal.direction not in ("long", "short"):
        # not triggered at the current price: is it close to a trigger level?
        d = strategy.diagnose(df) if hasattr(strategy, "diagnose") else {}
        up, down = d.get("entry_upper"), d.get("entry_lower")
        if up and 0 < (up - price) / price * 100 <= NEAR_PCT:
            level, side_word = up, "above"
            signal = strategy.generate_signal(_with_close(df, up * 1.0005))
        elif down and 0 < (price - down) / price * 100 <= NEAR_PCT:
            level, side_word = down, "below"
            signal = strategy.generate_signal(_with_close(df, down * 0.9995))
        if signal is None or signal.direction not in ("long", "short") or level is None:
            return None
        condition = (f"{coin} is {_px(price)} now, {abs(level - price) / price:.2%} {('below' if side_word == 'above' else 'above')} "
                     f"the trigger {_px(level)}. If the candle closes {side_word} {_px(level)}")
    else:
        d = strategy.diagnose(df) if hasattr(strategy, "diagnose") else {}
        level = d.get("entry_upper") if signal.direction == "long" else d.get("entry_lower")
        side_word = "above" if signal.direction == "long" else "below"
        condition = (f"{coin} is {_px(price)} now, already {side_word} the trigger {_px(level)}. If it still closes "
                     f"{side_word} {_px(level)}" if level else f"{coin} is {_px(price)} now. If the close confirms it")

    side = "BUY" if signal.direction == "long" else "SELL"
    stop = float(signal.stop_loss)
    if feed.capital:
        lots, risk = position_lots(feed, price, stop)
        size = (f"{side} about {_lot(lots)} lots {pair}, Stop Loss about {_px(stop)} (risking about ${risk:,.0f})"
                if lots >= (feed.min_lot or 0.01) else f"{side} {pair}, but the size is under the minimum lot for your capital")
    else:
        size = f"{side} {pair} with Stop Loss about {_px(stop)}. {NO_CAPITAL}"
    title = f"GET READY: {feed.name} may {side} {pair} at {when(close_at)}"
    body = "\n".join([
        f"{condition} at {when(close_at)}, the GO alert will say: {size}.",
        f"Have MT5 open on {pair}. Don't trade before the GO alert: until the candle closes the price can move back "
        "and there is no signal. If it doesn't trigger you'll get a short 'no trade' message instead.",
    ])
    feed.heads_up_note = {"kind": "entry", "side": side, "level": level}
    return title, body


def _preview_exposure(feed: Feed, strategy, df: pd.DataFrame, close_at: datetime) -> tuple[str, str] | None:
    pair, price = _pair(feed), float(df["close"].iloc[-1])
    want = float(strategy.target_weights(df).iloc[-1])
    want = 0.0 if math.isnan(want) else want
    held = feed.weight
    if feed.last_close:
        held, _ = mark_to_market(held, feed.equity, feed.last_close, price)
    threshold = getattr(strategy, "rebalance_threshold", 0.0)
    if abs(want - held) <= threshold and not (want == 0 and held > 0):
        return None
    if feed.capital:
        target = lots_for(feed, feed.current_capital * want / price)
        change = round(target - feed.lots_held, 8)
        if change == 0:
            return None
        verb = f"BUY about {_lot(change)} lots" if change > 0 else f"CLOSE about {_lot(-change)} lots"
        size = f"{verb} {pair} (to hold about {_lot(target)} lots)"
    else:
        size = f"resize {pair} from {held:.0%} to about {want:.0%} of your capital. {NO_CAPITAL}"
    title = f"GET READY: {feed.name} may resize {pair} at {when(close_at)}"
    body = "\n".join([
        f"At the current price ({_px(price)}) the strategy would move from {held:.0%} to {want:.0%} of capital when the "
        f"candle closes at {when(close_at)}. The GO alert would say: {size}.",
        "Wait for the GO alert; the exact size is set at the close. If it no longer applies you'll get a 'no change' note.",
    ])
    feed.heads_up_note = {"kind": "resize", "from": round(held, 3), "to": round(want, 3)}
    return title, body


def _preview_position(feed: Feed, strategy, df: pd.DataFrame, close_at: datetime) -> tuple[str, str] | None:
    """For an open call: a stop that's about to move, or a price close to
    the stop (MT5 closes it by itself; nothing to do)."""
    pair, price = _pair(feed), float(df["close"].iloc[-1])
    side = "buy" if feed.direction == "long" else "sell"
    new_stop = float(strategy.trail_stop(df, feed.direction, feed.stop))
    if new_stop != feed.stop:
        feed.heads_up_note = {"kind": "stop", "stop": feed.stop}
        return (f"GET READY: {feed.name} may move {pair} Stop Loss at {when(close_at)}",
                f"If the candle closes around the current price ({_px(price)}), the GO alert will say: move the Stop "
                f"Loss on your {pair} {side} from {_px(feed.stop)} to about {_px(new_stop)}. Nothing to do until then.")
    gap = abs(price - feed.stop) / price * 100
    if gap <= NEAR_PCT:
        feed.heads_up_note = {"kind": "near_stop"}
        return (f"HEADS-UP: {pair} is near your {feed.name} Stop Loss",
                f"{_pair(feed)} is {_px(price)}, {gap:.2f}% from your Stop Loss at {_px(feed.stop)}. If it's hit, MT5 "
                f"closes the {side} by itself: nothing to do. The close at {when(close_at)} confirms either way.")
    return None


def heads_up(feed: Feed, exchange_id: str, build_strategy, now: datetime | None = None) -> tuple[str, str] | None:
    """Sends a get-ready alert for `feed` if one is due and a call looks
    likely. `build_strategy(feed)` builds its strategy. Returns the alert."""
    now = now or timezone.now()
    bar = due(feed, now)
    if bar is None or not feed.enabled or not feed.following or feed.halted or SignalSwitch.get().halted:
        return None
    feed.heads_up_bar = bar  # one look per candle, whatever it shows
    feed.heads_up_note = {}
    message = None
    strategy = build_strategy(feed)
    exposure = hasattr(strategy, "target_weights")
    df = candles_df(exchange_id, feed.symbol, feed.timeframe, None if exposure else HISTORY_BARS)
    if len(df) >= strategy.min_lookback and df.index[-1].to_pydatetime() == bar:
        close_at = bar + _period(feed)
        if exposure:
            message = _preview_exposure(feed, strategy, df, close_at)
        elif not feed.direction:
            message = _preview_signal(feed, strategy, df, close_at)
        else:
            message = _preview_position(feed, strategy, df, close_at)
    feed.save(update_fields=["heads_up_bar", "heads_up_note"])
    if message:
        notify(AlertRule.Kind.RECOMMENDATION, *message)
    return message


def no_trade_note(feed: Feed, bar_time, close: float) -> tuple[str, str] | None:
    """After a get-ready alert, when the close didn't produce the call."""
    note = feed.heads_up_note or {}
    if not note:
        return None
    pair = _pair(feed)
    close_at = bar_time + _period(feed)
    if note.get("kind") == "near_stop":
        return None  # the close's own alert (an exit, or nothing) says it all
    if note.get("kind") == "stop":
        return (f"NO CHANGE: {feed.name} {pair} Stop Loss at {when(close_at)}",
                f"Keep your Stop Loss at {_px(feed.stop)}: it closed at {_px(close)} and the stop doesn't move this time.")
    if note.get("kind") == "entry":
        level = note.get("level")
        why = f"it closed at {_px(close)}" + (f", not past the trigger {_px(level)}" if level else "")
        return (f"NO TRADE: {feed.name} {pair} at {when(close_at)}",
                f"Stand down: {why}. Nothing to do; the next check is at the next candle close.")
    return (f"NO CHANGE: {feed.name} {pair} at {when(close_at)}",
            f"Stand down: it closed at {_px(close)} and the resize no longer applies. Keep what you hold.")

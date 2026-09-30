"""Turns a recommendation into MT5 instructions in lots: what to click,
how many lots, where the stop loss goes, and why. Amounts need the feed's
capital (Recommendations page); without it, alerts say what to set."""

import math

from recommendations.models import Feed, Recommendation

NO_CAPITAL = "Set your capital for this feed on the Recommendations page and alerts will give exact lot sizes."


def _px(x) -> str:
    return f"{x:,.2f}"


def _pair(feed: Feed) -> str:
    return feed.symbol.replace("/USDT", "USD").replace("/", "")


def lots_for(feed: Feed, units: float) -> float:
    """Rounds a quantity of the coin DOWN to the symbol's lot step: never
    more exposure or risk than the strategy asked for."""
    step = feed.lot_step or 0.01
    lots = math.floor(units / (feed.contract_size or 1.0) / step + 1e-9) * step
    return round(lots, 8)


def _lot(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") if x < 1 else f"{x:.2f}"


def _min_lot_value(feed: Feed, price: float) -> float:
    return (feed.min_lot or 0.01) * (feed.contract_size or 1.0) * price


def rebalance_lots(feed: Feed, event: Recommendation) -> tuple[float, float]:
    """(target lots, lot change) for an exposure resize, from the lots the
    alerts have already told you to hold."""
    capital = feed.current_capital
    target_units = capital * (event.to_weight or 0.0) / event.price
    target = lots_for(feed, target_units)
    if 0 < target < (feed.min_lot or 0.01):
        target = 0.0
    return target, round(target - feed.lots_held, 8)


def position_lots(feed: Feed, entry: float, stop: float) -> tuple[float, float]:
    """(lots, money at risk) for a position entry sized at risk_pct of
    capital over the stop distance, capped at 1x notional (no leverage)."""
    capital = feed.capital
    distance = abs(entry - stop)
    units = capital * feed.risk_pct / distance if distance > 0 else 0.0
    units = min(units, capital / entry)
    lots = lots_for(feed, units)
    return lots, lots * (feed.contract_size or 1.0) * distance


def describe(feed: Feed, event: Recommendation, diagnosis: dict | None = None) -> tuple[str, str] | None:
    """(title, body) for the alert, or None when there's nothing to do
    (e.g. a resize too small to change your lots)."""
    pair = _pair(feed)
    d = diagnosis or {}
    k = Recommendation.Kind

    if event.kind == k.REBALANCE:
        why = ""
        if d.get("models_long"):
            why = f"Why: {d['models_long']} trend signals are on"
            if d.get("long_lookbacks_days"):
                why += f" ({d['long_lookbacks_days']}-day)"
            if d.get("realized_vol_pct") is not None:
                why += f"; BTC volatility {d['realized_vol_pct']}% a year"
            why += "."
        if not feed.capital:
            title = f"MT5 {pair}: hold {event.to_weight:.0%} of capital ({feed.name})"
            body = (f"The strategy moved from {event.from_weight:.0%} to {event.to_weight:.0%} of the money you set "
                    f"aside for it, at {_px(event.price)}. {NO_CAPITAL} {why}")
            return title, body
        target, change = rebalance_lots(feed, event)
        feed.lots_held = target
        if change == 0:
            return None  # the resize doesn't change your lots: nothing to do
        capital = feed.current_capital
        if change > 0:
            action = f"BUY {_lot(change)} lots {pair}"
            step1 = f"Open a BUY of {_lot(change)} lots {pair} at market (about {_px(event.price)})."
        elif target == 0:
            action = f"CLOSE all {pair} buys ({_lot(-change)} lots)"
            step1 = f"Close your {pair} buy positions for this strategy: {_lot(-change)} lots in total, at market."
        else:
            action = f"CLOSE {_lot(-change)} lots {pair}"
            step1 = (f"Close {_lot(-change)} lots of your {pair} buy at market (about {_px(event.price)}). "
                     "In MT5: right-click the position, Close, and type the volume.")
        lines = [
            "What to do now:",
            f"1. {step1}",
            f"2. You should then hold {_lot(target)} lots {pair} in total for this strategy"
            + (f" ({event.to_weight:.0%} of your ${capital:,.0f})." if target else ", i.e. none."),
            "No stop loss on this strategy: a later alert tells you when to reduce or close. "
            "Act within the next 4 hours; small price moves since the alert don't matter.",
        ]
        if why:
            lines.append(why)
        return f"MT5: {action} ({feed.name})", "\n".join(lines)

    if event.kind == k.ENTRY:
        side = "BUY" if event.direction == "long" else "SELL"
        if not feed.capital:
            title = f"MT5: {side} {pair} ({feed.name})"
            body = f"{side} at about {_px(event.price)}, Stop Loss {_px(event.stop)}. {NO_CAPITAL} {event.reason}."
            return title, body
        lots, risk = position_lots(feed, event.price, event.stop)
        if lots < (feed.min_lot or 0.01):
            min_risk = (feed.min_lot or 0.01) * (feed.contract_size or 1.0) * abs(event.price - event.stop)
            title = f"MT5: {side} signal {pair}, too small for your capital ({feed.name})"
            body = (f"The strategy says {side} at about {_px(event.price)} with Stop Loss {_px(event.stop)}, but at "
                    f"{feed.risk_pct:.0%} risk on ${feed.capital:,.0f} the size is under {_lot(feed.min_lot)} lots. "
                    f"The minimum {_lot(feed.min_lot)} lots would risk ${min_risk:,.2f} "
                    f"({min_risk / feed.capital:.0%} of your capital). Skipping is the safe choice.")
            return title, body
        feed.lots_held = lots
        tp = f", Take Profit {_px(event.take_profit)}" if event.take_profit else ", no Take Profit"
        lines = [
            "What to do now:",
            f"1. New Order in MT5: {pair}, Market Execution, Volume {_lot(lots)} lots.",
            f"2. Set Stop Loss {_px(event.stop)}{tp}.",
            f"3. Click {side} by Market (about {_px(event.price)}).",
            f"If the stop is hit you lose about ${risk:,.2f} ({risk / feed.capital:.1%} of your ${feed.capital:,.0f}).",
            f"Why: {event.reason}.",
        ]
        return f"MT5: {side} {_lot(lots)} lots {pair}, SL {_px(event.stop)} ({feed.name})", "\n".join(lines)

    if event.kind == k.STOP_UPDATE:
        side = "buy" if event.direction == "long" else "sell"
        old = (event.context or {}).get("old_stop")
        lines = [
            "What to do now:",
            f"1. In MT5, double-click your {pair} {side}" + (f" ({_lot(feed.lots_held)} lots)" if feed.lots_held else "") + ".",
            f"2. Change Stop Loss to {_px(event.stop)}" + (f" (was {_px(old)})" if old else "") + ", then Modify.",
            "This locks in more of the move; the stop only ever moves in your favour.",
        ]
        return f"MT5: move {pair} Stop Loss to {_px(event.stop)} ({feed.name})", "\n".join(lines)

    if event.kind == k.EXIT:
        side = "buy" if event.direction == "long" else "sell"
        lots = f" ({_lot(feed.lots_held)} lots)" if feed.lots_held else ""
        lines = [
            "What to do now:",
            f"1. Close your {pair} {side}{lots} at market, if it's still open. "
            "If your Stop Loss already triggered in MT5, it's closed and there's nothing to do.",
            f"Why: {event.reason}. Result {event.pnl_pct:+.2f}% on the price move.",
        ]
        feed.lots_held = 0.0
        return f"MT5: CLOSE {pair} {side}{lots} ({feed.name})", "\n".join(lines)
    return None

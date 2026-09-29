"""Structured (not free-text) Telegram message formatting (spec Section 9:
"keep the message format consistent and parseable... in case it's parsed
programmatically later"). Every message is a header line naming the event
type, followed by simple `key=value` lines — easy to read in Telegram and
trivial to parse back out if needed.

Every message carries `strategy` — with multiple shadow runs posting to the
same chat concurrently, the header line alone doesn't say which strategy an
alert belongs to."""

import pandas as pd

from bot.strategy.base import Signal


def _fmt_time(value) -> str:
    """Positions are stored with epoch-millisecond entry times, which are
    unreadable in a Telegram message. Render those (and pandas Timestamps) as
    a UTC date — plus a clock time only when it isn't midnight, since daily
    bars all open at 00:00. Strings are assumed already human-readable."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        ts = pd.Timestamp(int(value), unit="ms", tz="UTC")
    elif isinstance(value, pd.Timestamp):
        ts = value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")
    else:
        return value
    if ts == ts.normalize():
        return ts.strftime("%Y-%m-%d")
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def _pct_move(direction: str, entry: float, price: float) -> float:
    """% gain (positive) or loss (negative) of moving from entry to price, for
    a position in `direction`."""
    move = (price - entry) / entry * 100
    return move if direction == "long" else -move


def _kv_lines(header: str, fields: dict) -> str:
    lines = [header]
    for key, value in fields.items():
        lines.append(f"{key}={'' if value is None else value}")
    return "\n".join(lines)


def format_signal_message(signal: Signal, symbol: str, timeframe: str, strategy_label: str) -> str:
    return _kv_lines(
        "SIGNAL_FIRED",
        {
            "strategy": strategy_label,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": signal.direction,
            "entry": f"{signal.entry_price:.2f}",
            "stop": f"{signal.stop_loss:.2f}",
            "target": f"{signal.take_profit:.2f}" if signal.take_profit is not None else None,
            "reason": signal.reason,
            "time": _fmt_time(signal.timestamp),
        },
    )


def format_exit_message(
    symbol: str,
    timeframe: str,
    strategy_label: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    exit_reason: str,
    exit_time,
) -> str:
    return _kv_lines(
        "POSITION_CLOSED",
        {
            "strategy": strategy_label,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "entry": f"{entry_price:.2f}",
            "exit": f"{exit_price:.2f}",
            "pnl_pct": f"{pnl_pct:.2f}",
            "reason": exit_reason,
            "time": _fmt_time(exit_time),
        },
    )


def _summary_sentence(
    open_position: dict | None, current_price: float | None, trades_all_time: int, total_pnl_pct: float,
) -> str:
    closed = (
        f"Closed trades so far: {trades_all_time}, total {total_pnl_pct:+.2f}%."
        if trades_all_time
        else "No closed trades yet."
    )
    if open_position is None:
        return f"No open position. {closed}"
    direction = open_position["direction"]
    entry = open_position["entry_price"]
    stop = open_position["stop"]
    parts = [
        f"Holding {direction.upper()} since {_fmt_time(open_position['entry_time'])} "
        f"at {entry:,.2f}."
    ]
    if current_price is not None:
        parts.append(
            f"Now {current_price:,.2f} ({_pct_move(direction, entry, current_price):+.2f}% open, not yet locked in)."
        )
    stop_result = _pct_move(direction, entry, stop)
    outcome = "a profit" if stop_result >= 0 else "a loss"
    parts.append(f"Stop {stop:,.2f} — if hit, the trade closes at {outcome} of {abs(stop_result):.2f}%.")
    parts.append(f"{closed} (The open trade isn't counted until it closes.)")
    return " ".join(parts)


def format_daily_summary(
    symbol: str,
    timeframe: str,
    strategy_label: str,
    open_position: dict | None,
    trades_today: int,
    trades_all_time: int,
    total_pnl_pct: float,
    diagnosis: dict | None = None,
    current_price: float | None = None,
) -> str:
    if open_position is not None:
        position_line = (
            f"{open_position['direction']} since {_fmt_time(open_position['entry_time'])}, "
            f"entry={open_position['entry_price']:.2f}, stop={open_position['stop']:.2f}"
        )
    else:
        position_line = "flat"
    fields = {
        "strategy": strategy_label,
        "summary": _summary_sentence(open_position, current_price, trades_all_time, total_pnl_pct),
        "symbol": symbol,
        "timeframe": timeframe,
        "position": position_line,
    }
    if open_position is not None and current_price is not None:
        direction = open_position["direction"]
        fields["unrealized_pnl_pct"] = f"{_pct_move(direction, open_position['entry_price'], current_price):.2f}"
        fields["stop_distance_pct"] = f"{abs(current_price - open_position['stop']) / current_price * 100:.2f}"
    fields.update({
        "trades_today": trades_today,
        "trades_all_time": trades_all_time,
        "cumulative_pnl_pct": f"{total_pnl_pct:.2f}",
    })
    # for sanity-checking the bot's read of the market against your own —
    # near_miss_key is an internal de-dup token, not meant for a human reader
    for key, value in (diagnosis or {}).items():
        if key == "near_miss_key":
            continue
        fields[f"diag_{key}"] = value
    return _kv_lines("DAILY_SUMMARY", fields)


def format_near_miss_alert(symbol: str, timeframe: str, strategy_label: str, diagnosis: dict) -> str:
    fields = {
        "strategy": strategy_label,
        "symbol": symbol,
        "timeframe": timeframe,
        "reason": diagnosis.get("near_miss_reason"),
    }
    for key, value in diagnosis.items():
        if key in ("near_miss", "near_miss_key", "near_miss_reason"):
            continue
        fields[key] = value
    return _kv_lines("NEAR_MISS", fields)


def format_recommendation_entry(
    signal: Signal,
    symbol: str,
    timeframe: str,
    strategy_label: str,
    fear_greed: dict | None = None,
) -> str:
    """For the `recommend` system (manual trading via MT5/Exness, not the
    automated shadow runs) — always explicit that this is advisory, and
    merges in Signal.context (regime, ATR%, funding rate, etc. — whatever
    that strategy already populates) plus market-wide sentiment, since the
    whole point is giving the user enough "why" to decide for themselves."""
    fields = {
        "strategy": strategy_label,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": signal.direction,
        "entry": f"{signal.entry_price:.2f}",
        "stop": f"{signal.stop_loss:.2f}",
        "target": f"{signal.take_profit:.2f}" if signal.take_profit is not None else None,
        "reason": signal.reason,
        "time": _fmt_time(signal.timestamp),
    }
    for key, value in signal.context.items():
        fields[f"ctx_{key}"] = value
    if fear_greed is not None:
        fields["fear_greed"] = f"{fear_greed['value']} ({fear_greed['classification']})"
    fields["note"] = "for your review — no order placed"
    return _kv_lines("RECOMMENDATION_ENTRY", fields)


def format_recommendation_exit(
    symbol: str,
    timeframe: str,
    strategy_label: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    exit_reason: str,
    exit_time,
) -> str:
    return _kv_lines(
        "RECOMMENDATION_EXIT",
        {
            "strategy": strategy_label,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "entry": f"{entry_price:.2f}",
            "exit": f"{exit_price:.2f}",
            "pnl_pct": f"{pnl_pct:.2f}",
            "reason": exit_reason,
            "time": _fmt_time(exit_time),
            "note": "for your review — no order placed",
        },
    )


def format_recommendation_stop_update(
    symbol: str,
    timeframe: str,
    strategy_label: str,
    direction: str,
    old_stop: float,
    new_stop: float,
) -> str:
    """The shadow runner updates a trailing stop silently in the DB — for a
    manually-managed MT5 position, the moved stop has to actually reach the
    user or their real stop order goes stale."""
    change = new_stop - old_stop
    return _kv_lines(
        "RECOMMENDATION_STOP_UPDATE",
        {
            "strategy": strategy_label,
            "symbol": symbol,
            "timeframe": timeframe,
            "action": (
                f"Move your MT5 stop-loss on the {symbol} {direction} from {old_stop:,.2f} to "
                f"{new_stop:,.2f} ({'up' if change > 0 else 'down'} {abs(change):,.2f})"
            ),
            "direction": direction,
            "old_stop": f"{old_stop:.2f}",
            "new_stop": f"{new_stop:.2f}",
            "note": "update your MT5 stop-loss order to match",
        },
    )


def format_heartbeat(symbol: str, timeframe: str, strategy_label: str) -> str:
    return _kv_lines(
        "HEARTBEAT", {"strategy": strategy_label, "symbol": symbol, "timeframe": timeframe, "status": "alive"}
    )


def format_error_alert(symbol: str, timeframe: str, strategy_label: str, error: str) -> str:
    return _kv_lines(
        "ERROR", {"strategy": strategy_label, "symbol": symbol, "timeframe": timeframe, "detail": error}
    )


TELEGRAM_MAX_CHARS = 4000  # Telegram's hard limit is 4096; leave headroom for the part marker


def _fmt_result_line(label: str, summary: dict | None) -> str:
    if not summary:
        return f" {label}: no data in window"
    return (
        f" {label}: n={summary.get('trades', 0)} ret={summary.get('total_return_pct', 0):+.2f}% "
        f"dd={summary.get('max_drawdown_pct', 0):.2f}% pf={summary.get('profit_factor', 0):.2f} "
        f"win={summary.get('win_rate_pct', 0):.1f}% sharpe={summary.get('sharpe_ratio', 0):.2f} "
        f"bh={summary.get('buy_hold_pct', 0):+.2f}%"
    )


def format_research_report(header: dict, runs: list[tuple[str, dict | None, dict | None]]) -> list[str]:
    """A backtest batch as plain text meant to be copy-pasted back into a
    chat with Claude in one go — so it's compact, one line per window, and
    self-describing (windows, fees, data range) rather than relying on
    context the reader won't have. `runs` is [(label, train_summary,
    test_summary)]. Returns one or more messages, split on run boundaries
    to stay under Telegram's length limit."""
    head = _kv_lines("RESEARCH_REPORT", header)
    blocks = [
        "\n".join([f"[{label}]", _fmt_result_line("train", train), _fmt_result_line("test ", test)])
        for label, train, test in runs
    ]
    footer = "key: n=trades ret=return dd=max drawdown pf=profit factor bh=buy&hold\nnote=paste this whole message back to Claude"

    messages: list[str] = []
    current = head
    for block in blocks:
        if len(current) + len(block) + len(footer) + 4 > TELEGRAM_MAX_CHARS:
            messages.append(current)
            current = "RESEARCH_REPORT (continued)"
        current += "\n\n" + block
    messages.append(current + "\n\n" + footer)
    if len(messages) > 1:
        messages = [f"{m}\n(part {i}/{len(messages)})" for i, m in enumerate(messages, 1)]
    return messages


def format_exposure_rebalance(
    symbol: str,
    timeframe: str,
    strategy_label: str,
    from_weight: float,
    to_weight: float,
    price: float,
    bar_time,
    paper_equity: float,
    diagnosis: dict | None = None,
    advisory: bool = False,
) -> str:
    """A fraction-of-capital strategy (e.g. donchian_ensemble) changed how
    much of the account it holds. Phrased as a percentage of capital so it
    maps straight onto a manual MT5 position size. `advisory` marks it as
    part of the recommend feed (RECOMMENDATION_REBALANCE) rather than a
    paper-bot event."""
    change = to_weight - from_weight
    if to_weight == 0:
        action = f"Close the {symbol} position (was {from_weight:.0%} of capital)"
    elif from_weight == 0:
        action = f"Open a {symbol} long worth {to_weight:.0%} of your trading capital"
    else:
        verb = "Add to" if change > 0 else "Reduce"
        action = (
            f"{verb} the {symbol} long: {from_weight:.0%} → {to_weight:.0%} of capital "
            f"({'buy' if change > 0 else 'sell'} {abs(change):.0%} of capital)"
        )
    fields = {
        "strategy": strategy_label,
        "action": action,
        "symbol": symbol,
        "timeframe": timeframe,
        "from_weight_pct": f"{from_weight * 100:.1f}",
        "to_weight_pct": f"{to_weight * 100:.1f}",
        "price": f"{price:.2f}",
        "time": _fmt_time(bar_time),
        "paper_equity": f"{paper_equity:.2f}",
    }
    for key, value in (diagnosis or {}).items():
        if key in ("near_miss", "near_miss_key", "near_miss_reason"):
            continue
        fields[f"diag_{key}"] = value
    if advisory:
        fields["note"] = "for your review — no order placed; resize your MT5 position to match"
        return _kv_lines("RECOMMENDATION_REBALANCE", fields)
    fields["note"] = "paper trading — no order placed"
    return _kv_lines("EXPOSURE_REBALANCE", fields)


def format_exposure_summary(
    symbol: str,
    timeframe: str,
    strategy_label: str,
    held: float,
    paper_equity: float,
    start_equity: float,
    started_at,
    rebalances_all_time: int,
    diagnosis: dict | None = None,
) -> str:
    ret = (paper_equity / start_equity - 1) * 100 if start_equity else 0.0
    summary = (
        f"Holding {held:.0%} of capital in {symbol}. " if held > 0 else f"Flat, no {symbol} held. "
    ) + f"Paper account {paper_equity:,.2f} ({ret:+.2f}% since {_fmt_time(started_at)}), {rebalances_all_time} rebalances so far."
    fields = {
        "strategy": strategy_label,
        "summary": summary,
        "symbol": symbol,
        "timeframe": timeframe,
        "weight_pct": f"{held * 100:.1f}",
        "paper_equity": f"{paper_equity:.2f}",
        "return_pct": f"{ret:.2f}",
        "rebalances_all_time": rebalances_all_time,
    }
    for key, value in (diagnosis or {}).items():
        if key in ("near_miss", "near_miss_key", "near_miss_reason"):
            continue
        fields[f"diag_{key}"] = value
    return _kv_lines("DAILY_SUMMARY", fields)

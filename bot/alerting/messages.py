"""Structured (not free-text) Telegram message formatting (spec Section 9:
"keep the message format consistent and parseable... in case it's parsed
programmatically later"). Every message is a header line naming the event
type, followed by simple `key=value` lines — easy to read in Telegram and
trivial to parse back out if needed.

Every message carries `strategy` — with multiple shadow runs posting to the
same chat concurrently, the header line alone doesn't say which strategy an
alert belongs to."""

from bot.strategy.base import Signal


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
            "time": signal.timestamp,
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
            "time": exit_time,
        },
    )


def format_daily_summary(
    symbol: str,
    timeframe: str,
    strategy_label: str,
    open_position: dict | None,
    trades_today: int,
    trades_all_time: int,
    total_pnl_pct: float,
    diagnosis: dict | None = None,
) -> str:
    if open_position is not None:
        position_line = (
            f"{open_position['direction']} since {open_position['entry_time']}, "
            f"entry={open_position['entry_price']:.2f}, stop={open_position['stop']:.2f}"
        )
    else:
        position_line = "flat"
    fields = {
        "strategy": strategy_label,
        "symbol": symbol,
        "timeframe": timeframe,
        "position": position_line,
        "trades_today": trades_today,
        "trades_all_time": trades_all_time,
        "cumulative_pnl_pct": f"{total_pnl_pct:.2f}",
    }
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


def format_heartbeat(symbol: str, timeframe: str, strategy_label: str) -> str:
    return _kv_lines(
        "HEARTBEAT", {"strategy": strategy_label, "symbol": symbol, "timeframe": timeframe, "status": "alive"}
    )


def format_error_alert(symbol: str, timeframe: str, strategy_label: str, error: str) -> str:
    return _kv_lines(
        "ERROR", {"strategy": strategy_label, "symbol": symbol, "timeframe": timeframe, "detail": error}
    )

"""Structured (not free-text) Telegram message formatting (spec Section 9:
"keep the message format consistent and parseable... in case it's parsed
programmatically later"). Every message is a header line naming the event
type, followed by simple `key=value` lines — easy to read in Telegram and
trivial to parse back out if needed."""

from bot.strategy.base import Signal


def _kv_lines(header: str, fields: dict) -> str:
    lines = [header]
    for key, value in fields.items():
        lines.append(f"{key}={'' if value is None else value}")
    return "\n".join(lines)


def format_signal_message(signal: Signal, symbol: str, timeframe: str) -> str:
    return _kv_lines(
        "SIGNAL_FIRED",
        {
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
    open_position: dict | None,
    trades_today: int,
    trades_all_time: int,
    total_pnl_pct: float,
) -> str:
    if open_position is not None:
        position_line = (
            f"{open_position['direction']} since {open_position['entry_time']}, "
            f"entry={open_position['entry_price']:.2f}, stop={open_position['stop']:.2f}"
        )
    else:
        position_line = "flat"
    return _kv_lines(
        "DAILY_SUMMARY",
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "position": position_line,
            "trades_today": trades_today,
            "trades_all_time": trades_all_time,
            "cumulative_pnl_pct": f"{total_pnl_pct:.2f}",
        },
    )


def format_heartbeat(symbol: str, timeframe: str) -> str:
    return _kv_lines("HEARTBEAT", {"symbol": symbol, "timeframe": timeframe, "status": "alive"})


def format_error_alert(symbol: str, timeframe: str, error: str) -> str:
    return _kv_lines("ERROR", {"symbol": symbol, "timeframe": timeframe, "detail": error})

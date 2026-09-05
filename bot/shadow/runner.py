import logging
import sqlite3
import time

import ccxt
import pandas as pd

from bot.alerting.messages import (
    format_daily_summary,
    format_error_alert,
    format_exit_message,
    format_heartbeat,
    format_signal_message,
)
from bot.alerting.telegram import TelegramAlerter
from bot.backtest.engine import check_exit, close_position, open_position
from bot.data.backfill import backfill_candles
from bot.storage.db import (
    close_paper_position,
    count_paper_trades_all_time,
    count_paper_trades_since,
    get_open_paper_position,
    get_state,
    open_paper_position,
    query_candles_df,
    record_paper_trade,
    record_signal,
    set_state,
    sum_paper_trade_pnl_pct,
    update_paper_position_stop,
)
from bot.strategy.base import Strategy

logger = logging.getLogger(__name__)


def _drop_incomplete_bar(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """The exchange's most recent candle is still forming until its period
    ends — evaluating signals/exits against it would diverge from the
    backtest (which only ever sees finished bars) and could fire on data
    that keeps changing intraday. Drops it if the current bar's period
    hasn't ended yet."""
    if len(df) == 0:
        return df
    period_ms = ccxt.Exchange.parse_timeframe(timeframe) * 1000
    last_open_ms = int(df.index[-1].value // 1_000_000)
    now_ms = int(time.time() * 1000)
    if now_ms < last_open_ms + period_ms:
        return df.iloc[:-1]
    return df


def shadow_poll_once(
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    alerter: TelegramAlerter,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    strategy: Strategy,
    fee: float,
    slippage: float,
    backfill_start_date: str,
    history_bars: int = 500,
) -> None:
    """One shadow-run iteration. Reuses engine.open_position/check_exit/
    close_position directly (not a reimplementation) so live behavior can't
    silently diverge from what the backtest models — the entire point of a
    shadow run is comparing the two (spec Phase 4)."""
    # resume=True: fetches from wherever local data left off, so this both
    # keeps up with routine new candles and catches up after any downtime
    # (VPS restart, network blip) without a gap, in the same code path.
    backfill_candles(exchange, conn, exchange_id, symbol, timeframe, backfill_start_date, resume=True)

    df = query_candles_df(conn, exchange_id, symbol, timeframe).tail(
        max(history_bars, strategy.min_lookback + 5)
    )
    df = _drop_incomplete_bar(df, timeframe)

    if len(df) < strategy.min_lookback:
        logger.info(
            "not enough complete history yet (%d/%d bars) — skipping this iteration",
            len(df), strategy.min_lookback,
        )
        return

    bar = df.iloc[-1]
    position = get_open_paper_position(conn, exchange_id, symbol, timeframe)

    if position is not None:
        new_stop = strategy.trail_stop(df, position["direction"], position["stop"])
        if new_stop != position["stop"]:
            update_paper_position_stop(conn, exchange_id, symbol, timeframe, new_stop)
            position["stop"] = new_stop

        exit_price, exit_reason = check_exit(position, bar)
        if exit_price is not None:
            pnl, effective_exit = close_position(position, exit_price, fee, slippage)
            pnl_pct = (pnl / (position["entry_price"] * position["size"])) * 100
            record_paper_trade(
                conn, exchange_id, symbol, timeframe, position["direction"],
                position["entry_time"], position["entry_price"],
                bar.name, effective_exit, pnl, pnl_pct, exit_reason, bar.name,
            )
            close_paper_position(conn, exchange_id, symbol, timeframe)
            logger.info(
                "paper position closed: %s pnl_pct=%.2f reason=%s",
                position["direction"], pnl_pct, exit_reason,
            )
            alerter.send(
                format_exit_message(
                    symbol, timeframe, position["direction"], position["entry_price"],
                    effective_exit, pnl_pct, exit_reason, bar.name,
                )
            )
            position = None

    if position is None:
        signal = strategy.generate_signal(df)
        if signal is not None and signal.direction != "flat":
            record_signal(conn, exchange_id, symbol, timeframe, signal)
            new_position = open_position(signal, size=1.0, fee=fee, slippage=slippage, owner=strategy)
            open_paper_position(conn, exchange_id, symbol, timeframe, new_position)
            logger.info(
                "signal fired: %s entry=%.2f stop=%.2f reason=%s",
                signal.direction, signal.entry_price, signal.stop_loss, signal.reason,
            )
            alerter.send(format_signal_message(signal, symbol, timeframe))


def maybe_send_heartbeat(
    conn: sqlite3.Connection, alerter: TelegramAlerter, symbol: str, timeframe: str,
    interval_seconds: int,
) -> None:
    now_ms = int(time.time() * 1000)
    last = get_state(conn, "last_heartbeat_at")
    if last is not None and now_ms - int(last) < interval_seconds * 1000:
        return
    alerter.send(format_heartbeat(symbol, timeframe))
    set_state(conn, "last_heartbeat_at", str(now_ms))


def maybe_send_daily_summary(
    conn: sqlite3.Connection, alerter: TelegramAlerter, exchange_id: str, symbol: str, timeframe: str
) -> None:
    today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    if get_state(conn, "last_summary_date") == today:
        return
    since_ms = int(pd.Timestamp.now(tz="UTC").normalize().value // 1_000_000)
    open_position = get_open_paper_position(conn, exchange_id, symbol, timeframe)
    trades_today = count_paper_trades_since(conn, exchange_id, symbol, timeframe, since_ms)
    trades_all_time = count_paper_trades_all_time(conn, exchange_id, symbol, timeframe)
    total_pnl_pct = sum_paper_trade_pnl_pct(conn, exchange_id, symbol, timeframe)
    alerter.send(
        format_daily_summary(symbol, timeframe, open_position, trades_today, trades_all_time, total_pnl_pct)
    )
    set_state(conn, "last_summary_date", today)


def run_shadow_loop(
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    alerter: TelegramAlerter,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    strategy: Strategy,
    fee: float,
    slippage: float,
    backfill_start_date: str,
    interval_seconds: int = 300,
    heartbeat_interval_seconds: int = 86400,
) -> None:
    logger.info(
        "starting shadow run for %s %s every %ds (paper trading only — no orders placed)",
        symbol, timeframe, interval_seconds,
    )
    while True:
        try:
            shadow_poll_once(
                exchange, conn, alerter, exchange_id, symbol, timeframe, strategy,
                fee, slippage, backfill_start_date,
            )
            maybe_send_daily_summary(conn, alerter, exchange_id, symbol, timeframe)
            maybe_send_heartbeat(conn, alerter, symbol, timeframe, heartbeat_interval_seconds)
        except Exception as exc:
            logger.exception("shadow run iteration failed")
            alerter.send(format_error_alert(symbol, timeframe, str(exc)))
        time.sleep(interval_seconds)

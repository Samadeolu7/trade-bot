import logging
import random
import sqlite3
import time

import ccxt
import pandas as pd

from bot.alerting.messages import (
    format_error_alert,
    format_recommendation_entry,
    format_recommendation_exit,
    format_recommendation_stop_update,
)
from bot.alerting.telegram import TelegramAlerter
from bot.backtest.engine import check_exit, close_position, open_position
from bot.data.backfill import backfill_candles
from bot.data.sentiment import fetch_fear_greed_index
from bot.shadow.runner import (
    drop_incomplete_bar,
    maybe_send_daily_summary,
    maybe_send_heartbeat,
    maybe_send_near_miss_alert,
)
from bot.storage.db import (
    close_paper_position,
    get_latest_fear_greed,
    get_open_paper_position,
    open_paper_position,
    query_candles_df,
    record_paper_trade,
    record_signal,
    update_paper_position_stop,
    upsert_fear_greed_entries,
)
from bot.strategy.base import Strategy

logger = logging.getLogger(__name__)

FEAR_GREED_REFRESH_SECONDS = 24 * 60 * 60  # the index itself only updates once/day


def maybe_refresh_fear_greed(conn: sqlite3.Connection) -> dict | None:
    """Best-effort: only refetch if the latest stored entry is more than a
    day old (or missing) — refetching every 5-minute poll would be wasted
    work against an index that doesn't change that often. A fetch failure
    just means recommendations go out without a sentiment reading this
    cycle, not a crashed poll."""
    latest = get_latest_fear_greed(conn)
    now_s = int(time.time())
    if latest is not None and now_s - latest["fetched_at"] < FEAR_GREED_REFRESH_SECONDS:
        return latest
    try:
        entries = fetch_fear_greed_index(limit=1)
        upsert_fear_greed_entries(conn, entries)
    except Exception:
        logger.exception("failed to refresh Fear & Greed Index — continuing without it")
        return latest
    return get_latest_fear_greed(conn)


def _process_one_strategy(
    conn: sqlite3.Connection,
    alerter: TelegramAlerter,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    label: str,
    strategy: Strategy,
    df: pd.DataFrame,
    fee: float,
    slippage: float,
    fear_greed: dict | None,
) -> None:
    """One strategy's slice of a recommend-loop iteration: manage any open
    recommended position (stop-update / exit) or evaluate for a new entry —
    the exact same shape as shadow_poll_once's position handling, just with
    advisory RECOMMENDATION_* alerts instead of SIGNAL_FIRED/POSITION_CLOSED,
    and tracked under this strategy's own reco_-prefixed label so it can
    never collide with a real shadow-run paper trade."""
    reco_label = f"reco_{label}"
    strategy.before_poll()

    if len(df) < strategy.min_lookback:
        logger.info(
            "[%s] not enough complete history yet (%d/%d bars) — skipping this iteration",
            reco_label, len(df), strategy.min_lookback,
        )
        return

    bar = df.iloc[-1]
    position = get_open_paper_position(conn, exchange_id, symbol, timeframe, reco_label)

    if position is not None:
        new_stop = strategy.trail_stop(df, position["direction"], position["stop"])
        if new_stop != position["stop"]:
            old_stop = position["stop"]
            update_paper_position_stop(conn, exchange_id, symbol, timeframe, reco_label, new_stop)
            position["stop"] = new_stop
            alerter.send(
                format_recommendation_stop_update(
                    symbol, timeframe, reco_label, position["direction"], old_stop, new_stop,
                )
            )

        exit_price, exit_reason = check_exit(position, bar)
        if exit_price is not None:
            pnl, effective_exit = close_position(position, exit_price, fee, slippage)
            pnl_pct = (pnl / (position["entry_price"] * position["size"])) * 100
            record_paper_trade(
                conn, exchange_id, symbol, timeframe, reco_label, position["direction"],
                position["entry_time"], position["entry_price"],
                bar.name, effective_exit, pnl, pnl_pct, exit_reason, bar.name,
                context=position.get("context"),
            )
            close_paper_position(conn, exchange_id, symbol, timeframe, reco_label)
            logger.info(
                "[%s] recommended position closed: %s pnl_pct=%.2f reason=%s",
                reco_label, position["direction"], pnl_pct, exit_reason,
            )
            alerter.send(
                format_recommendation_exit(
                    symbol, timeframe, reco_label, position["direction"], position["entry_price"],
                    effective_exit, pnl_pct, exit_reason, bar.name,
                )
            )
            position = None

    if position is None:
        signal = strategy.generate_signal(df)
        if signal is not None and signal.direction != "flat":
            record_signal(conn, exchange_id, symbol, timeframe, reco_label, signal)
            new_position = open_position(signal, size=1.0, fee=fee, slippage=slippage, owner=strategy)
            open_paper_position(conn, exchange_id, symbol, timeframe, reco_label, new_position)
            logger.info(
                "[%s] recommendation: %s entry=%.2f stop=%.2f reason=%s",
                reco_label, signal.direction, signal.entry_price, signal.stop_loss, signal.reason,
            )
            alerter.send(format_recommendation_entry(signal, symbol, timeframe, reco_label, fear_greed))
        else:
            maybe_send_near_miss_alert(conn, alerter, symbol, timeframe, reco_label, strategy, df)


def recommend_poll_once(
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    alerter: TelegramAlerter,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    strategies: list[tuple[str, Strategy]],
    fee: float,
    slippage: float,
    backfill_start_date: str,
    history_bars: int = 500,
) -> None:
    """One recommend-loop iteration across every configured strategy.
    Candle data (and the Fear & Greed refresh) is fetched once and shared —
    every strategy reads the same instrument — but each strategy's
    "currently recommended" state is fully independent, tracked via the
    exact same paper_position mechanism the shadow runs use. A single
    strategy's failure is caught and logged per-strategy so it can't stop
    the others from being evaluated this cycle; a failure in the shared
    setup (backfill, candle read) aborts the whole iteration, same as
    shadow_poll_once.

    Every alert here is explicitly advisory — for the user's own manual
    execution on MT5/Exness, never an automated order on any venue."""
    backfill_candles(exchange, conn, exchange_id, symbol, timeframe, backfill_start_date, resume=True)
    fear_greed = maybe_refresh_fear_greed(conn)

    max_lookback = max((strategy.min_lookback for _, strategy in strategies), default=0)
    df = query_candles_df(conn, exchange_id, symbol, timeframe).tail(
        max(history_bars, max_lookback + 5)
    )
    df = drop_incomplete_bar(df, timeframe)

    for label, strategy in strategies:
        try:
            _process_one_strategy(
                conn, alerter, exchange_id, symbol, timeframe, label, strategy, df,
                fee, slippage, fear_greed,
            )
        except Exception:
            logger.exception("[reco_%s] recommend processing failed for this strategy this cycle", label)


def run_recommend_loop(
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    alerter: TelegramAlerter,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    strategies: list[tuple[str, Strategy]],
    fee: float,
    slippage: float,
    backfill_start_date: str,
    interval_seconds: int = 300,
    heartbeat_interval_seconds: int = 86400,
    jitter_seconds: int = 30,
) -> None:
    labels = ", ".join(label for label, _ in strategies)
    logger.info(
        "starting recommend loop for %s %s every %ds — strategies: %s "
        "(advisory only — no orders placed, ever, for your own manual review)",
        symbol, timeframe, interval_seconds, labels,
    )
    # same startup/per-iteration jitter as run_shadow_loop, for the same
    # reason — this process is one more thing polling Binance's public API
    time.sleep(random.uniform(0, interval_seconds))
    while True:
        try:
            recommend_poll_once(
                exchange, conn, alerter, exchange_id, symbol, timeframe, strategies,
                fee, slippage, backfill_start_date,
            )
            for label, strategy in strategies:
                reco_label = f"reco_{label}"
                maybe_send_daily_summary(
                    conn, alerter, exchange_id, symbol, timeframe, reco_label, strategy
                )
                maybe_send_heartbeat(
                    conn, alerter, symbol, timeframe, reco_label, heartbeat_interval_seconds
                )
        except Exception as exc:
            logger.exception("recommend loop iteration failed")
            alerter.send(format_error_alert(symbol, timeframe, "recommend", str(exc)))
        time.sleep(max(0.0, interval_seconds + random.uniform(-jitter_seconds, jitter_seconds)))

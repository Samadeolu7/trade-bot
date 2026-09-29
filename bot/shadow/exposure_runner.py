"""Paper trading for fraction-of-capital strategies (e.g. donchian_ensemble):
the live counterpart of bot/backtest/exposure.py, the way runner.py is the
live counterpart of the stop-based engine.

Each completed bar: mark the held fraction to market, then rebalance to
the strategy's target weight, using the exact mark_to_market/rebalance
functions the backtest uses. State (paper equity, held fraction, last bar
processed) lives in bot_state as JSON, so a restart picks up where it left
off. If the bot was down for several bars it catches up bar by bar, but
only alerts on the latest one, since a burst of stale rebalance alerts
isn't actionable."""

import json
import logging
import random
import sqlite3
import time

import ccxt
import pandas as pd

from bot.alerting.messages import format_error_alert, format_exposure_rebalance, format_exposure_summary
from bot.alerting.telegram import TelegramAlerter
from bot.backtest.exposure import mark_to_market, rebalance
from bot.data.backfill import backfill_candles
from bot.shadow.runner import drop_incomplete_bar, maybe_send_heartbeat
from bot.storage.db import (
    count_exposure_rebalances,
    get_state,
    query_candles_df,
    record_exposure_rebalance,
    set_state,
)
from bot.strategy.base import Strategy

logger = logging.getLogger(__name__)

INITIAL_PAPER_EQUITY = 10_000.0


def _state_key(strategy_label: str) -> str:
    return f"{strategy_label}:exposure_state"


def load_exposure_state(conn: sqlite3.Connection, strategy_label: str) -> dict | None:
    raw = get_state(conn, _state_key(strategy_label))
    return json.loads(raw) if raw else None


def _ms(ts: pd.Timestamp) -> int:
    return int(ts.value // 1_000_000)


def exposure_poll_once(
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    alerter: TelegramAlerter,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    strategy: Strategy,
    strategy_label: str,
    fee: float,
    slippage: float,
    backfill_start_date: str,
) -> None:
    backfill_candles(exchange, conn, exchange_id, symbol, timeframe, backfill_start_date, resume=True)
    # full history, not a trailing window: a long-lookback model can stay
    # in a position for months, and its ratcheted stop depends on the whole
    # path since entry — a truncated window could silently disagree with
    # the backtest about whether a model is long
    df = drop_incomplete_bar(query_candles_df(conn, exchange_id, symbol, timeframe), timeframe)
    process_exposure_bars(
        conn, alerter, exchange_id, symbol, timeframe, strategy, strategy_label, df, fee, slippage
    )


def process_exposure_bars(
    conn: sqlite3.Connection,
    alerter: TelegramAlerter,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    strategy: Strategy,
    strategy_label: str,
    df: pd.DataFrame,
    fee: float,
    slippage: float,
    advisory: bool = False,
) -> None:
    """Everything after data loading, shared by the paper bot and the
    recommend feed (advisory=True: RECOMMENDATION_REBALANCE alerts, under
    the reco_-prefixed label the caller passes). `df` must be the full
    completed-bar history, for the reason given in exposure_poll_once."""
    if len(df) < strategy.min_lookback:
        logger.info(
            "[%s] not enough complete history yet (%d/%d bars) — skipping",
            strategy_label, len(df), strategy.min_lookback,
        )
        return

    state = load_exposure_state(conn, strategy_label)
    last_ms = _ms(df.index[-1])
    if state is not None and state["last_bar_ms"] >= last_ms:
        return  # no new completed bar since the last poll

    weights = strategy.target_weights(df)
    threshold = getattr(strategy, "rebalance_threshold", 0.0)
    cost_rate = fee + slippage
    closes = df["close"]

    if state is None:
        # first run: start flat at the latest completed bar, then take that
        # bar's target, exactly like a backtest whose window starts there
        start = df.index[-1]
        state = {
            "equity": INITIAL_PAPER_EQUITY,
            "held": 0.0,
            "last_bar_ms": None,
            "last_close": None,
            "start_equity": INITIAL_PAPER_EQUITY,
            "started_ms": _ms(start),
        }
        new_bars = [start]
        logger.info("[%s] starting exposure paper run at %s", strategy_label, start)
    else:
        new_bars = list(df.index[df.index > pd.Timestamp(state["last_bar_ms"], unit="ms", tz="UTC")])

    pending_alerts = []
    for ts in new_bars:
        close = float(closes.loc[ts])
        held, equity = state["held"], state["equity"]
        if state["last_close"] is not None:
            held, equity = mark_to_market(held, equity, state["last_close"], close)
        want = float(weights.loc[ts])
        from_weight = held
        held, equity, traded = rebalance(held, equity, want, threshold, cost_rate)
        if traded:
            record_exposure_rebalance(
                conn, exchange_id, symbol, timeframe, strategy_label, ts, close, from_weight, held, equity
            )
            if ts == df.index[-1]:
                pending_alerts.append((from_weight, held, close, ts, equity))
        state.update(held=held, equity=equity, last_bar_ms=_ms(ts), last_close=close)
        set_state(conn, _state_key(strategy_label), json.dumps(state))  # commits the rebalance row too

    if pending_alerts:
        diagnosis = strategy.diagnose(df)
        for from_weight, to_weight, price, ts, equity in pending_alerts:
            logger.info(
                "[%s] rebalance %.1f%% -> %.1f%% at %.2f", strategy_label, from_weight * 100, to_weight * 100, price
            )
            alerter.send(
                format_exposure_rebalance(
                    symbol, timeframe, strategy_label, from_weight, to_weight, price, ts, equity, diagnosis,
                    advisory=advisory,
                )
            )


def maybe_send_exposure_summary(
    conn: sqlite3.Connection, alerter: TelegramAlerter, exchange_id: str, symbol: str, timeframe: str,
    strategy_label: str, strategy: Strategy,
) -> None:
    today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    summary_key = f"{strategy_label}:last_summary_date"
    if get_state(conn, summary_key) == today:
        return
    state = load_exposure_state(conn, strategy_label)
    if state is None:
        return  # nothing to summarize until the first bar is processed
    df = drop_incomplete_bar(query_candles_df(conn, exchange_id, symbol, timeframe), timeframe)
    diagnosis = strategy.diagnose(df) if len(df) >= strategy.min_lookback else {}
    rebalances = count_exposure_rebalances(conn, exchange_id, symbol, timeframe, strategy_label)
    sent = alerter.send(
        format_exposure_summary(
            symbol, timeframe, strategy_label, state["held"], state["equity"], state["start_equity"],
            state["started_ms"], rebalances, diagnosis,
        )
    )
    if sent:
        set_state(conn, summary_key, today)


def run_exposure_shadow_loop(
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    alerter: TelegramAlerter,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    strategy: Strategy,
    strategy_label: str,
    fee: float,
    slippage: float,
    backfill_start_date: str,
    interval_seconds: int = 300,
    heartbeat_interval_seconds: int = 86400,
    jitter_seconds: int = 30,
) -> None:
    logger.info(
        "starting exposure shadow run '%s' for %s %s every %ds (paper trading only — no orders placed)",
        strategy_label, symbol, timeframe, interval_seconds,
    )
    # same startup stagger / jitter as run_shadow_loop, for the same reason
    time.sleep(random.uniform(0, interval_seconds))
    while True:
        try:
            exposure_poll_once(
                exchange, conn, alerter, exchange_id, symbol, timeframe, strategy, strategy_label,
                fee, slippage, backfill_start_date,
            )
            maybe_send_exposure_summary(conn, alerter, exchange_id, symbol, timeframe, strategy_label, strategy)
            maybe_send_heartbeat(conn, alerter, symbol, timeframe, strategy_label, heartbeat_interval_seconds)
        except Exception as exc:
            logger.exception("exposure shadow run iteration failed")
            alerter.send(format_error_alert(symbol, timeframe, strategy_label, str(exc)))
        time.sleep(max(0.0, interval_seconds + random.uniform(-jitter_seconds, jitter_seconds)))

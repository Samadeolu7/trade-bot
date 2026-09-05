import logging
import sqlite3
import time

import ccxt

from bot.data.exchange import fetch_ohlcv
from bot.storage.db import upsert_candles

logger = logging.getLogger(__name__)


def poll_once(
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    exchange_id: str,
    symbol: str,
    timeframe: str,
) -> int:
    """Fetch the last couple of candles and upsert — safely overwrites the
    still-forming candle as it completes."""
    candles = fetch_ohlcv(exchange, symbol, timeframe, limit=2)
    return upsert_candles(conn, exchange_id, symbol, timeframe, candles)


def run_poll_loop(
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    interval_seconds: int = 60,
) -> None:
    logger.info(
        "starting poll loop for %s %s every %ds", symbol, timeframe, interval_seconds
    )
    while True:
        try:
            count = poll_once(exchange, conn, exchange_id, symbol, timeframe)
            logger.info("polled %d candle(s) for %s %s", count, symbol, timeframe)
        except Exception:
            logger.exception("poll iteration failed, will retry after interval")
        time.sleep(interval_seconds)

import logging
import sqlite3

import ccxt

from bot.data.exchange import fetch_ohlcv
from bot.storage.db import get_latest_open_time, upsert_candles

logger = logging.getLogger(__name__)


def backfill_candles(
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    start_date: str,
    resume: bool = True,
    limit: int = 1000,
) -> int:
    """Paginate ccxt.fetch_ohlcv forward from start_date until caught up to now.
    Upserts each page immediately so a crash mid-backfill loses at most one page.
    """
    since = ccxt.Exchange.parse8601(start_date)

    if resume:
        latest = get_latest_open_time(conn, exchange_id, symbol, timeframe)
        if latest is not None:
            timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
            since = max(since, latest + timeframe_ms)

    total = 0
    while True:
        candles = fetch_ohlcv(exchange, symbol, timeframe, since=since, limit=limit)
        if not candles:
            break

        total += upsert_candles(conn, exchange_id, symbol, timeframe, candles)
        logger.info(
            "backfilled %d candles for %s %s (up to %s)",
            len(candles),
            symbol,
            timeframe,
            candles[-1][0],
        )

        timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
        next_since = candles[-1][0] + timeframe_ms
        if next_since <= since or len(candles) < limit:
            break
        since = next_since

    return total

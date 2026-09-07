import logging
import sqlite3

import ccxt

from bot.data.funding import fetch_funding_rate_history
from bot.storage.db import get_latest_funding_time, upsert_funding_rates

logger = logging.getLogger(__name__)

# Binance prints a new perpetual funding rate every 8h — used only to advance
# `since` past the last stored print on resume, not to predict exact timing.
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000


def backfill_funding_rates(
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    exchange_id: str,
    symbol: str,
    start_date: str,
    resume: bool = True,
    limit: int = 1000,
) -> int:
    """Same paginate-and-upsert-immediately shape as backfill_candles, over
    the much sparser funding-rate history."""
    since = ccxt.Exchange.parse8601(start_date)

    if resume:
        latest = get_latest_funding_time(conn, exchange_id, symbol)
        if latest is not None:
            since = max(since, latest + FUNDING_INTERVAL_MS)

    total = 0
    while True:
        rates = fetch_funding_rate_history(exchange, symbol, since=since, limit=limit)
        if not rates:
            break

        total += upsert_funding_rates(conn, exchange_id, symbol, rates)
        logger.info(
            "backfilled %d funding rate(s) for %s (up to %s)",
            len(rates), symbol, rates[-1]["timestamp"],
        )

        next_since = rates[-1]["timestamp"] + FUNDING_INTERVAL_MS
        if next_since <= since or len(rates) < limit:
            break
        since = next_since

    return total

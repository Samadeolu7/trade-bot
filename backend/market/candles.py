"""Candle storage and ingestion for the platform: the Postgres counterpart
of bot/data/backfill.py + bot/storage/db.py's query_candles_df, returning
frames in exactly the shape the strategies already expect."""

import logging
from datetime import datetime, timezone

import ccxt
import pandas as pd

from bot.data.exchange import fetch_ohlcv
from bot.data.funding import create_funding_exchange, fetch_funding_rate_history
from market.models import Candle, FundingRate

logger = logging.getLogger(__name__)

FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def _dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def upsert_candles(exchange_id: str, symbol: str, timeframe: str, rows: list[list]) -> int:
    objs = [
        Candle(
            exchange=exchange_id, symbol=symbol, timeframe=timeframe, open_time=_ms_to_dt(r[0]),
            open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5],
        )
        for r in rows
    ]
    Candle.objects.bulk_create(
        objs,
        update_conflicts=True,
        unique_fields=["exchange", "symbol", "timeframe", "open_time"],
        update_fields=["open", "high", "low", "close", "volume"],
    )
    return len(objs)


def backfill_candles(
    exchange: ccxt.Exchange, exchange_id: str, symbol: str, timeframe: str, start_date: str, limit: int = 1000
) -> int:
    """Resumes from the latest stored candle, and re-fetches that one: the
    newest bar is usually still forming when it was stored."""
    since = ccxt.Exchange.parse8601(start_date)
    latest = (
        Candle.objects.filter(exchange=exchange_id, symbol=symbol, timeframe=timeframe)
        .order_by("-open_time").values_list("open_time", flat=True).first()
    )
    if latest is not None:
        since = max(since, _dt_to_ms(latest))

    timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
    total = 0
    while True:
        rows = fetch_ohlcv(exchange, symbol, timeframe, since=since, limit=limit)
        if not rows:
            break
        total += upsert_candles(exchange_id, symbol, timeframe, rows)
        next_since = rows[-1][0] + timeframe_ms
        if next_since <= since or len(rows) < limit:
            break
        since = next_since
    return total


def backfill_head(
    exchange: ccxt.Exchange, exchange_id: str, symbol: str, timeframe: str, start_date: str, limit: int = 1000
) -> int:
    """Fills history *before* the earliest stored candle, back to
    `start_date`. backfill_candles only moves forward, so a symbol first
    fetched from 2020 would otherwise never get its 2018–2019 history.
    Stops at whatever the exchange has: a coin listed later just starts
    later."""
    earliest = (
        Candle.objects.filter(exchange=exchange_id, symbol=symbol, timeframe=timeframe)
        .order_by("open_time").values_list("open_time", flat=True).first()
    )
    if earliest is None:
        return 0  # nothing stored yet: backfill_candles fetches from start_date anyway
    since = ccxt.Exchange.parse8601(start_date)
    stop = _dt_to_ms(earliest)
    timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
    total = 0
    while since < stop:
        rows = [r for r in fetch_ohlcv(exchange, symbol, timeframe, since=since, limit=limit) if r[0] < stop]
        if not rows:
            break
        total += upsert_candles(exchange_id, symbol, timeframe, rows)
        next_since = rows[-1][0] + timeframe_ms
        if next_since <= since:
            break
        since = next_since
    return total


def backfill_funding(exchange_id: str, symbol: str, start_date: str) -> int:
    exchange = create_funding_exchange(exchange_id)
    since = ccxt.Exchange.parse8601(start_date)
    latest = (
        FundingRate.objects.filter(exchange=exchange_id, symbol=symbol)
        .order_by("-funding_time").values_list("funding_time", flat=True).first()
    )
    if latest is not None:
        since = max(since, _dt_to_ms(latest) + FUNDING_INTERVAL_MS)
    total = 0
    while True:
        rates = fetch_funding_rate_history(exchange, symbol, since=since, limit=1000)
        if not rates:
            break
        FundingRate.objects.bulk_create(
            [
                FundingRate(exchange=exchange_id, symbol=symbol, funding_time=_ms_to_dt(r["timestamp"]),
                            funding_rate=r["fundingRate"])
                for r in rates
            ],
            ignore_conflicts=True,
        )
        total += len(rates)
        next_since = rates[-1]["timestamp"] + FUNDING_INTERVAL_MS
        if next_since <= since or len(rates) < 1000:
            break
        since = next_since
    return total


def candles_df(exchange_id: str, symbol: str, timeframe: str, limit: int | None = None) -> pd.DataFrame:
    """Same shape as bot.storage.db.query_candles_df: ascending, indexed by
    a tz-aware ns `open_time`, columns open/high/low/close/volume."""
    qs = Candle.objects.filter(exchange=exchange_id, symbol=symbol, timeframe=timeframe)
    if limit:
        ids = list(qs.order_by("-open_time").values_list("id", flat=True)[:limit])
        qs = Candle.objects.filter(id__in=ids)
    rows = list(qs.order_by("open_time").values_list("open_time", "open", "high", "low", "close", "volume"))
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True).astype("datetime64[ns, UTC]")
    return df.set_index("open_time")


def funding_df(exchange_id: str, symbol: str) -> pd.DataFrame:
    rows = list(
        FundingRate.objects.filter(exchange=exchange_id, symbol=symbol)
        .order_by("funding_time").values_list("funding_time", "funding_rate")
    )
    df = pd.DataFrame(rows, columns=["funding_time", "funding_rate"])
    df["funding_time"] = pd.to_datetime(df["funding_time"], utc=True).astype("datetime64[ns, UTC]")
    return df.set_index("funding_time")

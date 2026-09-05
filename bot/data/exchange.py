import ccxt


def create_exchange(exchange_id: str = "binance") -> ccxt.Exchange:
    """Public/read-only client — no API key, no credentials, no order placement."""
    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class({"enableRateLimit": True})


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since: int | None = None,
    limit: int = 1000,
) -> list[list]:
    return exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)

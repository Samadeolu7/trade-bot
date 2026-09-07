import ccxt


def create_funding_exchange(exchange_id: str = "binanceusdm") -> ccxt.Exchange:
    """Funding rates are a perpetual-futures concept — Binance's spot market
    (used everywhere else for OHLCV/backtesting) has none. A separate USD-M
    futures client is needed purely to read funding-rate history read-only;
    no API key, same no-credentials constraint as the spot client. Quidax is
    spot-only, so this data is used only as a filter/confirmation input
    (spec Section 7d) — the trade itself never executes here."""
    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class({"enableRateLimit": True})


def fetch_funding_rate_history(
    exchange: ccxt.Exchange, symbol: str, since: int | None = None, limit: int = 1000
) -> list[dict]:
    return exchange.fetchFundingRateHistory(symbol, since=since, limit=limit)

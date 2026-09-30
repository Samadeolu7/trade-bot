"""Where prices come from, for paper fills, engine-held stops and the
trade ticket. All public endpoints, no keys."""

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

import requests

from bot.broker.base import BrokerError, Depth, Quote

logger = logging.getLogger(__name__)

QUIDAX_BASE_URL = "https://openapi.quidax.io/exchange-open-api/api/v1"


class QuoteSource(Protocol):
    def quote(self, symbol: str) -> Quote: ...

    def depth(self, symbol: str) -> Depth | None:
        """Real book levels, or None when this source has none."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class QuidaxQuoteSource:
    """Quidax's `/markets/{market}/depth` endpoint. Its `/order_book`
    endpoint is not used: it returns raw order records, including years-old
    filled ones at price 0, rather than the aggregated book (checked
    2026-09-29)."""

    def __init__(self, base_url: str = QUIDAX_BASE_URL, levels: int = 50, ttl_seconds: float = 1.0):
        self.base_url = base_url
        self.levels = levels
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, Depth, datetime]] = {}

    @staticmethod
    def market(symbol: str) -> str:
        return symbol.replace("/", "").lower()

    def _fetch(self, symbol: str) -> tuple[Depth, datetime]:
        cached = self._cache.get(symbol)
        if cached and time.monotonic() - cached[0] < self.ttl_seconds:
            return cached[1], cached[2]
        url = f"{self.base_url}/markets/{self.market(symbol)}/depth"
        try:
            response = requests.get(url, params={"limit": self.levels}, timeout=10)
            response.raise_for_status()
            data = response.json()["data"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise BrokerError(f"Quidax depth for {symbol} unavailable: {exc}") from exc

        bids = sorted(((Decimal(str(p)), Decimal(str(v))) for p, v in data.get("bids", [])), reverse=True)
        asks = sorted((Decimal(str(p)), Decimal(str(v))) for p, v in data.get("asks", []))
        bids = [(p, v) for p, v in bids if p > 0 and v > 0]
        asks = [(p, v) for p, v in asks if p > 0 and v > 0]
        if not bids or not asks:
            raise BrokerError(f"Quidax depth for {symbol} has an empty side")
        stamp = data.get("timestamp")
        at = datetime.fromtimestamp(stamp / 1000, timezone.utc) if stamp else _now()
        depth = Depth(bids=bids, asks=asks)
        self._cache[symbol] = (time.monotonic(), depth, at)
        return depth, at

    def quote(self, symbol: str) -> Quote:
        depth, at = self._fetch(symbol)
        bid, ask = depth.bids[0][0], depth.asks[0][0]
        return Quote(symbol=symbol, bid=bid, ask=ask, last=(bid + ask) / 2, time=at)

    def depth(self, symbol: str) -> Depth | None:
        return self._fetch(symbol)[0]


class CcxtQuoteSource:
    """A reference price from a ccxt exchange (Binance by default), with a
    synthetic spread around it. Used for venues whose own book we can't
    see, like an Exness CFD."""

    def __init__(self, exchange_id: str = "binance", spread: Decimal = Decimal(0), ttl_seconds: float = 1.0):
        import ccxt

        self.exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True, "timeout": 10000})
        self.spread = spread
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, Quote]] = {}

    def quote(self, symbol: str) -> Quote:
        cached = self._cache.get(symbol)
        if cached and time.monotonic() - cached[0] < self.ttl_seconds:
            return cached[1]
        try:
            ticker = self.exchange.fetch_ticker(symbol)
        except Exception as exc:
            raise BrokerError(f"{self.exchange.id} ticker for {symbol} unavailable: {exc}") from exc
        last = Decimal(str(ticker["last"]))
        half = last * self.spread / 2
        quote = Quote(symbol=symbol, bid=last - half, ask=last + half, last=last, time=_now())
        self._cache[symbol] = (time.monotonic(), quote)
        return quote

    def depth(self, symbol: str) -> Depth | None:
        return None


class FallbackQuoteSource:
    """Tries each source in order. Binance isn't reachable from every
    network (it times out from the development machine), so the CFD
    profile falls back to Quidax's mid price with its own spread applied."""

    def __init__(self, sources: list, spread: Decimal = Decimal(0), retry_after_seconds: float = 60.0):
        self.sources = sources
        self.spread = spread
        self.retry_after_seconds = retry_after_seconds
        # a source that just failed is skipped for a while rather than
        # costing a full timeout on every quote
        self._failed_at: dict[int, float] = {}

    def quote(self, symbol: str) -> Quote:
        errors = []
        for i, source in enumerate(self.sources):
            failed = self._failed_at.get(i)
            if failed is not None and time.monotonic() - failed < self.retry_after_seconds and i < len(self.sources) - 1:
                continue
            try:
                quote = source.quote(symbol)
            except BrokerError as exc:
                self._failed_at[i] = time.monotonic()
                errors.append(str(exc))
                continue
            self._failed_at.pop(i, None)
            if i == 0 or self.spread == 0:
                return quote
            half = quote.mid * self.spread / 2
            return Quote(symbol, quote.mid - half, quote.mid + half, quote.mid, quote.time)
        raise BrokerError("; ".join(errors))

    def depth(self, symbol: str) -> Depth | None:
        return None


class StaticQuoteSource:
    """Fixed prices, for tests and for replaying a scenario."""

    def __init__(self, quotes: dict[str, Quote] | None = None, depths: dict[str, Depth] | None = None):
        self.quotes = quotes or {}
        self.depths = depths or {}

    def set(self, symbol: str, bid, ask, last=None) -> None:
        bid, ask = Decimal(str(bid)), Decimal(str(ask))
        last = Decimal(str(last)) if last is not None else (bid + ask) / 2
        self.quotes[symbol] = Quote(symbol, bid, ask, last, _now())

    def quote(self, symbol: str) -> Quote:
        if symbol not in self.quotes:
            raise BrokerError(f"no static quote for {symbol}")
        return self.quotes[symbol]

    def depth(self, symbol: str) -> Depth | None:
        return self.depths.get(symbol)


def quote_source_for(venue) -> QuoteSource:
    if venue.quote_source == "quidax":
        return QuidaxQuoteSource()
    return FallbackQuoteSource([CcxtQuoteSource(spread=venue.spread), QuidaxQuoteSource()], spread=venue.spread)

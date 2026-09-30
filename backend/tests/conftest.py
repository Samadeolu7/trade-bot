from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot.broker.quotes import StaticQuoteSource
from bot.broker.venues import VENUES

SYMBOL = "BTC/USDT"


@pytest.fixture(autouse=True)
def _settings(db, settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.REQUIRE_2FA = False
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def quotes():
    """Every venue quotes from one controllable static source."""
    from trading.services import brokers

    source = StaticQuoteSource()
    source.set(SYMBOL, 100, 101)
    for key in VENUES:
        brokers.set_quote_source(key, source)
    yield source
    brokers._quote_sources.clear()


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user("owner", password="correct-horse-battery", role="owner")


@pytest.fixture
def account(quotes, owner):
    from trading.models import TradingAccount
    from trading.services.orders import fund_paper_account

    acct = TradingAccount.objects.create(name="Paper", venue="quidax_spot")
    fund_paper_account(acct, Decimal(10_000), user=owner)
    return acct


def make_candles(closes, timeframe="1d", start=None, exchange="binance", symbol=SYMBOL):
    from market.candles import upsert_candles

    step = {"1h": 3600, "4h": 14400, "1d": 86400}[timeframe]
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i, c in enumerate(closes):
        t = start + timedelta(seconds=step * i)
        rows.append([int(t.timestamp() * 1000), c, c + 0.5, c - 0.5, c, 1.0])
    upsert_candles(exchange, symbol, timeframe, rows)

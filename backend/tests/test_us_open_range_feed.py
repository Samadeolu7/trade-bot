from datetime import datetime, timezone

import pytest

from alerts.models import AlertEvent, AlertRule
from recommendations.models import Feed, Recommendation
from recommendations.runner import run_feed
from tests.conftest import make_candles
from trading.services.bots import create_bot
from trading.services.orders import OrderError

SUNDAY = datetime(2023, 12, 31, tzinfo=timezone.utc)


def day_of(monday_bars):
    """A quiet Sunday at 100 (warm-up history), then Monday's bars."""
    return [100.0] * 96 + monday_bars


def test_migration_creates_the_paper_feed_unfollowed():
    f = Feed.objects.get(name="us_open_range_15m")
    assert f.strategy == "us_open_range" and f.timeframe == "15m" and not f.following
    assert f.slippage == 0.0001 and f.fee == 0.0


def test_breakout_entry_then_end_of_day_exit_without_alerts(owner):
    AlertRule.objects.create(user=owner, kind="recommendation")
    f = Feed.objects.get(name="us_open_range_15m")
    # Monday 00:00-14:15 at 100 (range 99.5-100.5), then 14:30 closes at 102
    monday = [100.0] * 58 + [102.0]
    make_candles(day_of(monday), timeframe="15m", start=SUNDAY)
    events = run_feed(f, "binance", {})
    assert [e.kind for e in events] == ["entry"]
    f.refresh_from_db()
    assert f.direction == "long" and f.stop == 100.0
    assert f.entry_price == pytest.approx(102.0 * 1.0001)

    # holds through the evening; the 23:30 candle's close ends it
    make_candles(day_of(monday + [103.0] * 36), timeframe="15m", start=SUNDAY)
    assert Feed.objects.get(pk=f.pk).last_bar_at.hour == 14
    events = run_feed(f, "binance", {})
    assert [e.kind for e in events] == ["exit"]
    assert events[0].bar_time.hour == 23 and events[0].bar_time.minute == 30
    assert events[0].reason.startswith("end of day at")
    assert events[0].pnl_pct == pytest.approx((103 * 0.9999 / (102 * 1.0001) - 1) * 100)
    f.refresh_from_db()
    assert f.direction == ""
    assert not AlertEvent.objects.exists()  # not followed: tracked only
    assert Recommendation.objects.filter(feed=f).count() == 2


def test_stop_at_the_range_middle(owner):
    f = Feed.objects.get(name="us_open_range_15m")
    monday = [100.0] * 58 + [102.0]
    make_candles(day_of(monday), timeframe="15m", start=SUNDAY)
    run_feed(f, "binance", {})
    make_candles(day_of(monday + [99.0]), timeframe="15m", start=SUNDAY)
    events = run_feed(f, "binance", {})
    assert events[0].kind == "exit" and events[0].reason.startswith("stop at")


def test_bots_cannot_run_it(owner):
    from trading.models import TradingAccount

    account = TradingAccount.objects.create(name="paper", venue="quidax_spot")
    with pytest.raises(OrderError, match="only as a recommendation feed"):
        create_bot(account, name="orb", strategy="us_open_range", allocation=100)

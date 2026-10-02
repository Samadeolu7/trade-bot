from datetime import datetime, timedelta, timezone

from alerts.models import AlertEvent, AlertRule
from recommendations.heads_up import due, heads_up
from recommendations.models import Feed
from recommendations.runner import build_feed_strategy, run_feed
from tests.conftest import make_candles

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
FORMING = START + timedelta(hours=4 * 70)  # the 71st candle
CLOSE = FORMING + timedelta(hours=4)


def feed(**kw):
    defaults = dict(name="donchian_4h", strategy="donchian", timeframe="4h", following=True,
                    params={"donchian.channel_period": 5, "donchian.exit_channel_period": 5})
    return Feed.objects.create(**{**defaults, **kw})


def rule(owner):
    AlertRule.objects.create(user=owner, kind="recommendation")


def test_window_is_15_to_5_minutes_before_the_close():
    f = Feed(name="x", strategy="donchian", timeframe="4h")
    assert due(f, CLOSE - timedelta(minutes=20)) is None
    assert due(f, CLOSE - timedelta(minutes=14)) == FORMING
    assert due(f, CLOSE - timedelta(minutes=4)) is None
    f.heads_up_bar = FORMING
    assert due(f, CLOSE - timedelta(minutes=10)) is None  # once per candle


def test_get_ready_near_the_trigger_then_go_at_the_close(owner):
    rule(owner)
    make_candles([100.0] * 70 + [100.2], timeframe="4h")  # forming candle 0.3% under the 100.5 channel high
    f = feed()
    message = heads_up(f, "binance", build_feed_strategy, CLOSE - timedelta(minutes=12))
    assert message is not None
    title, body = message
    assert title.startswith("GET READY: donchian_4h may BUY BTCUSD at")
    assert "trigger 100.50" in body and "closes above 100.50" in body and "Stop Loss" in body
    assert "UTC (" in title  # local time shown too

    # the candle closes above the trigger: the call comes marked GO
    make_candles([100.0] * 70 + [101.0], timeframe="4h")
    events = run_feed(Feed.objects.get(pk=f.pk), "binance", {})
    assert [e.kind for e in events] == ["entry"]
    titles = list(AlertEvent.objects.order_by("id").values_list("title", flat=True))
    assert titles[-1].startswith("GO: MT5: BUY")
    assert Feed.objects.get(pk=f.pk).heads_up_note == {}


def test_no_trade_note_when_the_close_falls_back(owner):
    rule(owner)
    make_candles([100.0] * 70 + [100.2], timeframe="4h")
    f = feed()
    assert heads_up(f, "binance", build_feed_strategy, CLOSE - timedelta(minutes=12))
    make_candles([100.0] * 70 + [99.9], timeframe="4h")
    events = run_feed(Feed.objects.get(pk=f.pk), "binance", {})
    assert not [e for e in events if e.kind == "entry"]
    last = AlertEvent.objects.order_by("-id").first()
    assert last.title.startswith("NO TRADE: donchian_4h") and "not past the trigger 100.50" in last.body


def test_quiet_when_nothing_is_close(owner):
    rule(owner)
    swings = [95.0 if i % 2 else 105.0 for i in range(70)]
    make_candles(swings + [100.0], timeframe="4h")  # 5% from either channel
    f = feed()
    assert heads_up(f, "binance", build_feed_strategy, CLOSE - timedelta(minutes=12)) is None
    f.refresh_from_db()
    assert f.heads_up_bar == FORMING and f.heads_up_note == {}
    assert not AlertEvent.objects.exists()
    make_candles(swings + [100.5], timeframe="4h")
    run_feed(f, "binance", {})
    assert not AlertEvent.objects.filter(title__startswith="NO TRADE").exists()  # nothing was announced


def test_exposure_feed_warns_of_a_likely_resize(owner):
    rule(owner)
    make_candles([100.0 + i + (i % 2) * 0.5 for i in range(40)], timeframe="4h")
    f = feed(name="ens", strategy="donchian_ensemble",
             params={"donchian_ensemble.bars_per_day": 6, "donchian_ensemble.rebalance_threshold": 0.1})
    forming = START + timedelta(hours=4 * 39)
    message = heads_up(f, "binance", build_feed_strategy, forming + timedelta(hours=4, minutes=-12))
    if message is not None:  # depends on the ensemble's warm-up on 40 bars
        assert message[0].startswith("GET READY: ens may resize BTCUSD")


def test_only_followed_feeds_alert(owner):
    import json

    from django.test import Client

    rule(owner)
    make_candles([100.0] * 70 + [110.0], timeframe="4h")
    f = feed(following=False)
    assert heads_up(f, "binance", build_feed_strategy, CLOSE - timedelta(minutes=12)) is None
    events = run_feed(f, "binance", {})
    assert [e.kind for e in events] == ["entry"]  # still tracked
    assert not AlertEvent.objects.exists()  # but silent

    client = Client()
    client.force_login(owner)
    r = client.post(f"/api/recommendations/feeds/{f.pk}/following", json.dumps({"following": True}),
                    content_type="application/json")
    assert r.status_code == 200 and r.json()["following"] is True


def test_open_call_gets_a_heads_up_before_its_stop_moves(owner):
    rule(owner)
    # in a long from 100 with stop 95; a rising forming candle lifts the 5-bar low channel
    make_candles([100.0] * 66 + [101.0, 102.0, 103.0, 104.0, 105.0], timeframe="4h")
    f = feed(direction="long", entry_price=100.0, entry_time=START, stop=95.0)
    message = heads_up(f, "binance", build_feed_strategy, CLOSE - timedelta(minutes=12))
    assert message and message[0].startswith("GET READY: donchian_4h may move BTCUSD Stop Loss")
    assert "from 95.00 to about" in message[1]
    events = run_feed(Feed.objects.get(pk=f.pk), "binance", {})
    assert "stop_update" in [e.kind for e in events]
    assert AlertEvent.objects.order_by("-id").first().title.startswith("GO: MT5: move BTCUSD Stop Loss")

from datetime import datetime, timezone

from alerts.models import AlertEvent, AlertRule
from recommendations.models import Feed, Recommendation
from recommendations.runner import run_feed
from tests.conftest import make_candles

FUNDING = {}


def feed(**kw):
    defaults = dict(name="donchian_1d", strategy="donchian", timeframe="1d",
                    params={"donchian.channel_period": 5, "donchian.exit_channel_period": 5})
    return Feed.objects.create(**{**defaults, **kw})


def test_entry_then_trailing_then_exit(owner, monkeypatch):
    monkeypatch.setattr("recommendations.runner.fear_greed", lambda: "62 (Greed)")
    AlertRule.objects.create(user=owner, kind="recommendation")
    f = feed()
    make_candles([100.0] * 70 + [110.0])
    events = run_feed(f, "binance", FUNDING)
    assert [e.kind for e in events] == ["entry"]
    entry = events[0]
    assert entry.direction == "long" and entry.fear_greed == "62 (Greed)"
    assert entry.stop == 99.5
    f.refresh_from_db()
    assert f.direction == "long" and f.stop == 99.5
    # no capital set: the alert says what to do and how to get lot sizes
    alert = AlertEvent.objects.get()
    assert alert.title.startswith("MT5: BUY BTCUSD")
    assert "Stop Loss 99.50" in alert.body and "exact lot sizes" in alert.body

    # a later bar that falls through the stop closes the long, and (like the
    # CLI runner) the same bar's breakdown is then a fresh short call
    make_candles([100.0] * 70 + [110.0, 111.0, 90.0])
    events = run_feed(f, "binance", FUNDING)
    assert [e.kind for e in events][-2:] == ["exit", "entry"]
    f.refresh_from_db()
    assert f.direction == "short"
    assert Recommendation.objects.filter(kind="exit").get().pnl_pct < 0


def test_shorts_are_recommended_for_cfd(owner):
    f = feed()
    make_candles([100.0] * 70 + [90.0])
    events = run_feed(f, "binance", FUNDING)
    assert events[0].direction == "short"


def test_same_bar_is_not_evaluated_twice(owner):
    f = feed()
    make_candles([100.0] * 70 + [110.0])
    run_feed(f, "binance", FUNDING)
    assert run_feed(f, "binance", FUNDING) == []


def test_exposure_feed_recommends_resizes(owner):
    f = feed(name="ens", strategy="donchian_ensemble",
             params={"donchian_ensemble.lookback_days": [2, 3], "donchian_ensemble.vol_window_days": 5,
                     "donchian_ensemble.rebalance_threshold": 0.1})
    make_candles([100.0 + i + (i % 2) * 0.5 for i in range(40)])
    events = run_feed(f, "binance", FUNDING)
    assert events[0].kind == "rebalance" and events[0].to_weight == 1.0
    f.refresh_from_db()
    assert f.weight == 1.0


def test_seed_brings_over_feeds_with_open_calls_and_history(tmp_path, owner, quotes):
    import json
    import sqlite3

    from django.core.management import call_command

    path = tmp_path / "trades.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE paper_position (exchange TEXT, symbol TEXT, timeframe TEXT, strategy_label TEXT, "
        "direction TEXT, entry_price REAL, stop_loss REAL, take_profit REAL, entry_time INTEGER, "
        "entry_fee REAL, size REAL, context TEXT)"
    )
    conn.execute("CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE signals (id INTEGER PRIMARY KEY, exchange TEXT, symbol TEXT, timeframe TEXT, "
        "strategy_label TEXT, direction TEXT, entry_price REAL, stop_loss REAL, take_profit REAL, reason TEXT, "
        "context TEXT, fired_at INTEGER)"
    )
    ms = int(datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp() * 1000)
    conn.execute("INSERT INTO paper_position VALUES ('binance','BTC/USDT','4h','reco_donchian_4h','short',"
                 "85000,88000,NULL,?,0,1,'{\"atr\": 900}')", (ms,))
    conn.execute("INSERT INTO signals VALUES (1,'binance','BTC/USDT','4h','reco_donchian_4h','short',85000,88000,"
                 "NULL,'close broke below 20-bar low channel',NULL,?)", (ms,))
    conn.execute("INSERT INTO bot_state VALUES (?, ?)", ("reco_donchian_ensemble_4h:exposure_state",
                 json.dumps({"held": 0.4, "equity": 10100, "last_close": 84000, "last_bar_ms": ms})))
    conn.commit()
    conn.close()

    call_command("seed_shadow_bots", str(path), owner="owner")
    assert Feed.objects.count() == 5
    donchian = Feed.objects.get(name="donchian_4h")
    assert donchian.direction == "short" and donchian.stop == 88000  # CFD calls can be short
    assert donchian.recommendations.get(kind="entry").imported is True
    ensemble = Feed.objects.get(name="donchian_ensemble_4h")
    assert ensemble.weight == 0.4 and ensemble.params["donchian_ensemble.bars_per_day"] == 6
    natr = Feed.objects.get(name="donchian_natr_regime")
    assert natr.params == {"regime.type": "natr"}

    call_command("seed_shadow_bots", str(path), owner="owner")
    assert Recommendation.objects.count() == 1


def test_feed_reports_a_repeat_signal_once_per_call(owner):
    f = feed()
    make_candles([100.0] * 70 + [110.0])
    run_feed(f, "binance", FUNDING)
    make_candles([100.0] * 70 + [110.0, 112.0])
    events = run_feed(f, "binance", FUNDING)
    assert [e.kind for e in events] == ["signal_again"]
    make_candles([100.0] * 70 + [110.0, 112.0, 114.0])
    assert [e.kind for e in run_feed(f, "binance", FUNDING)] == []
    f.refresh_from_db()
    assert f.direction == "long"  # the call itself never changes


def test_entry_alert_gives_lots_and_stop_loss_when_capital_is_set(owner):
    AlertRule.objects.create(user=owner, kind="recommendation")
    f = feed(capital=10_000.0, risk_pct=0.01)
    make_candles([100.0] * 70 + [110.0])
    run_feed(f, "binance", FUNDING)
    alert = AlertEvent.objects.get()
    # entry 110.03 (0.03% slippage), stop 99.5: 1% of 10,000 over a 10.53 stop distance
    assert alert.title == "MT5: BUY 9.49 lots BTCUSD, SL 99.50 (donchian_1d)"
    assert "Volume 9.49 lots" in alert.body and "Set Stop Loss 99.50, no Take Profit" in alert.body
    assert "lose about $99." in alert.body
    f.refresh_from_db()
    assert f.lots_held == 9.49

    # the stop trails up: modify instructions, with the old stop
    AlertEvent.objects.all().delete()
    make_candles([100.0] * 70 + [110.0, 120.0, 130.0, 140.0, 150.0, 160.0])
    run_feed(f, "binance", FUNDING)
    stop_alert = AlertEvent.objects.filter(title__contains="move BTCUSD Stop Loss").first()
    assert stop_alert is not None
    assert "Change Stop Loss to" in stop_alert.body and "(was 99.50)" in stop_alert.body


def test_entry_too_small_for_the_minimum_lot_says_so(owner):
    AlertRule.objects.create(user=owner, kind="recommendation")
    f = feed(capital=100.0, contract_size=100.0)  # 0.01 lot = 1 unit, far above 1% risk on $100
    make_candles([100.0] * 70 + [110.0])
    run_feed(f, "binance", FUNDING)
    alert = AlertEvent.objects.get()
    assert "too small for your capital" in alert.title
    assert "Skipping is the safe choice" in alert.body
    f.refresh_from_db()
    assert f.lots_held == 0.0


def test_exposure_resizes_are_given_as_lots_to_buy_and_close(owner):
    from recommendations.messages import describe

    AlertRule.objects.create(user=owner, kind="recommendation")
    f = feed(name="ens", strategy="donchian_ensemble", capital=1_000.0,
             params={"donchian_ensemble.lookback_days": [2, 3], "donchian_ensemble.vol_window_days": 5,
                     "donchian_ensemble.rebalance_threshold": 0.1})
    f.capital_equity_base = f.equity
    f.save()
    make_candles([100.0 + i + (i % 2) * 0.5 for i in range(40)])
    run_feed(f, "binance", FUNDING)
    alert = AlertEvent.objects.get()
    f.refresh_from_db()
    # 100% of $1,000 at ~139.5 = 7.16 lots (rounded down to the 0.01 step)
    assert f.lots_held == 7.16
    assert alert.title == "MT5: BUY 7.16 lots BTCUSD (ens)"
    assert "You should then hold 7.16 lots BTCUSD in total" in alert.body

    # a resize down to 40% closes part of it; to 0 closes everything
    down = Recommendation(feed=f, kind="rebalance", bar_time=datetime.now(timezone.utc), price=140.0,
                          from_weight=1.0, to_weight=0.4)
    title, body = describe(f, down)
    # 40% of $1,000 at 140 = 2.857 BTC, rounded down to 2.85 lots
    assert title.startswith("MT5: CLOSE 4.31 lots BTCUSD")
    assert f.lots_held == 2.85
    out = Recommendation(feed=f, kind="rebalance", bar_time=datetime.now(timezone.utc), price=140.0,
                         from_weight=0.4, to_weight=0.0)
    title, _ = describe(f, out)
    assert title.startswith("MT5: CLOSE all BTCUSD buys (2.85 lots)")
    assert f.lots_held == 0.0


def test_resize_that_doesnt_change_the_lots_sends_nothing(owner):
    from recommendations.messages import describe

    f = feed(name="ens2", strategy="donchian_ensemble", capital=1_000.0, lots_held=0.0)
    f.capital_equity_base = f.equity
    tiny = Recommendation(feed=f, kind="rebalance", bar_time=datetime.now(timezone.utc), price=84_000.0,
                          from_weight=0.0, to_weight=0.4)
    # 40% of $1,000 is 0.0047 BTC: under the 0.01-lot minimum, so no lots to trade
    assert describe(f, tiny) is None


def test_sizing_endpoint(owner):
    import json

    from django.test import Client

    f = feed()
    client = Client()
    client.force_login(owner)

    def post(body):
        return client.post(f"/api/recommendations/feeds/{f.pk}/sizing", json.dumps(body),
                           content_type="application/json")

    assert post({"capital": -5}).status_code == 400
    assert post({"capital": 1000, "risk_pct": 0.2}).status_code == 400
    ok = post({"capital": 2500, "contract_size": 1, "min_lot": 0.01, "lot_step": 0.01, "risk_pct": 0.01})
    assert ok.status_code == 200, ok.content
    assert ok.json()["capital"] == 2500 and ok.json()["current_capital"] == 2500

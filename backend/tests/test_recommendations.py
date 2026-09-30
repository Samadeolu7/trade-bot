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
    assert AlertEvent.objects.get().title.startswith("MT5: long BTCUSD")

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

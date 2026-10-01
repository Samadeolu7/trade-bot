import json
from datetime import datetime, timedelta, timezone

import pandas as pd
from django.test import Client

from alerts.models import AlertEvent, AlertRule
from market import health
from market.health import check_series
from recommendations.models import Feed, Recommendation, SignalSwitch
from recommendations.runner import run_feed
from tests.conftest import make_candles
from tests.test_engine import engine, running_bot

FUNDING = {}
NOW = datetime(2026, 3, 1, 1, 0, tzinfo=timezone.utc)


def frame(closes, timeframe="4h", end=NOW):
    step = pd.Timedelta(timeframe)
    index = pd.date_range(end=pd.Timestamp(end) - step, periods=len(closes), freq=step)
    c = pd.Series(closes, index=index, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1.0})


def fresh(monkeypatch):
    monkeypatch.setattr(health, "STALE_PERIODS", 2)


def test_clean_series_passes(monkeypatch):
    fresh(monkeypatch)
    report = check_series(frame([100.0] * 200), "4h", now=NOW)
    assert report.ok and report.warnings == []


def test_stale_data_blocks(monkeypatch):
    fresh(monkeypatch)
    report = check_series(frame([100.0] * 200, end=NOW - timedelta(hours=12)), "4h", now=NOW)
    assert not report.ok and "stale data" in report.summary()
    # one bar late is still fine (the exchange can lag a few minutes)
    assert check_series(frame([100.0] * 200, end=NOW - timedelta(hours=4)), "4h", now=NOW).ok


def test_gaps_duplicates_and_bad_candles_block(monkeypatch):
    fresh(monkeypatch)
    df = frame([100.0] * 200)
    assert "3 missing" in check_series(df.drop(df.index[-10:-7]), "4h", now=NOW).summary()
    assert "duplicate" in check_series(pd.concat([df, df.iloc[[-1]]]).sort_index(), "4h", now=NOW).summary()
    bad = df.copy()
    bad.iloc[-5, bad.columns.get_loc("close")] = float("nan")
    assert "non-numeric" in check_series(bad, "4h", now=NOW).summary()
    bad = df.copy()
    bad.iloc[-5, bad.columns.get_loc("high")] = 90.0
    assert "high/low" in check_series(bad, "4h", now=NOW).summary()
    # old gaps outside the recent window don't matter
    assert check_series(df.drop(df.index[5:8]), "4h", now=NOW).ok


def test_big_move_is_a_warning_not_a_block(monkeypatch):
    fresh(monkeypatch)
    report = check_series(frame([100.0] * 199 + [60.0]), "4h", now=NOW)
    assert report.ok and "40% single-bar move" in report.warnings[0]


def feed(**kw):
    defaults = dict(name="donchian_1d", strategy="donchian", timeframe="1d",
                    params={"donchian.channel_period": 5, "donchian.exit_channel_period": 5})
    return Feed.objects.create(**{**defaults, **kw})


def test_bad_data_pauses_the_feed_until_a_person_resumes_it(owner, monkeypatch):
    AlertRule.objects.create(user=owner, kind="recommendation")
    make_candles([100.0] * 70 + [110.0])
    f = feed()
    fresh(monkeypatch)  # the 2024 test candles are now stale
    events = run_feed(f, "binance", FUNDING)
    assert [e.kind for e in events] == [Recommendation.Kind.SUPPRESSED]
    f.refresh_from_db()
    assert f.halted and "stale data" in f.halt_reason and f.direction == ""
    alert = AlertEvent.objects.get()
    assert alert.title.startswith("SIGNALS PAUSED: donchian_1d") and "press Resume" in alert.body

    # the data recovers, but a paused feed stays paused: no calls, no more alerts
    monkeypatch.setattr(health, "STALE_PERIODS", 10**6)
    assert run_feed(f, "binance", FUNDING) == []
    assert AlertEvent.objects.count() == 1 and f.status_reason.startswith("paused:")

    client = Client()
    client.force_login(owner)
    url = f"/api/recommendations/feeds/{f.pk}/resume"
    assert client.post(url, json.dumps({"acknowledged": False}), content_type="application/json").status_code == 400
    ok = client.post(url, json.dumps({"acknowledged": True, "note": "exchange was down"}),
                     content_type="application/json")
    assert ok.status_code == 200 and ok.json()["halted"] is False
    events = run_feed(Feed.objects.get(pk=f.pk), "binance", FUNDING)
    assert [e.kind for e in events] == [Recommendation.Kind.ENTRY]
    kinds = list(Recommendation.objects.order_by("id").values_list("kind", flat=True))
    assert kinds == ["suppressed", "resumed", "entry"]


def test_owner_can_pause_one_feed_or_all_of_them(owner):
    make_candles([100.0] * 70 + [110.0])
    f = feed()
    client = Client()
    client.force_login(owner)

    def post(url, body):
        return client.post(url, json.dumps(body), content_type="application/json")

    assert post(f"/api/recommendations/feeds/{f.pk}/halt", {"reason": " "}).status_code == 400
    r = post(f"/api/recommendations/feeds/{f.pk}/halt", {"reason": "news event"})
    assert r.status_code == 200 and r.json()["halted"] and "news event" in r.json()["halt_reason"]
    assert run_feed(Feed.objects.get(pk=f.pk), "binance", FUNDING) == []
    post(f"/api/recommendations/feeds/{f.pk}/resume", {"acknowledged": True})

    assert post("/api/recommendations/switch", {"halted": True}).status_code == 400  # needs a reason
    assert post("/api/recommendations/switch", {"halted": True, "reason": "CPI day"}).json()["halted"]
    f.refresh_from_db()
    assert run_feed(f, "binance", FUNDING) == []
    assert f.status_reason == "paused for all feeds: CPI day"
    post("/api/recommendations/switch", {"halted": False})
    assert not SignalSwitch.get().halted
    assert [e.kind for e in run_feed(f, "binance", FUNDING)] == [Recommendation.Kind.ENTRY]


def test_bot_makes_no_decision_from_bad_data(account, owner, quotes, monkeypatch):
    AlertRule.objects.create(user=owner, kind="bot_error")
    make_candles([100.0] * 70 + [110.0])
    quotes.set("BTC/USDT", 110, 111)
    bot = running_bot(account, owner, name="breakout", strategy="donchian", timeframe="1d", allocation=5_000,
                      params={"donchian.channel_period": 5, "donchian.exit_channel_period": 5})
    fresh(monkeypatch)
    decision = engine().run_bot(bot)
    assert decision.action == "blocked" and "stale data" in decision.reason and decision.order is None
    engine().run_bot(bot)  # same problem: no second alert
    assert AlertEvent.objects.filter(title__startswith="SIGNALS PAUSED").count() == 1

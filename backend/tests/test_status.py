from datetime import timedelta

from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from core import status
from recommendations.models import Feed
from trading.engine.loop import HEARTBEAT_KEY


def beat():
    cache.set(HEARTBEAT_KEY, {"at": timezone.now().isoformat()})
    status.worker_heartbeat()


def test_status_is_public_and_shows_health_only(owner):
    beat()
    now = timezone.now()
    Feed.objects.create(name="ok_feed", strategy="donchian", timeframe="4h",
                        last_bar_at=now - timedelta(hours=8), last_run_at=now, weight=0.41, capital=5000)
    Feed.objects.create(name="paused_feed", strategy="donchian", timeframe="4h", halted=True,
                        halt_reason="data check failed: stale data")
    r = Client().get("/api/public/status")  # no login
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["engine"]["healthy"] and body["worker"]["healthy"]
    assert {f["name"]: f["state"] for f in body["feeds"]} == {"ok_feed": "ok", "paused_feed": "paused"}
    assert body["overall"] == "degraded" and "paused_feed: paused" in body["problems"]
    text = r.content.decode()
    assert "0.41" not in text and "5000" not in text and "stale data" not in text  # no positions, money or reasons


def test_status_down_without_engine_and_stale_feeds():
    now = timezone.now()
    feed = Feed(name="f", strategy="donchian", timeframe="4h", last_bar_at=now - timedelta(hours=20))
    assert status._feed_state(feed, now) == "stale"
    feed.last_bar_at = now - timedelta(hours=8)
    assert status._feed_state(feed, now) == "ok"
    assert status.build_status()["overall"] == "down"


def test_status_is_rate_limited(monkeypatch):
    monkeypatch.setattr(status, "RATE_LIMIT", 3)
    client = Client()
    codes = [client.get("/api/public/status").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]

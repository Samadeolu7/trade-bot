from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from alerts.models import AlertEvent, AlertRule
from alerts.service import PriceHistory, add_default_rules, daily_summaries, evaluate_prices, notify
from trading.models import AccountGrant, TradingAccount

KEY = ("quidax_spot", "BTC/USDT")
T0 = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def price_rule(owner, kind, **params):
    return AlertRule.objects.create(user=owner, kind=kind, params={"venue": KEY[0], "symbol": KEY[1], **params},
                                    once=kind != "price_move")


def test_price_above_fires_on_the_cross_only_once(owner):
    rule = price_rule(owner, "price_above", price=100)
    history = PriceHistory()
    assert evaluate_prices({KEY: D(99)}, history, T0) == 0  # first sighting only records the price
    assert evaluate_prices({KEY: D(101)}, history, T0) == 1
    rule.refresh_from_db()
    assert rule.enabled is False  # "once" alerts switch themselves off
    assert AlertEvent.objects.get().title.startswith("BTC/USDT on quidax spot crossed ▲ above 100.00")


def test_price_below_ignores_prices_already_below(owner):
    price_rule(owner, "price_below", price=100)
    history = PriceHistory()
    evaluate_prices({KEY: D(90)}, history, T0)
    assert evaluate_prices({KEY: D(80)}, history, T0) == 0


def test_sudden_move_needs_a_full_window_and_respects_cooldown(owner):
    rule = price_rule(owner, "price_move", pct=3, minutes=60)
    rule.cooldown_minutes = 60
    rule.save()
    history = PriceHistory()
    history.add(*KEY, T0, D(100))
    # 10 minutes later: not enough history to judge a 60-minute move
    assert evaluate_prices({KEY: D(105)}, history, T0 + timedelta(minutes=10)) == 0
    history.add(*KEY, T0 + timedelta(minutes=58), D(104))
    assert evaluate_prices({KEY: D(104)}, history, T0 + timedelta(minutes=58)) == 1
    assert "up 4.00% in 60 minutes" in AlertEvent.objects.get().title
    # still moving, but inside the cooldown
    rule.refresh_from_db()
    rule.last_fired_at = T0 + timedelta(minutes=58)
    rule.save()
    assert evaluate_prices({KEY: D(106)}, history, T0 + timedelta(minutes=59)) == 0


def test_event_alerts_respect_account_access(account, django_user_model):
    viewer = django_user_model.objects.create_user("vera", password="x" * 12, role="viewer")
    AlertRule.objects.create(user=viewer, kind="bot_trade")
    other = TradingAccount.objects.create(name="Other")
    assert notify("bot_trade", "b bought", account=other) == 0
    AccountGrant.objects.create(user=viewer, account=other, role="viewer")
    assert notify("bot_trade", "b bought", account=other) == 1


def test_rule_scoped_to_one_account_ignores_others(account, owner):
    AlertRule.objects.create(user=owner, kind="stop_hit", account=account)
    other = TradingAccount.objects.create(name="Other")
    assert notify("stop_hit", "stop", account=other) == 0
    assert notify("stop_hit", "stop", account=account) == 1


def test_daily_summary_once_per_day_at_its_hour(account, owner):
    AlertRule.objects.create(user=owner, kind="daily_summary", params={"hour": 7})
    assert daily_summaries(datetime(2026, 9, 1, 6, tzinfo=timezone.utc)) == 0
    assert daily_summaries(datetime(2026, 9, 1, 7, tzinfo=timezone.utc)) == 1
    assert daily_summaries(datetime(2026, 9, 1, 7, 30, tzinfo=timezone.utc)) == 0
    assert "Paper (paper): 10,000.00 USDT" in AlertEvent.objects.get().body


def test_undelivered_alerts_are_still_logged(owner, settings):
    settings.TELEGRAM_BOT_TOKEN = ""
    AlertRule.objects.create(user=owner, kind="research_done")
    notify("research_done", "Report ready")
    event = AlertEvent.objects.get()
    assert event.delivered is False


def test_default_rules_are_added_once(owner):
    add_default_rules(owner)
    add_default_rules(owner)
    assert AlertRule.objects.filter(user=owner).count() == 9


def test_engine_bot_trade_raises_alert(account, owner, quotes):
    from tests.conftest import make_candles
    from trading.engine.loop import Engine
    from trading.services.bots import create_bot, set_bot_status

    AlertRule.objects.create(user=owner, kind="bot_trade")
    make_candles([100.0] * 70 + [110.0])
    bot = create_bot(account, name="breakout", strategy="donchian", timeframe="1d", allocation=5_000, user=owner,
                     params={"donchian.channel_period": 5})
    set_bot_status(bot, "running")
    Engine().run_bot(bot)
    assert AlertEvent.objects.get().title.startswith("breakout bought")


def test_alerts_api_defaults_and_toggle(owner):
    import json

    from django.test import Client

    client = Client()
    client.post("/api/auth/login", json.dumps({"username": "owner", "password": "correct-horse-battery"}),
                content_type="application/json")
    rules = client.post("/api/alerts/defaults").json()
    assert len(rules) == 9
    rule = rules[0]
    response = client.put(f"/api/alerts/rules/{rule['id']}", json.dumps({**rule, "enabled": False}),
                          content_type="application/json")
    assert response.status_code == 200 and response.json()["enabled"] is False
    assert client.post("/api/alerts/rules", json.dumps({"kind": "price_above", "params": {}}),
                       content_type="application/json").status_code == 400


def test_alerts_stay_until_dismissed(owner):
    import json

    from django.test import Client

    AlertRule.objects.create(user=owner, kind="research_done")
    notify("research_done", "Report one")
    notify("research_done", "Report two")
    client = Client()
    client.post("/api/auth/login", json.dumps({"username": "owner", "password": "correct-horse-battery"}),
                content_type="application/json")
    active = client.get("/api/alerts/events?active=true").json()
    assert len(active) == 2
    client.post(f"/api/alerts/events/{active[0]['id']}/dismiss")
    assert len(client.get("/api/alerts/events?active=true").json()) == 1
    client.post("/api/alerts/events/dismiss-all")
    assert client.get("/api/alerts/events?active=true").json() == []
    assert len(client.get("/api/alerts/events").json()) == 2  # still in the log


def test_repeating_price_alert_fires_again_after_cooldown(owner):
    rule = AlertRule.objects.create(user=owner, kind="price_above", cooldown_minutes=15,
                                    params={"venue": KEY[0], "symbol": KEY[1], "price": 100})
    history = PriceHistory()
    evaluate_prices({KEY: D(99)}, history, T0)
    assert evaluate_prices({KEY: D(101)}, history, T0) == 1
    evaluate_prices({KEY: D(99)}, history, T0 + timedelta(minutes=5))
    # crossing again inside the cooldown: stays quiet, rule stays on
    assert evaluate_prices({KEY: D(101)}, history, T0 + timedelta(minutes=6)) == 0
    evaluate_prices({KEY: D(99)}, history, T0 + timedelta(minutes=20))
    assert evaluate_prices({KEY: D(101)}, history, T0 + timedelta(minutes=21)) == 1
    rule.refresh_from_db()
    assert rule.enabled is True

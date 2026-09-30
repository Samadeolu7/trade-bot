import json

import pytest
from django.test import Client
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from trading.models import AccountGrant, TradingAccount


def post(client, url, data):
    return client.post(url, json.dumps(data), content_type="application/json")


def login(client, username, password="correct-horse-battery", otp=None):
    return post(client, "/api/auth/login", {"username": username, "password": password, "otp": otp})


@pytest.fixture
def make_user(django_user_model):
    def make(username, role, grant_role=None, account=None):
        user = django_user_model.objects.create_user(username, password="correct-horse-battery", role=role)
        if grant_role:
            AccountGrant.objects.create(user=user, account=account, role=grant_role)
        return user

    return make


def order_payload(account):
    return {"account_id": account.pk, "side": "buy", "quantity": "0.01"}


def test_everything_needs_login(account):
    assert Client().get("/api/accounts").status_code == 401


def test_login_and_me(owner):
    client = Client()
    assert login(client, "owner", "wrong-password").status_code == 401
    response = login(client, "owner")
    assert response.status_code == 200
    assert response.json()["role"] == "owner"
    assert client.get("/api/auth/me").json()["username"] == "owner"


def test_repeated_failures_are_throttled(owner):
    client = Client()
    for _ in range(10):
        login(client, "owner", "wrong-password")
    assert login(client, "owner").status_code == 429


def test_viewer_sees_granted_account_but_cannot_trade(account, make_user):
    make_user("vera", "viewer", "viewer", account)
    client = Client()
    login(client, "vera")
    assert [a["id"] for a in client.get("/api/accounts").json()] == [account.pk]
    assert client.get(f"/api/accounts/{account.pk}").json()["can_trade"] is False
    assert post(client, "/api/orders", order_payload(account)).status_code == 403


def test_user_without_grant_cannot_see_account(account, make_user):
    make_user("nobody", "trader")
    client = Client()
    login(client, "nobody")
    assert client.get("/api/accounts").json() == []
    assert client.get(f"/api/accounts/{account.pk}").status_code == 403
    assert post(client, "/api/orders", order_payload(account)).status_code == 403


def test_trader_with_trader_grant_can_trade(account, make_user):
    make_user("tom", "trader", "trader", account)
    client = Client()
    login(client, "tom")
    response = post(client, "/api/orders", order_payload(account))
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "filled"
    assert response.json()["placed_by"] == "tom"


def test_trader_role_with_only_a_viewer_grant_cannot_trade(account, make_user):
    make_user("tina", "trader", "viewer", account)
    client = Client()
    login(client, "tina")
    assert post(client, "/api/orders", order_payload(account)).status_code == 403


def test_only_owner_manages_users_and_accounts(account, make_user):
    make_user("tom", "trader", "trader", account)
    client = Client()
    login(client, "tom")
    assert client.get("/api/users").status_code == 403
    assert post(client, "/api/accounts", {"name": "Mine"}).status_code == 403


def test_live_accounts_cannot_be_created_yet(owner, quotes):
    client = Client()
    login(client, "owner")
    response = post(client, "/api/accounts", {"name": "Real", "mode": "live"})
    assert response.status_code == 400


def test_2fa_is_required_to_trade_when_enforced(account, owner, settings):
    settings.REQUIRE_2FA = True
    client = Client()
    login(client, "owner")
    assert client.get("/api/auth/me").json()["needs_2fa_setup"] is True
    assert post(client, "/api/orders", order_payload(account)).status_code == 403

    post(client, "/api/auth/2fa/setup", {})
    device = TOTPDevice.objects.get(user=owner)
    response = post(client, "/api/auth/2fa/confirm", {"token": f"{totp(device.bin_key):06d}"})
    assert response.status_code == 200
    assert response.json()["two_factor_verified"] is True
    assert post(client, "/api/orders", order_payload(account)).status_code == 200


def test_login_asks_for_otp_once_enabled(owner):
    device = TOTPDevice.objects.create(user=owner, name="a", confirmed=True)
    client = Client()
    response = login(client, "owner")
    assert response.status_code == 401 and "otp_required" in response.content.decode()
    assert login(client, "owner", otp="000000").status_code == 401
    # django-otp throttles after a wrong code; a real user just waits a second
    device.refresh_from_db()
    device.throttle_reset()
    assert login(client, "owner", otp=f"{totp(device.bin_key):06d}").status_code == 200


def test_bot_positions_are_not_hand_protected(account, owner):
    from trading.services.bots import create_bot
    from trading.services.orders import submit_order

    bot = create_bot(account, name="b", strategy="donchian", allocation=1_000, user=owner)
    submit_order(account, book=bot.book, bot=bot, symbol="BTC/USDT", side="buy", quantity="1", source="bot")
    position = bot.positions.get()
    client = Client()
    login(client, "owner")
    response = client.put(f"/api/positions/{position.pk}/protection", json.dumps({"stop_price": "50"}),
                          content_type="application/json")
    assert response.status_code == 400


def test_create_bot_and_read_it_back(account, owner):
    client = Client()
    login(client, "owner")
    response = post(client, "/api/bots", {
        "account_id": account.pk, "name": "ens", "strategy": "donchian_ensemble", "allocation": "2000",
        "params": {"donchian_ensemble.bars_per_day": 6},
    })
    assert response.status_code == 200, response.content
    bot = response.json()
    assert bot["status"] == "running" and bot["equity"] == 2000.0
    assert TradingAccount.objects.get().bots.count() == 1
    bad = post(client, "/api/bots", {"account_id": account.pk, "name": "x", "strategy": "donchian",
                                     "allocation": "10", "params": {"rsi_bb.bb_std": 3}})
    assert bad.status_code == 400


def test_30_day_change_ignores_deposits(account, owner, quotes):
    from decimal import Decimal

    from django.utils import timezone

    from trading.models import EquitySnapshot
    from trading.services.orders import fund_paper_account

    EquitySnapshot.objects.create(account=account, book="", time=timezone.now(),
                                  equity=Decimal(10_000))
    fund_paper_account(account, 60_000)
    client = Client()
    login(client, "owner")
    data = client.get(f"/api/accounts/{account.pk}").json()
    assert data["equity"] == 70_000.0
    assert data["change_30d"] == 0.0

import json
import sqlite3
from decimal import Decimal as D

from django.core.management import call_command

from tests.test_orders import assert_books_match_venue
from trading.models import Bot, BotDecision, Position, TradingAccount
from trading.services import books

ENTRY_MS = 1_790_000_000_000


def shadow_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE paper_position (exchange TEXT, symbol TEXT, timeframe TEXT, strategy_label TEXT, "
        "direction TEXT, entry_price REAL, stop_loss REAL, take_profit REAL, entry_time INTEGER, "
        "entry_fee REAL, size REAL, context TEXT)"
    )
    conn.execute("CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT)")
    rows = [
        ("donchian_4h", "4h", "long", 80_000.0, 76_000.0),
        ("donchian_adx_control", "1d", "short", 90_000.0, 95_000.0),
    ]
    for label, tf, direction, entry, stop in rows:
        conn.execute(
            "INSERT INTO paper_position VALUES ('binance','BTC/USDT',?,?,?,?,?,NULL,?,0,1,NULL)",
            (tf, label, direction, entry, stop, ENTRY_MS),
        )
    conn.execute(
        "INSERT INTO bot_state VALUES (?, ?)",
        ("donchian_ensemble_4h:exposure_state",
         json.dumps({"equity": 10_300, "held": 0.402, "last_bar_ms": ENTRY_MS + 14_400_000, "last_close": 84_358.01})),
    )
    conn.commit()
    conn.close()


def test_shadow_runs_continue_from_their_positions(tmp_path, owner, quotes):
    path = tmp_path / "trades.db"
    shadow_db(path)
    call_command("seed_shadow_bots", str(path), owner="owner")

    account = TradingAccount.objects.get(name="Quidax paper")
    assert Bot.objects.filter(status="running").count() == 6

    # long carried at its own entry, sized to 1% risk of 10,000 over the 4,000 stop distance
    long_bot = Bot.objects.get(name="donchian_4h")
    position = Position.objects.get(book=long_bot.book)
    assert position.quantity == D("0.025")
    assert position.average_price == D(80_000)
    assert position.stop_price == D(76_000)
    assert position.opened_at.timestamp() * 1000 == ENTRY_MS

    # a short can't live on spot: flat, with the reason recorded
    short_bot = Bot.objects.get(name="donchian_adx_control")
    assert not Position.objects.filter(book=short_bot.book).exclude(quantity=0).exists()
    assert "long-only" in BotDecision.objects.get(bot=short_bot).reason

    # the ensemble holds the same fraction of capital and resumes after its last bar
    ensemble = Bot.objects.get(name="donchian_ensemble_4h")
    held = Position.objects.get(book=ensemble.book).quantity * D("84358.01")
    assert abs(held / D(10_000) - D("0.402")) < D("0.001")
    assert ensemble.last_bar_at.timestamp() * 1000 == ENTRY_MS + 14_400_000

    # bots with nothing open start flat with their full allocation
    flat = Bot.objects.get(name="donchian_natr_regime")
    assert books.book_balances(account, flat.book) == {"USDT": D(10_000)}
    assert books.book_balances(account, "manual") == {"USDT": D(10_000)}
    assert_books_match_venue(account)

    # running it again changes nothing
    call_command("seed_shadow_bots", str(path), owner="owner")
    assert Bot.objects.count() == 6
    assert_books_match_venue(account)

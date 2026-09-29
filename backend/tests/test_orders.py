from decimal import Decimal as D

import pytest
from django.db.models import Sum

from trading.models import MANUAL_BOOK, LedgerEntry, Order, PaperBalance, Position, TradingAccount
from trading.services import books
from trading.services.bots import create_bot
from trading.services.orders import OrderError, cancel_order, fund_paper_account, submit_order, sync_open_orders


def assert_books_match_venue(account):
    """The books of a paper account always add up to the paper venue."""
    venue = {b.asset: b.amount for b in PaperBalance.objects.filter(account=account)}
    ledger = {
        r["asset"]: r["total"]
        for r in LedgerEntry.objects.filter(account=account).values("asset").annotate(total=Sum("amount"))
    }
    for asset in set(venue) | set(ledger):
        assert venue.get(asset, D(0)) == ledger.get(asset, D(0)), asset


def buy(account, qty, book=MANUAL_BOOK, **kw):
    return submit_order(account, book=book, symbol="BTC/USDT", side="buy", quantity=qty, source="manual", **kw)


def sell(account, qty, book=MANUAL_BOOK, **kw):
    return submit_order(account, book=book, symbol="BTC/USDT", side="sell", quantity=qty, source="manual", **kw)


def test_manual_market_buy_is_booked(account, owner):
    order = buy(account, "2", user=owner)
    assert order.status == "filled"
    assert order.average_price == D(101)
    assert order.fees == D("0.202")
    position = Position.objects.get(account=account, book=MANUAL_BOOK)
    assert position.quantity == D(2)
    assert position.average_price == D(101)
    assert books.book_balances(account, MANUAL_BOOK)["USDT"] == D(10_000) - D(202) - D("0.202")
    assert_books_match_venue(account)


def test_round_trip_realizes_pnl_and_clears_protection(account, quotes):
    buy(account, "1", stop_price=90, take_profit=120)
    position = Position.objects.get(account=account, book=MANUAL_BOOK)
    assert position.stop_price == D(90) and position.take_profit == D(120)
    quotes.set("BTC/USDT", 110, 111)
    sell(account, "1")
    position.refresh_from_db()
    assert position.quantity == 0
    assert position.realized_pnl == D(9)  # sold at the 110 bid, bought at the 101 ask
    assert position.stop_price is None and position.take_profit is None
    assert_books_match_venue(account)


def test_rejections_are_recorded_not_dropped(account):
    order = sell(account, "1")
    assert order.status == "rejected"
    assert "can't sell" in order.reject_reason
    assert Order.objects.filter(status="rejected").count() == 1
    assert_books_match_venue(account)


def test_manual_book_cannot_spend_a_bots_allocation(account, owner):
    create_bot(account, name="b1", strategy="donchian", allocation=9_000, user=owner)
    order = buy(account, "20")
    assert order.status == "rejected"
    assert "not enough USDT in this book" in order.reject_reason


def test_bot_cannot_sell_what_the_manual_book_holds(account, owner):
    bot = create_bot(account, name="b1", strategy="donchian", allocation=1_000, user=owner)
    buy(account, "5")
    order = submit_order(account, book=bot.book, bot=bot, symbol="BTC/USDT", side="sell", quantity="1", source="bot")
    assert order.status == "rejected"


def test_allocation_cannot_exceed_manual_cash(account, owner):
    with pytest.raises(OrderError):
        create_bot(account, name="b1", strategy="donchian", allocation=20_000, user=owner)


def test_resting_limit_fills_on_sync_and_attaches_stop(account, quotes):
    order = buy(account, "1", order_type="limit", limit_price=95, stop_price=90)
    assert order.status == "open"
    assert sync_open_orders(account) == 0
    quotes.set("BTC/USDT", 94, 95)
    assert sync_open_orders(account) == 1
    order.refresh_from_db()
    assert order.status == "filled" and order.average_price == D(95)
    position = Position.objects.get(account=account, book=MANUAL_BOOK)
    assert position.quantity == 1 and position.stop_price == D(90)
    assert_books_match_venue(account)


def test_book_reservation_blocks_overspending_with_resting_orders(account):
    assert buy(account, "100", order_type="limit", limit_price=95).status == "open"
    assert buy(account, "10", order_type="limit", limit_price=95).status == "rejected"


def test_cancel(account):
    order = buy(account, "1", order_type="limit", limit_price=95)
    cancel_order(order)
    order.refresh_from_db()
    assert order.status == "cancelled"


def test_halted_account_allows_exits_only(account):
    buy(account, "1")
    account.halted = True
    account.save()
    with pytest.raises(OrderError):
        buy(account, "1")
    assert sell(account, "1").status == "filled"


def test_cfd_short_and_financing(quotes):
    from trading.engine.loop import Engine

    acct = TradingAccount.objects.create(name="CFD", venue="exness_cfd")
    fund_paper_account(acct, D(1_000))
    assert sell(acct, "5").status == "filled"
    assert Position.objects.get(account=acct).quantity == D(-5)
    Engine.__new__(Engine).charge_financing()
    assert LedgerEntry.objects.filter(account=acct, kind="financing").count() == 1
    assert_books_match_venue(acct)

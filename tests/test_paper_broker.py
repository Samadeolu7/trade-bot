"""Behaviour every Broker must share, run here against PaperBroker. The
Quidax connector (Phase 4) gets the same assertions against recorded
responses, which is what lets an account move from paper to live without
anything above the broker noticing."""

from decimal import Decimal as D

import pytest

from bot.broker.base import Depth, OrderRequest
from bot.broker.paper import InMemoryPaperStore, PaperBroker
from bot.broker.quotes import StaticQuoteSource
from bot.broker.venues import VENUES

SYMBOL = "BTC/USDT"


def make_broker(venue="quidax_spot", balances=None, bid=100, ask=101, depth=None):
    quotes = StaticQuoteSource(depths={SYMBOL: depth} if depth else None)
    quotes.set(SYMBOL, bid, ask)
    store = InMemoryPaperStore(balances if balances is not None else {"USDT": 10_000})
    return PaperBroker(VENUES[venue], store, quotes), quotes


def order(side, qty, order_type="market", limit=None, client_id="c1"):
    return OrderRequest(client_id, SYMBOL, side, order_type, D(str(qty)),
                        D(str(limit)) if limit is not None else None)


def test_market_buy_fills_at_ask_and_charges_taker_fee():
    broker, _ = make_broker()
    result = broker.place_order(order("buy", "1"))
    assert result.status == "filled"
    assert result.fills[0].price == D(101)
    assert result.fills[0].fee == D("0.101")
    balances = broker.get_balances()
    assert balances["BTC"] == D(1)
    assert balances["USDT"] == D(10_000) - D(101) - D("0.101")


def test_market_order_walks_the_book():
    depth = Depth(bids=[(D(100), D(1))], asks=[(D(101), D("0.5")), (D(102), D(1))])
    broker, _ = make_broker(depth=depth)
    result = broker.place_order(order("buy", "1"))
    assert result.status == "filled"
    assert [(f.price, f.quantity) for f in result.fills] == [(D(101), D("0.5")), (D(102), D("0.5"))]


def test_market_order_remainder_is_cancelled_when_book_runs_out():
    depth = Depth(bids=[(D(100), D(1))], asks=[(D(101), D("0.3"))])
    broker, _ = make_broker(depth=depth)
    result = broker.place_order(order("buy", "1"))
    assert result.status == "cancelled"
    assert result.filled_quantity == D("0.3")


def test_spot_rejects_selling_more_than_held():
    broker, _ = make_broker()
    result = broker.place_order(order("sell", "1"))
    assert result.status == "rejected"
    assert "long-only" in result.reject_reason
    assert broker.get_balances()["USDT"] == D(10_000)


def test_rejects_insufficient_funds_without_touching_balances():
    broker, _ = make_broker(balances={"USDT": 50})
    result = broker.place_order(order("buy", "1"))
    assert result.status == "rejected"
    assert "insufficient" in result.reject_reason
    assert broker.get_balances() == {"USDT": D(50)}


def test_rejects_below_minimum_and_off_step_sizes():
    broker, _ = make_broker()
    assert broker.place_order(order("buy", "0.000001")).status == "rejected"
    assert broker.place_order(order("buy", "0.0000105")).status == "rejected"


def test_resting_limit_fills_as_maker_when_price_touches():
    broker, quotes = make_broker()
    result = broker.place_order(order("buy", "1", "limit", 95))
    assert result.status == "open"
    assert broker.poll() == []

    quotes.set(SYMBOL, 94, 95)
    changed = broker.poll()
    assert len(changed) == 1
    _, filled = changed[0]
    assert filled.status == "filled"
    assert filled.fills[0].price == D(95)
    assert filled.fills[0].liquidity == "maker"


def test_resting_limit_reserves_funds():
    broker, _ = make_broker(balances={"USDT": 150})
    assert broker.place_order(order("buy", "1", "limit", 95)).status == "open"
    # 95 is reserved, so a second order for another 95 can't be funded
    assert broker.place_order(order("buy", "1", "limit", 95, "c2")).status == "rejected"


def test_cancel_releases_reservation():
    broker, _ = make_broker(balances={"USDT": 150})
    first = broker.place_order(order("buy", "1", "limit", 95))
    assert broker.cancel_order(first.venue_order_id).status == "cancelled"
    assert broker.place_order(order("buy", "1", "limit", 95, "c2")).status == "open"


def test_marketable_limit_only_takes_levels_within_limit():
    depth = Depth(bids=[(D(100), D(1))], asks=[(D(101), D("0.5")), (D(103), D(1))])
    broker, _ = make_broker(depth=depth)
    result = broker.place_order(order("buy", "1", "limit", 102))
    assert result.status == "open"
    assert result.filled_quantity == D("0.5")


def test_cfd_allows_short_within_margin():
    broker, _ = make_broker("exness_cfd", balances={"USDT": 1_000})
    result = broker.place_order(order("sell", "10"))  # 1000 notional, 2x leverage
    assert result.status == "filled"
    assert broker.get_balances()["BTC"] == D(-10)


def test_cfd_rejects_beyond_leverage():
    broker, _ = make_broker("exness_cfd", balances={"USDT": 1_000})
    result = broker.place_order(order("buy", "25"))  # ~2500 notional > 2x of 1000
    assert result.status == "rejected"
    assert "margin" in result.reject_reason


def test_cfd_financing_charges_open_position():
    broker, _ = make_broker("exness_cfd", balances={"USDT": 1_000})
    broker.place_order(order("buy", "5"))
    before = broker.get_balances()["USDT"]
    charged = broker.charge_financing(SYMBOL)
    assert charged > 0
    assert broker.get_balances()["USDT"] == before - charged


@pytest.mark.parametrize("venue", list(VENUES))
def test_round_trip_costs_money(venue):
    broker, _ = make_broker(venue, balances={"USDT": 10_000})
    step = D("0.1")
    broker.place_order(order("buy", step))
    broker.place_order(order("sell", step, client_id="c2"))
    balances = broker.get_balances()
    assert balances.get("BTC", D(0)) == 0
    assert balances["USDT"] < D(10_000)

from decimal import Decimal as D

from tests.conftest import make_candles
from trading.engine.loop import Engine
from trading.models import MANUAL_BOOK, Bot, BotDecision, Order, Position
from trading.services.bots import create_bot, set_bot_status, stop_bot
from trading.services.orders import submit_order


def engine() -> Engine:
    e = Engine()
    e.poll_bars = lambda: None  # no network: candles come from the test
    return e


def running_bot(account, owner, **kw):
    bot = create_bot(account, user=owner, **kw)
    set_bot_status(bot, Bot.Status.RUNNING)
    return bot


def test_stop_is_triggered_by_the_engine(account, quotes):
    submit_order(account, book=MANUAL_BOOK, symbol="BTC/USDT", side="buy", quantity="1", source="manual",
                 stop_price=95)
    engine().check_protections()
    assert Position.objects.get(account=account).quantity == 1  # bid 100 > stop 95

    quotes.set("BTC/USDT", 94, 95)
    engine().check_protections()
    position = Position.objects.get(account=account)
    assert position.quantity == 0
    stop_order = Order.objects.get(source=Order.Source.STOP)
    assert stop_order.status == "filled" and stop_order.average_price == D(94)


def test_take_profit_is_triggered_by_the_engine(account, quotes):
    submit_order(account, book=MANUAL_BOOK, symbol="BTC/USDT", side="buy", quantity="1", source="manual",
                 take_profit=110)
    quotes.set("BTC/USDT", 111, 112)
    engine().check_protections()
    assert Order.objects.filter(source=Order.Source.TAKE_PROFIT, status="filled").count() == 1


def test_signal_bot_enters_with_engine_held_stop(account, owner, quotes):
    make_candles([100.0] * 70 + [110.0])
    quotes.set("BTC/USDT", 110, 111)
    bot = running_bot(account, owner, name="breakout", strategy="donchian", timeframe="1d", allocation=5_000,
                      params={"donchian.channel_period": 5, "donchian.exit_channel_period": 5})

    decision = engine().run_bot(bot)
    assert decision.action == BotDecision.Action.ENTER
    position = Position.objects.get(book=bot.book)
    assert position.quantity > 0
    assert position.stop_price == D("99.5")  # the 5-bar exit channel low
    # 1% of 5,000 equity risked over the 10.5 stop distance from the signal price, as the backtest sizes it
    assert abs(position.quantity * (D(110) - D("99.5")) - D(50)) < D("0.01")

    bot.refresh_from_db()
    assert engine().run_bot(bot) is None  # same bar, nothing new


def test_short_signal_is_skipped_on_spot(account, owner, quotes):
    make_candles([100.0] * 70 + [90.0])
    bot = running_bot(account, owner, name="breakdown", strategy="donchian", timeframe="1d", allocation=5_000,
                      params={"donchian.channel_period": 5})
    decision = engine().run_bot(bot)
    assert decision.action == BotDecision.Action.SKIP
    assert "long-only" in decision.reason
    assert not Order.objects.filter(bot=bot).exists()


def test_quiet_bar_records_why_nothing_happened(account, owner, quotes):
    make_candles([100.0] * 71)
    bot = running_bot(account, owner, name="quiet", strategy="donchian", timeframe="1d", allocation=5_000,
                      params={"donchian.channel_period": 5})
    decision = engine().run_bot(bot)
    assert decision.action == BotDecision.Action.NONE
    assert decision.reason


def test_exposure_bot_rebalances_to_target_weight(account, owner, quotes):
    closes = [100.0 + i + (i % 2) * 0.5 for i in range(40)]
    make_candles(closes)
    quotes.set("BTC/USDT", 139, 140)
    bot = running_bot(
        account, owner, name="ensemble", strategy="donchian_ensemble", timeframe="1d", allocation=5_000,
        params={"donchian_ensemble.lookback_days": [2, 3], "donchian_ensemble.vol_window_days": 5,
                "donchian_ensemble.rebalance_threshold": 0.1},
    )
    decision = engine().run_bot(bot)
    assert decision.action == BotDecision.Action.REBALANCE
    held = Position.objects.get(book=bot.book).quantity
    assert held * D(140) > D(4_000)  # target is 100% of capital, less fees and rounding
    assert decision.diagnosis["target_weight"] == 1.0


def test_warming_up_is_reported_not_errored(account, owner, quotes):
    make_candles([100.0] * 3)
    bot = running_bot(account, owner, name="young", strategy="donchian", timeframe="1d", allocation=1_000)
    assert engine().run_bot(bot) is None
    bot.refresh_from_db()
    assert bot.status_reason.startswith("warming up")
    assert bot.consecutive_errors == 0


def test_stopping_a_bot_closes_its_position(account, owner, quotes):
    make_candles([100.0] * 70 + [110.0])
    bot = running_bot(account, owner, name="breakout", strategy="donchian", timeframe="1d", allocation=5_000,
                      params={"donchian.channel_period": 5})
    engine().run_bot(bot)
    stop_bot(bot)
    assert Position.objects.get(book=bot.book).quantity == 0
    bot.refresh_from_db()
    assert bot.status == Bot.Status.STOPPED


def test_failing_bot_is_paused_after_repeated_errors(account, owner, quotes, monkeypatch):
    bot = running_bot(account, owner, name="broken", strategy="donchian", timeframe="1d", allocation=1_000)
    monkeypatch.setattr("trading.engine.loop.build_bot_strategy", lambda *a, **k: 1 / 0)
    for _ in range(5):
        engine().run_bot(bot)
    bot.refresh_from_db()
    assert bot.status == Bot.Status.ERROR

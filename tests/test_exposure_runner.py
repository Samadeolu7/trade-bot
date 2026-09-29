from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from bot.backtest.exposure import run_exposure_backtest
from bot.shadow.exposure_runner import (
    INITIAL_PAPER_EQUITY,
    exposure_poll_once,
    load_exposure_state,
    maybe_send_exposure_summary,
)
from bot.storage.db import connect, count_exposure_rebalances, query_candles_df, upsert_candles
from bot.strategy.donchian_ensemble import DonchianEnsembleStrategy

LABEL = "ensemble_test"
FEE, SLIPPAGE = 0.001, 0.0005


def make_strategy(**overrides):
    params = {"lookback_days": [5, 10, 20], "vol_window_days": 10, "rebalance_threshold": 0.05}
    params.update(overrides)
    return DonchianEnsembleStrategy(params)


def random_candles(n, seed=3):
    """n completed daily candles ending yesterday, trending enough that
    the ensemble actually trades."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.002, 0.03, n)))
    end = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1)
    index = pd.date_range(end=end, periods=n, freq="D")
    return [[int(t.value // 1_000_000), c, c, c, c, 1.0] for t, c in zip(index, close)]


def poll(conn, strategy, alerter):
    with patch("bot.shadow.exposure_runner.backfill_candles"):
        exposure_poll_once(
            None, conn, alerter, "binance", "BTC/USDT", "1d", strategy, LABEL,
            fee=FEE, slippage=SLIPPAGE, backfill_start_date="2020-01-01T00:00:00Z",
        )


@pytest.fixture
def conn(tmp_path):
    return connect(str(tmp_path / "exposure.db"))


def test_live_bar_by_bar_matches_backtest_exactly(conn):
    """The whole point of sharing mark_to_market/rebalance: feeding candles
    to the paper bot one at a time must land on the same equity as the
    backtest over the same bars, starting from the same bar."""
    candles = random_candles(300)
    strategy = make_strategy()
    alerter = MagicMock()
    start = 60
    upsert_candles(conn, "binance", "BTC/USDT", "1d", candles[: start + 1])
    poll(conn, strategy, alerter)
    for candle in candles[start + 1 :]:
        upsert_candles(conn, "binance", "BTC/USDT", "1d", [candle])
        poll(conn, strategy, alerter)

    state = load_exposure_state(conn, LABEL)
    df = query_candles_df(conn, "binance", "BTC/USDT", "1d")
    window = df.iloc[start:]
    weights = strategy.target_weights(df).reindex(window.index)
    result = run_exposure_backtest(
        window, strategy, fee=FEE, slippage=SLIPPAGE, initial_capital=INITIAL_PAPER_EQUITY, weights=weights
    )
    assert state["equity"] == pytest.approx(result.equity_curve.iloc[-1], rel=1e-12)
    rebalances = count_exposure_rebalances(conn, "binance", "BTC/USDT", "1d", LABEL)
    assert rebalances > 5  # the scenario must actually exercise rebalancing
    assert alerter.send.call_count == rebalances  # one alert per live rebalance


def test_no_new_bar_is_a_no_op(conn):
    upsert_candles(conn, "binance", "BTC/USDT", "1d", random_candles(80))
    strategy, alerter = make_strategy(), MagicMock()
    poll(conn, strategy, alerter)
    state_before = load_exposure_state(conn, LABEL)
    calls_before = alerter.send.call_count
    poll(conn, strategy, alerter)
    assert load_exposure_state(conn, LABEL) == state_before
    assert alerter.send.call_count == calls_before


def test_catch_up_after_downtime_processes_every_bar_but_alerts_only_the_latest(conn):
    candles = random_candles(300)
    strategy = make_strategy(rebalance_threshold=0.0)  # rebalance every bar
    alerter = MagicMock()
    upsert_candles(conn, "binance", "BTC/USDT", "1d", candles[:100])
    poll(conn, strategy, alerter)
    alerter.reset_mock()
    upsert_candles(conn, "binance", "BTC/USDT", "1d", candles[100:])  # 200 bars while "down"
    poll(conn, strategy, alerter)

    assert load_exposure_state(conn, LABEL)["last_bar_ms"] == candles[-1][0]
    assert alerter.send.call_count <= 1
    if alerter.send.call_count:
        assert "EXPOSURE_REBALANCE" in alerter.send.call_args[0][0]


def test_first_run_starts_from_initial_equity(conn):
    upsert_candles(conn, "binance", "BTC/USDT", "1d", random_candles(80))
    poll(conn, make_strategy(), MagicMock())
    state = load_exposure_state(conn, LABEL)
    assert state["start_equity"] == INITIAL_PAPER_EQUITY
    assert 0.0 <= state["held"] <= 1.0


def test_skips_until_enough_history(conn):
    upsert_candles(conn, "binance", "BTC/USDT", "1d", random_candles(5))
    alerter = MagicMock()
    poll(conn, make_strategy(), alerter)
    assert load_exposure_state(conn, LABEL) is None
    alerter.send.assert_not_called()


def test_daily_summary_sent_once_per_day(conn):
    upsert_candles(conn, "binance", "BTC/USDT", "1d", random_candles(80))
    strategy = make_strategy()
    poll(conn, strategy, MagicMock())
    alerter = MagicMock()
    alerter.send.return_value = True
    maybe_send_exposure_summary(conn, alerter, "binance", "BTC/USDT", "1d", LABEL, strategy)
    maybe_send_exposure_summary(conn, alerter, "binance", "BTC/USDT", "1d", LABEL, strategy)
    assert alerter.send.call_count == 1
    message = alerter.send.call_args[0][0]
    assert message.startswith("DAILY_SUMMARY")
    assert f"strategy={LABEL}" in message
    assert "summary=" in message

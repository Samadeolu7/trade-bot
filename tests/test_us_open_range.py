import numpy as np
import pandas as pd
import pytest

from bot.backtest.engine import check_exit
from bot.research.intraday import orb
from bot.strategy.registry import STRATEGY_CATALOG, build_strategy
from bot.strategy.us_open_range import UsOpenRangeBreakoutStrategy


def candles(days=40, seed=1, start="2024-01-01"):
    idx = pd.date_range(start, periods=days * 96, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 40_000 * np.exp(np.cumsum(rng.normal(0, 0.003, len(idx))))
    open_ = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, 0.001, len(idx)))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * (1 + wick),
                         "low": np.minimum(open_, close) * (1 - wick), "close": close, "volume": 1.0}, index=idx)


def replay(df, strategy, history=500):
    """Bar by bar, the way the feed runner does it: stop first, then the
    time exit, then (when flat) a fresh signal on the same bar."""
    trades, pos = [], None
    for i in range(strategy.min_lookback - 1, len(df)):
        window = df.iloc[max(0, i - history + 1):i + 1]
        bar = window.iloc[-1]
        if pos is not None:
            price, _ = check_exit(pos, bar)
            if price is None and strategy.exit_due(window, pos["entry_time"]):
                price = float(bar["close"])
            if price is not None:
                trades.append((pos["entry_time"], window.index[-1], pos["direction"], pos["entry_price"], price))
                pos = None
        if pos is None:
            s = strategy.generate_signal(window)
            if s is not None:
                pos = {"direction": s.direction, "entry_price": s.entry_price, "stop": s.stop_loss,
                       "take_profit": None, "entry_time": window.index[-1]}
    return trades


@pytest.mark.parametrize("stop_at", ["mid", "opposite"])
def test_live_strategy_makes_the_backtests_trades(stop_at):
    df = candles()
    strategy = UsOpenRangeBreakoutStrategy({"range_minutes": 60, "stop_at": stop_at})
    live = replay(df, strategy)
    warmed_up = df.index[strategy.min_lookback - 1]  # live needs a day of history first
    research = [(t.entry_time, t.exit_time, "long" if t.direction > 0 else "short", t.entry, t.exit)
                for t in orb(df, range_minutes=60, stop_at=stop_at, session="always") if t.entry_time >= warmed_up]
    assert len(research) > 20
    assert len(live) == len(research)
    for a, b in zip(live, research):
        assert a[:3] == b[:3]
        assert a[3] == pytest.approx(b[3]) and a[4] == pytest.approx(b[4])


def test_one_entry_per_weekday_and_none_at_weekends():
    df = candles(days=14)
    trades = replay(df, UsOpenRangeBreakoutStrategy({}))
    days = [t[0].normalize() for t in trades]
    assert len(days) == len(set(days))
    assert all(d.weekday() < 5 for d in days)
    # every trade is out by the 23:30 candle (closing 23:45) the same day
    assert all(t[1] <= t[0].normalize() + pd.Timedelta(hours=23, minutes=30) for t in trades)


def test_signal_details_and_diagnosis():
    df = candles(days=2)
    day = pd.Timestamp("2024-01-02", tz="UTC")  # a Tuesday
    df = df[df.index < day + pd.Timedelta(hours=14, minutes=30)].copy()
    rng = df[df.index >= day + pd.Timedelta(hours=13, minutes=30)]
    hi, lo = rng["high"].max(), rng["low"].min()
    s = UsOpenRangeBreakoutStrategy({})
    assert s.diagnose(df)["state"] == "waiting for the US-open range to finish"
    t = day + pd.Timedelta(hours=14, minutes=30)
    df.loc[t] = [hi, hi * 1.01, hi, hi * 1.005, 1.0]
    signal = s.generate_signal(df)
    assert signal.direction == "long" and signal.stop_loss == pytest.approx((hi + lo) / 2)
    assert "closes by 23:45 UTC" in signal.reason
    assert s.exit_due(df, t) is None
    later = df.index[-1] + pd.Timedelta(hours=9)  # the 23:30 candle
    assert s.exit_due(df.rename(index={t: later}), t) == "end of day"


def test_registered_and_validates_params():
    assert STRATEGY_CATALOG["us_open_range"].kind == "signal"
    assert isinstance(build_strategy("us_open_range", {}), UsOpenRangeBreakoutStrategy)
    with pytest.raises(ValueError):
        UsOpenRangeBreakoutStrategy({"stop_at": "wide"})

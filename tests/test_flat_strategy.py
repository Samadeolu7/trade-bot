import pandas as pd

from bot.strategy.flat import FlatStrategy


def make_df(n):
    close = pd.Series([100.0] * n)
    return pd.DataFrame(
        {"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1.0}
    )


def test_generate_signal_always_none():
    strategy = FlatStrategy()
    assert strategy.generate_signal(make_df(10)) is None
    assert strategy.generate_signal(make_df(1)) is None


def test_entry_signals_always_empty():
    strategy = FlatStrategy()
    df = make_df(5)
    signals = strategy.entry_signals(df)
    assert len(signals) == 5
    assert signals["direction"].isna().all()


def test_trail_stop_is_noop_default():
    strategy = FlatStrategy()
    assert strategy.trail_stop(make_df(5), "long", 90.0) == 90.0


def test_min_lookback_is_one():
    assert FlatStrategy().min_lookback == 1

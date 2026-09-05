import pandas as pd

from bot.strategy.rsi_bb import RsiBollingerStrategy


def make_df(closes, wick=0.5):
    close = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + wick,
            "low": close - wick,
            "close": close,
            "volume": 1.0,
        }
    )


def build_strategy():
    return RsiBollingerStrategy({})  # defaults: rsi_period=14, bb_period=20


def test_min_lookback_computed_from_params():
    strategy = build_strategy()
    assert strategy.min_lookback == max(14, 20) * 3  # == 60


def test_no_signal_when_below_min_lookback():
    strategy = build_strategy()
    df = make_df([100.0] * 10)
    assert strategy.generate_signal(df) is None


def test_long_signal_on_oversold_at_lower_band():
    strategy = build_strategy()
    # long flat stretch (tight bands, RSI neutral) then a sharp one-bar crash:
    # RSI collapses (all prior deltas were 0 gain) and the crash pierces the
    # lower band, whose width is dominated by the other 19 flat bars
    df = make_df([100.0] * 59 + [80.0])
    assert len(df) == strategy.min_lookback

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == 80.0
    assert signal.stop_loss < signal.entry_price
    assert signal.take_profit > signal.entry_price
    assert "RSI" in signal.reason and "lower Bollinger" in signal.reason


def test_short_signal_on_overbought_at_upper_band():
    strategy = build_strategy()
    df = make_df([100.0] * 59 + [120.0])
    assert len(df) == strategy.min_lookback

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == 120.0
    assert signal.stop_loss > signal.entry_price
    assert signal.take_profit < signal.entry_price
    assert "RSI" in signal.reason and "upper Bollinger" in signal.reason


def test_no_signal_on_flat_market():
    strategy = build_strategy()
    df = make_df([100.0] * 60)
    assert strategy.generate_signal(df) is None

import pandas as pd
import pytest

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


def test_entry_signals_matches_generate_signal_long():
    strategy = build_strategy()
    df = make_df([100.0] * 59 + [80.0])

    signal = strategy.generate_signal(df)
    row = strategy.entry_signals(df).iloc[-1]

    assert row["direction"] == signal.direction == "long"
    assert row["entry_price"] == signal.entry_price
    assert row["stop_loss"] == signal.stop_loss
    assert row["take_profit"] == signal.take_profit
    assert row["reason"] == signal.reason


def test_entry_signals_matches_generate_signal_short():
    strategy = build_strategy()
    df = make_df([100.0] * 59 + [120.0])

    signal = strategy.generate_signal(df)
    row = strategy.entry_signals(df).iloc[-1]

    assert row["direction"] == signal.direction == "short"
    assert row["entry_price"] == signal.entry_price
    assert row["stop_loss"] == signal.stop_loss
    assert row["take_profit"] == signal.take_profit


def test_entry_signals_no_signal_on_flat_market():
    strategy = build_strategy()
    df = make_df([100.0] * 60)
    signals = strategy.entry_signals(df)
    assert signals["direction"].isna().all()


def test_stop_band_mult_scales_stop_distance_independent_of_target():
    df = make_df([100.0] * 59 + [80.0])

    default_signal = RsiBollingerStrategy({}).generate_signal(df)  # stop_band_mult=1.0
    tight_signal = RsiBollingerStrategy({"stop_band_mult": 0.5}).generate_signal(df)

    default_stop_distance = default_signal.entry_price - default_signal.stop_loss
    tight_stop_distance = tight_signal.entry_price - tight_signal.stop_loss

    assert tight_stop_distance == pytest.approx(default_stop_distance * 0.5)
    # target (take_profit) is unaffected by stop_band_mult -> better reward:risk
    assert tight_signal.take_profit == default_signal.take_profit

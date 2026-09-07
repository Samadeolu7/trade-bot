import pandas as pd

from bot.strategy.vol_expansion import VolatilityExpansionBreakoutStrategy


def make_df(closes, wick=0.3, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D", tz="UTC")
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {"open": close, "high": close + wick, "low": close - wick, "close": close, "volume": 1.0}
    )


def build_strategy():
    return VolatilityExpansionBreakoutStrategy(
        {
            "bb_period": 5, "bb_std": 2.0, "squeeze_lookback": 10, "squeeze_percentile": 0.3,
            "atr_period": 2, "atr_mult": 1.0,
        }
    )


# 24 bars of a decaying-amplitude zigzag around 100 — width contracts every
# bar, so the last pre-breakout bar is the tightest in its own trailing
# 10-bar window (guaranteeing it reads as squeezed), then a sharp breakout
_N = 24
_AMP = [20 * (0.8**i) for i in range(_N)]
COILING_UP = [100 + (_AMP[i] if i % 2 == 0 else -_AMP[i]) for i in range(_N)]
LONG_CLOSES = COILING_UP + [140]

COILING_DOWN = [100 - (_AMP[i] if i % 2 == 0 else -_AMP[i]) for i in range(_N)]
SHORT_CLOSES = COILING_DOWN + [60]


def test_min_lookback_formula():
    strategy = build_strategy()
    assert strategy.min_lookback == max(5, 2 * 3) + 10 + 1  # == 17


def test_no_signal_when_insufficient_data():
    strategy = build_strategy()
    df = make_df(LONG_CLOSES[:16])
    assert strategy.generate_signal(df) is None


def test_long_signal_on_squeeze_then_upside_breakout():
    strategy = build_strategy()
    df = make_df(LONG_CLOSES)

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == 140.0
    assert signal.stop_loss < signal.entry_price
    assert "squeeze" in signal.reason and "upside" in signal.reason
    assert signal.context["prior_bb_width_pct"] < signal.context["bb_width_pct"]


def test_short_signal_on_squeeze_then_downside_breakout():
    strategy = build_strategy()
    df = make_df(SHORT_CLOSES)

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == 60.0
    assert signal.stop_loss > signal.entry_price
    assert "squeeze" in signal.reason and "downside" in signal.reason


def test_no_signal_without_a_squeeze_beforehand():
    # steady, evenly wide oscillation the whole way — width never contracts
    # relative to its own trailing history, so even a clean breakout at the
    # end shouldn't count as a squeeze-then-expansion sequence
    closes = [100 + (10 if i % 2 == 0 else -10) for i in range(_N)] + [140]
    strategy = build_strategy()
    df = make_df(closes)
    assert strategy.generate_signal(df) is None


def test_entry_signals_matches_generate_signal_at_breakout_bar():
    strategy = build_strategy()
    df = make_df(LONG_CLOSES)
    vec = strategy.entry_signals(df)
    scalar = strategy.generate_signal(df)

    last = vec.iloc[-1]
    assert last["direction"] == scalar.direction
    assert last["entry_price"] == scalar.entry_price
    assert last["stop_loss"] == scalar.stop_loss


def test_trail_stop_only_ratchets_favorably():
    strategy = build_strategy()
    df = make_df(LONG_CLOSES)

    ratcheted = strategy.trail_stop(df, "long", current_stop=50.0)
    assert ratcheted > 50.0

    stop_already_tight = 139.0
    assert strategy.trail_stop(df, "long", stop_already_tight) == stop_already_tight

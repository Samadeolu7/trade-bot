import pandas as pd

from bot.backtest.engine import run_backtest
from bot.strategy.donchian import DonchianBreakoutStrategy
from bot.strategy.donchian_ensemble import DonchianEnsembleStrategy
from bot.strategy.long_only import LongOnlyStrategy
from bot.strategy.regime_switch import RegimeSwitchedStrategy
from bot.strategy.registry import STRATEGY_CATALOG, STRATEGY_CHOICES, build_strategy


def make_df(closes, wick=0.5):
    close = pd.Series(closes, dtype=float).to_numpy()
    return pd.DataFrame(
        {"open": close, "high": close + wick, "low": close - wick, "close": close, "volume": 1.0},
        index=pd.date_range("2024-01-01", periods=len(close), freq="1D", tz="UTC"),
    )


def test_every_catalog_entry_builds():
    for name in STRATEGY_CHOICES:
        strategy = build_strategy(name, {})
        assert strategy.min_lookback >= 1
        assert (STRATEGY_CATALOG[name].kind == "exposure") == hasattr(strategy, "target_weights")


def test_unknown_name_falls_back_to_regime_switched_like_the_cli_did():
    assert isinstance(build_strategy("regime_switched", {}), RegimeSwitchedStrategy)


def test_long_only_flag_wraps_signal_strategies():
    strategy = build_strategy("donchian", {"donchian": {"channel_period": 5, "long_only": True}})
    assert isinstance(strategy, LongOnlyStrategy)
    assert strategy.name == "donchian"


def test_long_only_flag_is_ignored_for_exposure_strategies():
    strategy = build_strategy("donchian_ensemble", {"donchian_ensemble": {"long_only": True}})
    assert isinstance(strategy, DonchianEnsembleStrategy)


def test_long_only_drops_short_breakouts_and_keeps_longs():
    base = DonchianBreakoutStrategy({"channel_period": 5})
    wrapped = LongOnlyStrategy(base)
    down = make_df([100.0] * 30 + [90.0])
    up = make_df([100.0] * 30 + [110.0])

    assert base.generate_signal(down).direction == "short"
    assert wrapped.generate_signal(down) is None
    assert wrapped.generate_signal(up).direction == "long"


def test_long_only_backtest_has_no_short_trades():
    closes = [100.0] * 30 + [95.0 - 2 * i for i in range(15)] + [66.0 + 3 * i for i in range(30)]
    df = make_df(closes)
    base = DonchianBreakoutStrategy({"channel_period": 5})

    both = run_backtest(df, base)
    longs = run_backtest(df, LongOnlyStrategy(base))

    assert any(t.direction == "short" for t in both.trades)
    assert longs.trades and all(t.direction == "long" for t in longs.trades)

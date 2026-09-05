import pandas as pd

from bot.strategy.regime_switch import RegimeSwitchedStrategy


class FakeSubStrategy:
    """Ignores the df it's given and always returns a fixed entry_signals
    frame, so the composite's own routing logic can be tested in isolation
    from real indicator math."""

    def __init__(self, frame: pd.DataFrame):
        self.min_lookback = 1
        self.frame = frame

    def entry_signals(self, df):
        return self.frame


class FakeRegimeFilter:
    def __init__(self, regimes: pd.Series):
        self.min_lookback = 1
        self.regimes = regimes

    def regime_series(self, df):
        return self.regimes


def _frame(index, direction, entry_price, stop_loss, take_profit, reason):
    return pd.DataFrame(
        {
            "direction": direction,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reason": reason,
        },
        index=index,
    )


def test_entry_signals_routes_by_regime_and_tags_owner():
    index = [0, 1, 2]
    trending_frame = _frame(
        index,
        direction=["long", None, None],
        entry_price=[100.0, float("nan"), float("nan")],
        stop_loss=[90.0, float("nan"), float("nan")],
        take_profit=[float("nan")] * 3,
        reason=["trend-reason", None, None],
    )
    ranging_frame = _frame(
        index,
        direction=[None, "short", None],
        entry_price=[float("nan"), 200.0, float("nan")],
        stop_loss=[float("nan"), 210.0, float("nan")],
        take_profit=[float("nan"), 190.0, float("nan")],
        reason=[None, "range-reason", None],
    )
    regimes = pd.Series(["trending", "ranging", None], index=index)

    trending = FakeSubStrategy(trending_frame)
    ranging = FakeSubStrategy(ranging_frame)
    composite = RegimeSwitchedStrategy(trending, ranging, FakeRegimeFilter(regimes))

    combined = composite.entry_signals(pd.DataFrame(index=index))

    # row 0: trending regime -> the trending sub-strategy's signal, tagged as its owner
    assert combined.loc[0, "direction"] == "long"
    assert combined.loc[0, "entry_price"] == 100.0
    assert combined.loc[0, "stop_loss"] == 90.0
    assert combined.loc[0, "reason"] == "trend-reason"
    assert combined.loc[0, "strategy"] is trending

    # row 1: ranging regime -> the ranging sub-strategy's signal
    assert combined.loc[1, "direction"] == "short"
    assert combined.loc[1, "entry_price"] == 200.0
    assert combined.loc[1, "take_profit"] == 190.0
    assert combined.loc[1, "strategy"] is ranging

    # row 2: unknown regime -> no signal, no owner, regardless of what the subs returned
    assert pd.isna(combined.loc[2, "direction"])
    assert pd.isna(combined.loc[2, "strategy"])


def test_min_lookback_is_max_of_all_three_inputs():
    trending = FakeSubStrategy(pd.DataFrame())
    trending.min_lookback = 50
    ranging = FakeSubStrategy(pd.DataFrame())
    ranging.min_lookback = 30
    regime_filter = FakeRegimeFilter(pd.Series(dtype=object))
    regime_filter.min_lookback = 42

    composite = RegimeSwitchedStrategy(trending, ranging, regime_filter)

    assert composite.min_lookback == 50

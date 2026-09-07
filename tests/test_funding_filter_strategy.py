import pandas as pd

from bot.strategy.base import Signal, Strategy
from bot.strategy.funding_filter import FundingFilteredStrategy


class AlwaysLongStrategy(Strategy):
    name = "always_long"

    def __init__(self):
        super().__init__({})
        self.min_lookback = 1

    def generate_signal(self, df):
        close = df["close"].iloc[-1]
        return Signal(
            symbol="", timeframe="", direction="long", entry_price=close,
            stop_loss=close - 10, take_profit=None, reason="always long",
            timestamp=df.index[-1],
        )

    def entry_signals(self, df):
        close = df["close"]
        return pd.DataFrame(
            {
                "direction": ["long"] * len(df),
                "entry_price": close,
                "stop_loss": close - 10,
                "take_profit": float("nan"),
                "reason": ["always long"] * len(df),
            },
            index=df.index,
        )

    def trail_stop(self, df, direction, current_stop):
        return current_stop + 1  # distinctive value, to verify delegation


class AlwaysShortStrategy(Strategy):
    name = "always_short"

    def __init__(self):
        super().__init__({})
        self.min_lookback = 1

    def generate_signal(self, df):
        close = df["close"].iloc[-1]
        return Signal(
            symbol="", timeframe="", direction="short", entry_price=close,
            stop_loss=close + 10, take_profit=None, reason="always short",
            timestamp=df.index[-1],
        )


def make_df(n=5, start="2020-01-01"):
    idx = pd.date_range(start, periods=n, freq="D", tz="UTC")
    close = pd.Series([100.0] * n, index=idx)
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0}
    )


def make_funding_df(pairs):
    idx = pd.DatetimeIndex([p[0] for p in pairs], name="funding_time")
    return pd.DataFrame({"funding_rate": [p[1] for p in pairs]}, index=idx)


def test_passes_through_when_funding_within_bounds():
    df = make_df(3)
    funding_df = make_funding_df([(df.index[0], 0.0001)])
    strat = FundingFilteredStrategy(
        AlwaysLongStrategy(), funding_df, high_threshold=0.0005, low_threshold=-0.0005
    )
    signal = strat.generate_signal(df)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.context["funding_rate"] == 0.0001


def test_vetoes_long_when_funding_crowded_long():
    df = make_df(3)
    funding_df = make_funding_df([(df.index[-1], 0.001)])  # above high_threshold
    strat = FundingFilteredStrategy(
        AlwaysLongStrategy(), funding_df, high_threshold=0.0005, low_threshold=-0.0005
    )
    assert strat.generate_signal(df) is None


def test_vetoes_short_when_funding_crowded_short():
    df = make_df(3)
    funding_df = make_funding_df([(df.index[-1], -0.001)])  # below low_threshold
    strat = FundingFilteredStrategy(
        AlwaysShortStrategy(), funding_df, high_threshold=0.0005, low_threshold=-0.0005
    )
    assert strat.generate_signal(df) is None


def test_passes_through_unfiltered_when_no_funding_data_yet():
    df = make_df(3)
    funding_df = make_funding_df([])
    strat = FundingFilteredStrategy(AlwaysLongStrategy(), funding_df, 0.0005, -0.0005)
    signal = strat.generate_signal(df)
    assert signal is not None
    assert "funding_rate" not in signal.context


def test_uses_most_recent_funding_rate_at_or_before_bar():
    df = make_df(3)
    funding_df = make_funding_df(
        [
            (df.index[0] - pd.Timedelta(hours=1), 0.0001),
            (df.index[-1], 0.0002),  # exact match on the last bar
        ]
    )
    strat = FundingFilteredStrategy(AlwaysLongStrategy(), funding_df, 0.0005, -0.0005)
    signal = strat.generate_signal(df)
    assert signal.context["funding_rate"] == 0.0002


def test_entry_signals_matches_generate_signal_per_bar():
    df = make_df(5)
    funding_df = make_funding_df(
        [
            (df.index[0], 0.0001),  # within bounds
            (df.index[2], 0.001),   # crowded from here on
        ]
    )
    strat = FundingFilteredStrategy(AlwaysLongStrategy(), funding_df, 0.0005, -0.0005)
    vec = strat.entry_signals(df)

    for i in range(len(df)):
        window = df.iloc[: i + 1]
        scalar = strat.generate_signal(window)
        row = vec.iloc[i]
        if scalar is None:
            assert row["direction"] is None or pd.isna(row["direction"])
        else:
            assert row["direction"] == scalar.direction


def test_trail_stop_delegates_to_base():
    df = make_df(3)
    strat = FundingFilteredStrategy(AlwaysLongStrategy(), make_funding_df([]), 0.0005, -0.0005)
    assert strat.trail_stop(df, "long", 50.0) == 51.0


def test_before_poll_refreshes_funding_df_via_refresh_fn():
    df = make_df(3)
    initial = make_funding_df([(df.index[-1], 0.0001)])
    refreshed = make_funding_df([(df.index[-1], 0.001)])
    strat = FundingFilteredStrategy(
        AlwaysLongStrategy(), initial, 0.0005, -0.0005, refresh_fn=lambda: refreshed
    )
    assert strat.generate_signal(df) is not None  # 0.0001 is within bounds

    strat.before_poll()
    assert strat.generate_signal(df) is None  # now 0.001 — crowded


def test_before_poll_is_noop_without_refresh_fn():
    strat = FundingFilteredStrategy(AlwaysLongStrategy(), make_funding_df([]), 0.0005, -0.0005)
    strat.before_poll()  # should not raise

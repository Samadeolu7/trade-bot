from dataclasses import replace
from typing import Callable

import pandas as pd

from bot.strategy.base import Direction, Signal, Strategy


class FundingFilteredStrategy(Strategy):
    """Wraps a base strategy with a perpetual-futures funding-rate filter
    (roadmap shortlist candidate #5 / spec Section 7d): funding rates are
    public positioning data — very high positive funding tends to correlate
    with over-leveraged longs (pullback risk), very negative funding with
    over-leveraged shorts. Used as a confirmation/veto only: a long signal
    from the base strategy is suppressed when funding is already crowded
    long (above `high_threshold`); a short is suppressed when funding is
    already crowded short (below `low_threshold`). Any trade that does fire
    still executes on Quidax's spot market — funding is Binance-futures-only
    data used purely as a filter input, spot never sees a futures order.

    Funding prints roughly every 8h, far sparser than the base candle
    timeframe, so the most recent rate at-or-before each bar's timestamp is
    looked up (as-of / backward-fill), not a one-per-bar alignment."""

    name = "funding_filtered"

    def __init__(
        self,
        base: Strategy,
        funding_df: pd.DataFrame,
        high_threshold: float,
        low_threshold: float,
        refresh_fn: Callable[[], pd.DataFrame] | None = None,
    ):
        super().__init__({})
        self.base = base
        self.funding_df = funding_df.sort_index()
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        # Live shadow runs need fresh funding data (a new print every ~8h);
        # backtests get a fixed funding_df for the whole historical window
        # and pass no refresh_fn.
        self.refresh_fn = refresh_fn
        self.min_lookback = base.min_lookback

    def before_poll(self) -> None:
        if self.refresh_fn is not None:
            self.funding_df = self.refresh_fn().sort_index()

    def _funding_rate_at(self, timestamp) -> float | None:
        if len(self.funding_df) == 0:
            # an empty index built with no rows defaults to tz-naive, which
            # would raise comparing against a tz-aware timestamp below
            return None
        eligible = self.funding_df.loc[:timestamp]
        if len(eligible) == 0:
            return None
        return float(eligible["funding_rate"].iloc[-1])

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        signal = self.base.generate_signal(df)
        if signal is None:
            return None
        rate = self._funding_rate_at(df.index[-1])
        if rate is None:
            # no funding data yet (e.g. still backfilling on a fresh shadow
            # run) — pass the base signal through unfiltered rather than
            # blocking every entry until funding history exists
            return signal
        if signal.direction == "long" and rate > self.high_threshold:
            return None
        if signal.direction == "short" and rate < self.low_threshold:
            return None
        return replace(signal, context={**signal.context, "funding_rate": round(rate, 6)})

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        base_signals = self.base.entry_signals(df)
        if len(self.funding_df) == 0:
            return base_signals

        left = pd.DataFrame({"open_time": df.index}).sort_values("open_time")
        right = (
            self.funding_df.reset_index()[["funding_time", "funding_rate"]]
            .sort_values("funding_time")
        )
        merged = pd.merge_asof(
            left, right, left_on="open_time", right_on="funding_time", direction="backward"
        )
        funding = pd.Series(merged["funding_rate"].to_numpy(), index=df.index)

        vetoed_long = (base_signals["direction"] == "long") & (funding > self.high_threshold)
        vetoed_short = (base_signals["direction"] == "short") & (funding < self.low_threshold)
        result = base_signals.copy()
        result.loc[(vetoed_long | vetoed_short).fillna(False), "direction"] = None
        return result

    def trail_stop(self, df: pd.DataFrame, direction: Direction, current_stop: float) -> float:
        return self.base.trail_stop(df, direction, current_stop)

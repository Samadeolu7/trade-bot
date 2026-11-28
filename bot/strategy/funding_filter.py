from dataclasses import replace
from typing import Callable

import pandas as pd

from bot.data.availability import LagModel, asof_values
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
    looked up (as-of / backward-fill), not a one-per-bar alignment.

    `lag` (backtests only): a LagModel saying when each print would really
    have reached the engine; see bot/data/availability.py. Without one, the
    rate is looked up at each bar's open time, as the live path does.

    Thresholds: fixed (`high_threshold` / `low_threshold`), or with
    `percentile` set, trailing percentiles of funding itself: a long is
    vetoed when funding is above its `percentile` over the last
    `window_days` (and a short below its 1 - `percentile`). Each print's
    thresholds use only prints at or before it, so there is no look-ahead,
    and they arrive with that print under any lag model. Until
    `min_days` of history exist there are no thresholds and nothing is
    vetoed."""

    name = "funding_filtered"

    def __init__(
        self,
        base: Strategy,
        funding_df: pd.DataFrame,
        high_threshold: float,
        low_threshold: float,
        refresh_fn: Callable[[], pd.DataFrame] | None = None,
        lag: LagModel | None = None,
        percentile: float | None = None,
        window_days: int = 365,
        min_days: int = 90,
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
        self.lag = lag
        if percentile is not None and not 0.5 < percentile < 1:
            raise ValueError(f"funding_filtered percentile must be between 0.5 and 1, got {percentile!r}")
        self.percentile = percentile
        self.window_days = int(window_days)
        self.min_days = int(min_days)
        self._levels_cache: tuple[pd.DataFrame, pd.DataFrame] | None = None
        self.min_lookback = base.min_lookback

    def levels(self) -> pd.DataFrame:
        """Per print (indexed by funding_time): its rate and the veto
        thresholds in force from that print on."""
        df = self.funding_df
        if self._levels_cache is not None and self._levels_cache[0] is df:
            return self._levels_cache[1]
        rate = df["funding_rate"].astype(float)
        if self.percentile is None:
            out = pd.DataFrame({"rate": rate, "high": self.high_threshold, "low": self.low_threshold},
                               index=df.index)
        else:
            window = rate.rolling(f"{self.window_days}D")
            enough = (rate.index - rate.index[0]) >= pd.Timedelta(days=self.min_days) if len(rate) else []
            high = window.quantile(self.percentile).where(enough)
            low = window.quantile(1 - self.percentile).where(enough)
            out = pd.DataFrame({"rate": rate, "high": high, "low": low}, index=df.index)
        self._levels_cache = (df, out)
        return out

    def before_poll(self) -> None:
        if self.refresh_fn is not None:
            self.funding_df = self.refresh_fn().sort_index()

    def _levels_at(self, timestamp) -> tuple[float | None, float, float]:
        """(rate, high, low) at-or-before timestamp; thresholds are NaN
        (never veto) while a percentile filter is still warming up."""
        if len(self.funding_df) == 0:
            # an empty index built with no rows defaults to tz-naive, which
            # would raise comparing against a tz-aware timestamp below
            return None, float("nan"), float("nan")
        eligible = self.levels().loc[:timestamp]
        if len(eligible) == 0:
            return None, float("nan"), float("nan")
        row = eligible.iloc[-1]
        return float(row["rate"]), float(row["high"]), float(row["low"])

    def _funding_rate_at(self, timestamp) -> float | None:
        return self._levels_at(timestamp)[0]

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        signal = self.base.generate_signal(df)
        if signal is None:
            return None
        rate, high, low = self._levels_at(df.index[-1])
        if rate is None:
            # no funding data yet (e.g. still backfilling on a fresh shadow
            # run) — pass the base signal through unfiltered rather than
            # blocking every entry until funding history exists
            return signal
        if signal.direction == "long" and rate > high:
            return None
        if signal.direction == "short" and rate < low:
            return None
        return replace(signal, context={**signal.context, "funding_rate": round(rate, 6)})

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        base_signals = self.base.entry_signals(df)
        if len(self.funding_df) == 0:
            return base_signals

        levels = self.levels()
        if self.lag is not None and len(df) >= 2:
            at = pd.DataFrame({c: asof_values(levels[c], df.index, self.lag) for c in levels.columns})
        else:
            left = pd.DataFrame({"open_time": df.index}).sort_values("open_time")
            right = levels.rename_axis("funding_time").reset_index().sort_values("funding_time")
            merged = pd.merge_asof(left, right, left_on="open_time", right_on="funding_time", direction="backward")
            at = pd.DataFrame({c: merged[c].to_numpy() for c in levels.columns}, index=df.index)

        vetoed_long = (base_signals["direction"] == "long") & (at["rate"] > at["high"])
        vetoed_short = (base_signals["direction"] == "short") & (at["rate"] < at["low"])
        result = base_signals.copy()
        result.loc[(vetoed_long | vetoed_short).fillna(False), "direction"] = None
        return result

    def trail_stop(self, df: pd.DataFrame, direction: Direction, current_stop: float) -> float:
        return self.base.trail_stop(df, direction, current_stop)

    def diagnose(self, df: pd.DataFrame) -> dict:
        """Merges the base strategy's own diagnose with the current funding
        rate, and — this is the interesting case — overrides the near-miss
        with an explicit "would have fired but funding vetoed it" whenever
        that's what's actually happening. That's a materially different (and
        more actionable) situation than the base's own generic near-miss, so
        it takes precedence when both are true."""
        result = dict(self.base.diagnose(df))
        rate, high, low = self._levels_at(df.index[-1])
        result["funding_rate"] = rate
        if rate is None:
            return result

        base_signal = self.base.generate_signal(df)
        if base_signal is None:
            return result
        if base_signal.direction == "long" and rate > high:
            result["near_miss"] = True
            result["near_miss_key"] = "long_signal_vetoed_by_funding"
            result["near_miss_reason"] = (
                f"{self.base.name} would enter long here, but funding rate {rate:.5f} "
                f"exceeds the veto threshold {high:.5f} (crowded-long risk)"
            )
        elif base_signal.direction == "short" and rate < low:
            result["near_miss"] = True
            result["near_miss_key"] = "short_signal_vetoed_by_funding"
            result["near_miss_reason"] = (
                f"{self.base.name} would enter short here, but funding rate {rate:.5f} "
                f"is below the veto threshold {low:.5f} (crowded-short risk)"
            )
        return result

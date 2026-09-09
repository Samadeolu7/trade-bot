from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

Direction = Literal["long", "short", "flat"]


@dataclass
class Signal:
    symbol: str
    timeframe: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float | None
    reason: str
    timestamp: pd.Timestamp
    # Diagnostic fields beyond the human-readable `reason` — regime state,
    # indicator values at signal time, etc. Optional and strategy-specific;
    # persisted alongside the signal/trade so patterns like "only profitable
    # when volatility is expanding" are discoverable later without having
    # rerun anything. Keep values JSON-serializable (str/int/float/bool).
    context: dict = field(default_factory=dict)


class Strategy(ABC):
    """A strategy turns a trailing window of OHLCV candles into at most one
    signal for the latest bar. `min_lookback` tells callers (live poll loop,
    backtest engine) how many trailing bars must be included in `df` for this
    strategy's indicators to be valid on the last row."""

    name: str
    min_lookback: int

    def __init__(self, params: dict):
        self.params = params

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        """df: OHLCV candles indexed by open_time, ascending, with at least
        `min_lookback` rows. Returns a Signal for the last row, or None."""
        ...

    def trail_stop(self, df: pd.DataFrame, direction: Direction, current_stop: float) -> float:
        """Recompute a trailing stop for an open position from the latest bar.
        Default: no trailing — the stop set at entry never moves. Strategies
        whose exit is itself a trailing stop (e.g. ATR-based) override this;
        the result must only ever move in the position's favor."""
        return current_stop

    def before_poll(self) -> None:
        """Optional hook the shadow runner calls once per live iteration,
        before generate_signal — for strategies that need to refresh
        external state that isn't part of `df` itself (e.g.
        FundingFilteredStrategy re-backfilling funding rates). No-op by
        default. The backtest engine never calls this: entry_signals/
        generate_signal there only ever see the fixed historical df."""
        return None

    def diagnose(self, df: pd.DataFrame) -> dict:
        """A snapshot of this strategy's current read of the market —
        regime state, indicator values, how close conditions are to firing
        — even when generate_signal returns nothing. Exists so "why didn't
        it trade" is answerable without guessing: used by the `diagnose` CLI
        command, the enriched daily Telegram summary, and near-miss alerts.

        Three keys have special meaning to those callers; everything else is
        free-form diagnostic context:
        - `near_miss` (bool): a human would plausibly expect an entry soon.
        - `near_miss_key` (str | None): a short, STABLE category identifying
          *which* near-miss condition this is (e.g. "bullish_pullback_
          awaiting_reclaim") — used to de-duplicate repeated alerts, so it
          must not embed values that drift bar-to-bar (a distance-to-target
          percentage, say). Required whenever near_miss is True.
        - `near_miss_reason` (str | None): human-readable detail, free to
          include numbers — shown in alerts but never compared for dedup.

        Default: nothing to report. Strategies actually deployed in a
        shadow run should override this; ones that aren't (yet) don't need
        to."""
        return {"near_miss": False, "near_miss_key": None, "near_miss_reason": None}

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Vectorized equivalent of generate_signal: for every bar in df,
        would this strategy open a position there? Returns a frame aligned to
        df.index with columns direction/entry_price/stop_loss/take_profit/
        reason (direction is None where nothing fires).

        The backtest engine uses this instead of calling generate_signal once
        per bar over a re-sliced trailing window — recomputing EMA/RSI/ATR
        etc. from scratch at every single bar is both O(n * lookback) instead
        of O(n), and numerically restarts each indicator's "memory" every
        window instead of letting it run continuously, which biases anything
        recursive (EMA, Wilder smoothing) versus how it actually behaves live.

        Default implementation: correct but slow — replays generate_signal
        per bar over a trailing window, identical to the old engine behavior.
        Strategies should override this with a real vectorized computation;
        this fallback exists so a Strategy that doesn't bother is still usable
        (e.g. a test double, or a quick prototype) without the engine caring.
        """
        directions: list = []
        entries: list = []
        stops: list = []
        take_profits: list = []
        reasons: list = []

        for i in range(len(df)):
            window = df.iloc[max(0, i - self.min_lookback + 1) : i + 1]
            signal = self.generate_signal(window) if len(window) >= self.min_lookback else None
            if signal is not None:
                directions.append(signal.direction)
                entries.append(signal.entry_price)
                stops.append(signal.stop_loss)
                take_profits.append(signal.take_profit)
                reasons.append(signal.reason)
            else:
                directions.append(None)
                entries.append(float("nan"))
                stops.append(float("nan"))
                take_profits.append(float("nan"))
                reasons.append(None)

        return pd.DataFrame(
            {
                "direction": directions,
                "entry_price": entries,
                "stop_loss": stops,
                "take_profit": take_profits,
                "reason": reasons,
            },
            index=df.index,
        )

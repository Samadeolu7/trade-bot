from abc import ABC, abstractmethod
from dataclasses import dataclass
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

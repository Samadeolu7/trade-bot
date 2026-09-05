import numpy as np
import pandas as pd

from bot.indicators.indicators import bollinger_bands, rsi
from bot.strategy.base import Signal, Strategy


class RsiBollingerStrategy(Strategy):
    """Mean-reversion: fade RSI extremes at the Bollinger Bands in range-bound
    conditions (spec Section 7b). Intended to run only while RegimeFilter
    reports "ranging". Target is the band midline; stop is one band-width
    beyond entry."""

    name = "rsi_bb"

    def __init__(self, params: dict):
        super().__init__(params)
        self.rsi_period = params.get("rsi_period", 14)
        self.bb_period = params.get("bb_period", 20)
        self.bb_std = params.get("bb_std", 2.0)
        self.rsi_oversold = params.get("rsi_oversold", 30)
        self.rsi_overbought = params.get("rsi_overbought", 70)
        self.min_lookback = max(self.rsi_period, self.bb_period) * 3

    def _indicators(self, df: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
        return rsi(df["close"], self.rsi_period), bollinger_bands(df["close"], self.bb_period, self.bb_std)

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_lookback:
            return None

        rsi_series, bb_frame = self._indicators(df)
        last_rsi = rsi_series.iloc[-1]
        bb = bb_frame.iloc[-1]

        if pd.isna(last_rsi) or pd.isna(bb["bb_lower"]):
            return None

        entry = df["close"].iloc[-1]
        band_width = bb["bb_upper"] - bb["bb_mid"]
        timestamp = df.index[-1]

        if last_rsi < self.rsi_oversold and entry <= bb["bb_lower"]:
            return Signal(
                symbol="",
                timeframe="",
                direction="long",
                entry_price=entry,
                stop_loss=entry - band_width,
                take_profit=bb["bb_mid"],
                reason=f"RSI {last_rsi:.1f} < {self.rsi_oversold} at/below lower Bollinger Band",
                timestamp=timestamp,
            )
        if last_rsi > self.rsi_overbought and entry >= bb["bb_upper"]:
            return Signal(
                symbol="",
                timeframe="",
                direction="short",
                entry_price=entry,
                stop_loss=entry + band_width,
                take_profit=bb["bb_mid"],
                reason=f"RSI {last_rsi:.1f} > {self.rsi_overbought} at/above upper Bollinger Band",
                timestamp=timestamp,
            )
        return None

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Vectorized: RSI/Bollinger computed once over the full df."""
        rsi_series, bb_frame = self._indicators(df)
        close = df["close"]
        band_width = bb_frame["bb_upper"] - bb_frame["bb_mid"]

        oversold = (rsi_series < self.rsi_oversold) & (close <= bb_frame["bb_lower"])
        overbought = (rsi_series > self.rsi_overbought) & (close >= bb_frame["bb_upper"])

        direction = np.where(oversold, "long", np.where(overbought, "short", None))
        stop_loss = np.where(
            oversold, close - band_width, np.where(overbought, close + band_width, np.nan)
        )
        take_profit = np.where(oversold | overbought, bb_frame["bb_mid"], np.nan)

        reason = np.full(len(df), None, dtype=object)
        for i in np.flatnonzero(oversold.to_numpy()):
            reason[i] = (
                f"RSI {rsi_series.iloc[i]:.1f} < {self.rsi_oversold} at/below lower Bollinger Band"
            )
        for i in np.flatnonzero(overbought.to_numpy()):
            reason[i] = (
                f"RSI {rsi_series.iloc[i]:.1f} > {self.rsi_overbought} at/above upper Bollinger Band"
            )

        return pd.DataFrame(
            {
                "direction": direction,
                "entry_price": np.where(direction != None, close, np.nan),  # noqa: E711
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "reason": reason,
            },
            index=df.index,
        )

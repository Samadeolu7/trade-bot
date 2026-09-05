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

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_lookback:
            return None

        last_rsi = rsi(df["close"], self.rsi_period).iloc[-1]
        bb = bollinger_bands(df["close"], self.bb_period, self.bb_std).iloc[-1]

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

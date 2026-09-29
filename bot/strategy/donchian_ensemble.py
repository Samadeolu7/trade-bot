import numpy as np
import pandas as pd

from bot.strategy.base import Signal, Strategy

DEFAULT_LOOKBACK_DAYS = [5, 10, 20, 30, 60, 90, 150, 250, 360]


class DonchianEnsembleStrategy(Strategy):
    """Donchian ensemble with volatility-targeted sizing, after Zarattini,
    Pagani & Barbon, "Catching Crypto Trends" (SSRN 5209907, 2025).
    Research candidate added 2026-09-29.

    Nine independent long-only Donchian models, one per lookback in
    `lookback_days`. Each one:
    - enters when the close is above the highest close of the prior N bars
    - sets a trailing stop at the channel midpoint ((max + min close over
      N bars) / 2), which only ever ratchets up
    - exits when the close falls below that stop

    The ensemble signal is the fraction of models currently long (0..1),
    so no single lookback has to be the "right" one. That's the direct fix
    for plain donchian's weak spot here, where one hand-picked exit width
    looked great in-sample and faded out of sample. Position size is
    signal x (vol_target / realized vol), capped at `max_weight` (1.0 = no
    leverage), with realized vol measured over `vol_window_days`.

    Unlike every other strategy here, the output is a target *fraction of
    capital* each bar rather than a discrete entry with a stop, so it's
    backtested by bot/backtest/exposure.py instead of the stop-based engine.
    The shadow and recommend loops don't support that yet, so it's
    research-only until it earns a live run.

    `bars_per_day` scales the day-based lookbacks and vol window to the
    candle timeframe (1 for 1d, 6 for 4h)."""

    name = "donchian_ensemble"

    def __init__(self, params: dict):
        super().__init__(params)
        self.lookback_days = list(params.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
        self.bars_per_day = params.get("bars_per_day", 1)
        self.vol_target = params.get("vol_target", 0.25)
        self.vol_window_days = params.get("vol_window_days", 90)
        self.max_weight = params.get("max_weight", 1.0)
        self.rebalance_threshold = params.get("rebalance_threshold", 0.0)

        self.lookbacks = [max(1, int(round(d * self.bars_per_day))) for d in self.lookback_days]
        self.vol_window = max(2, int(round(self.vol_window_days * self.bars_per_day)))
        self.periods_per_year = 365 * self.bars_per_day
        self.min_lookback = max(max(self.lookbacks), self.vol_window) + 1

    def model_positions(self, df: pd.DataFrame) -> pd.DataFrame:
        """0/1 per lookback per bar: is that model long as of this bar's
        close? Sequential by nature (the stop ratchets), hence the loop."""
        close = df["close"]
        out = {}
        for n in self.lookbacks:
            upper = close.rolling(n).max().shift(1).to_numpy()
            mid = ((close.rolling(n).max() + close.rolling(n).min()) / 2).to_numpy()
            c = close.to_numpy()
            pos = np.zeros(len(c))
            in_pos, stop = False, np.nan
            for t in range(len(c)):
                if in_pos:
                    if c[t] < stop:
                        in_pos = False
                    else:
                        stop = max(stop, mid[t])
                elif not np.isnan(upper[t]) and c[t] > upper[t]:
                    in_pos, stop = True, mid[t]
                pos[t] = 1.0 if in_pos else 0.0
            out[n] = pos
        return pd.DataFrame(out, index=df.index)

    def ensemble_signal(self, df: pd.DataFrame) -> pd.Series:
        return self.model_positions(df).mean(axis=1)

    def realized_vol(self, df: pd.DataFrame) -> pd.Series:
        log_returns = np.log(df["close"]).diff()
        return log_returns.rolling(self.vol_window).std() * np.sqrt(self.periods_per_year)

    def target_weights(self, df: pd.DataFrame) -> pd.Series:
        """Fraction of capital to hold from each bar's close to the next.
        Zero until realized vol is defined, rather than guessing a size."""
        scale = (self.vol_target / self.realized_vol(df)).clip(upper=self.max_weight)
        weights = (self.ensemble_signal(df) * scale).clip(lower=0.0, upper=self.max_weight)
        return weights.fillna(0.0)

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        # no discrete entries: see target_weights / bot/backtest/exposure.py
        return None

    def diagnose(self, df: pd.DataFrame) -> dict:
        if len(df) < self.min_lookback:
            return {"ready": False}
        positions = self.model_positions(df).iloc[-1]
        vol = self.realized_vol(df).iloc[-1]
        return {
            "ready": True,
            "close": round(float(df["close"].iloc[-1]), 2),
            "models_long": f"{int(positions.sum())}/{len(positions)}",
            "long_lookbacks_days": ",".join(
                str(d) for d, n in zip(self.lookback_days, self.lookbacks) if positions[n] > 0
            ),
            "realized_vol_pct": round(float(vol * 100), 1) if pd.notna(vol) else None,
            "target_weight_pct": round(float(self.target_weights(df).iloc[-1] * 100), 1),
            "near_miss": False,
            "near_miss_key": None,
            "near_miss_reason": None,
        }

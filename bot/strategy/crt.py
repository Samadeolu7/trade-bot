import numpy as np
import pandas as pd

from bot.indicators.indicators import ema
from bot.strategy.base import Signal, Strategy


class CandleRangeTheoryStrategy(Strategy):
    """Candle Range Theory (CRT) — a liquidity-sweep reversal, popularised
    on YouTube (e.g. the Trading Geek channel). Added 2026-09-29 as a
    research candidate after seeing it pitched as a working strategy;
    nothing below has been validated on this project's data yet.

    The viral version leaves a lot to discretion (killzones, "key levels",
    lower-timeframe confirmation). This is the strictly mechanical core,
    with every judgment call pinned to an explicit, sweepable parameter:

    - Range candle: the prior bar. Its high/low is the range.
    - Sweep candle: the current bar trades beyond exactly ONE side of that
      range, then closes back inside it. A bar that sweeps both sides (an
      outside bar) is ambiguous and skipped; one that closes beyond the
      range is a breakout, not a CRT.
    - Swept the low, closed back inside -> long. Swept the high -> short.
    - Entry at the sweep candle's close (the first moment the pattern is
      known — no lookahead).
    - Stop just beyond the sweep wick (`stop_buffer_pct` of price past it).
    - Target: the opposite end of the range candle (`target: range`), or a
      fixed reward:risk multiple (`target: rr`, using `rr_multiple`).
    - Skip setups whose reward:risk to target is below `min_rr` — a sweep
      that closes right next to the opposite extreme has nothing left to
      capture.
    - Optional trend filter (`trend_ema_period`, 0 = off): longs only above
      the EMA, shorts only below — a mechanical stand-in for CRT's
      "higher-timeframe alignment" rule.

    Fixed stop and target, no trailing — that's how CRT is described, and
    it keeps this a genuinely different exit profile from the trend
    strategies here."""

    name = "crt"

    def __init__(self, params: dict):
        super().__init__(params)
        self.stop_buffer_pct = params.get("stop_buffer_pct", 0.0)
        self.target = params.get("target", "range")
        self.rr_multiple = params.get("rr_multiple", 2.0)
        self.min_rr = params.get("min_rr", 1.0)
        self.trend_ema_period = params.get("trend_ema_period", 0)
        if self.target not in ("range", "rr"):
            raise ValueError(f"crt target must be 'range' or 'rr', got {self.target!r}")
        # the pattern itself needs just the range bar + sweep bar; an EMA
        # filter needs enough history to have warmed up
        self.min_lookback = max(2, self.trend_ema_period * 3 if self.trend_ema_period else 0)

    def _setups(self, df: pd.DataFrame) -> pd.DataFrame:
        """Every bar's CRT read, vectorized: shared by entry_signals (whole
        series), generate_signal (last row) and diagnose, so the three can
        never disagree about what counts as a setup."""
        range_high = df["high"].shift(1)
        range_low = df["low"].shift(1)
        high, low, close = df["high"], df["low"], df["close"]

        inside = (close > range_low) & (close < range_high)
        swept_low = low < range_low
        swept_high = high > range_high
        bull = swept_low & ~swept_high & inside
        bear = swept_high & ~swept_low & inside

        long_stop = low * (1 - self.stop_buffer_pct)
        short_stop = high * (1 + self.stop_buffer_pct)
        long_risk = close - long_stop
        short_risk = short_stop - close

        if self.target == "range":
            long_tp, short_tp = range_high, range_low
        else:
            long_tp = close + self.rr_multiple * long_risk
            short_tp = close - self.rr_multiple * short_risk

        # risk can't be <= 0 for a real setup (the sweep wick is always
        # beyond the close), but guard the division anyway
        long_rr = (long_tp - close) / long_risk.where(long_risk > 0)
        short_rr = (close - short_tp) / short_risk.where(short_risk > 0)
        bull &= long_rr >= self.min_rr
        bear &= short_rr >= self.min_rr

        if self.trend_ema_period:
            trend = ema(close, self.trend_ema_period)
            bull &= close > trend
            bear &= close < trend

        return pd.DataFrame(
            {
                "bull": bull.fillna(False).astype(bool),
                "bear": bear.fillna(False).astype(bool),
                "range_high": range_high,
                "range_low": range_low,
                "long_stop": long_stop,
                "short_stop": short_stop,
                "long_tp": long_tp,
                "short_tp": short_tp,
                "long_rr": long_rr,
                "short_rr": short_rr,
            },
            index=df.index,
        )

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        s = self._setups(df)
        direction = np.where(s["bull"], "long", np.where(s["bear"], "short", None))
        return pd.DataFrame(
            {
                "direction": direction,
                "entry_price": np.where(direction != None, df["close"], np.nan),  # noqa: E711
                "stop_loss": np.where(s["bull"], s["long_stop"], np.where(s["bear"], s["short_stop"], np.nan)),
                "take_profit": np.where(s["bull"], s["long_tp"], np.where(s["bear"], s["short_tp"], np.nan)),
                "reason": np.where(
                    s["bull"],
                    "swept prior candle's low, closed back inside its range",
                    np.where(s["bear"], "swept prior candle's high, closed back inside its range", None),
                ),
            },
            index=df.index,
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_lookback:
            return None
        s = self._setups(df).iloc[-1]
        if not (s["bull"] or s["bear"]):
            return None
        side = "long" if s["bull"] else "short"
        close = float(df["close"].iloc[-1])
        context = {
            "range_high": round(float(s["range_high"]), 2),
            "range_low": round(float(s["range_low"]), 2),
            "reward_risk": round(float(s[f"{side}_rr"]), 2),
            "target_mode": self.target,
        }
        return Signal(
            symbol="",
            timeframe="",
            direction=side,
            entry_price=close,
            stop_loss=float(s[f"{side}_stop"]),
            take_profit=float(s[f"{side}_tp"]),
            reason=(
                f"swept prior candle's {'low' if side == 'long' else 'high'}, closed back inside its range"
            ),
            timestamp=df.index[-1],
            context=context,
        )

    def diagnose(self, df: pd.DataFrame) -> dict:
        """The NEXT bar's range is the latest bar's high/low — report that,
        plus whether the latest bar was a sweep that failed a filter (so a
        "why didn't it trade" question has an answer). No near-miss alerts:
        CRT forms and resolves within a single bar, so there's no "getting
        close" state worth an alert."""
        if len(df) < self.min_lookback:
            return {"ready": False}
        last = df.iloc[-1]
        prev = df.iloc[-2]
        swept_low = last["low"] < prev["low"]
        swept_high = last["high"] > prev["high"]
        closed_inside = prev["low"] < last["close"] < prev["high"]
        if swept_low and swept_high:
            last_bar = "outside bar (swept both sides) — skipped"
        elif (swept_low or swept_high) and not closed_inside:
            last_bar = "swept and closed beyond the range — breakout, not CRT"
        elif swept_low or swept_high:
            s = self._setups(df).iloc[-1]
            last_bar = "CRT setup" if (s["bull"] or s["bear"]) else "CRT shape but filtered (min_rr/trend)"
        else:
            last_bar = "no sweep"
        return {
            "ready": True,
            "close": round(float(last["close"]), 2),
            "next_range_high": round(float(last["high"]), 2),
            "next_range_low": round(float(last["low"]), 2),
            "last_bar": last_bar,
            "near_miss": False,
            "near_miss_key": None,
            "near_miss_reason": None,
        }

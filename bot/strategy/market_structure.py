import pandas as pd

from bot.indicators.indicators import atr
from bot.strategy.base import Direction, Signal, Strategy


class MarketStructureBreakoutStrategy(Strategy):
    """Market-structure breakout + retest (user's roadmap, Tier 1 #1) — a
    genuinely different entry hypothesis from every other strategy in this
    codebase, not another indicator variant. Every other strategy asks "what
    do the indicators say"; this asks "what is price actually doing":

    1. Find the most recent confirmed swing high/low (a `swing_window`-bar
       fractal — a bar whose high/low is the extreme of the `swing_window`
       bars on each side of it; only confirmable once that many bars have
       passed since).
    2. Wait for a close beyond that level (breakout).
    3. Wait for price to come back within `retest_tolerance_pct` of the
       level within `retest_window` bars (retest).
    4. Enter once the current bar closes back beyond the level again
       (rejection/continuation) — stop below/above the retest's own
       low/high, ATR trailing stop from there (no fixed target, same
       mechanism as ema_cross/donchian).

    Untested against real data — log any backtest findings in spec Section 8
    like the other strategies.

    Deliberately uses only the *most recent* confirmed swing point per
    direction, not every unresolved one — simpler and more defensible as a
    first version than tracking multiple concurrent watched levels, at the
    cost of possibly missing an older level's still-valid cycle. Worth
    revisiting if backtesting shows that costs real opportunities.

    No vectorized `entry_signals` override: this pattern is inherently
    sequential (find a level, then the first breakout after it, then the
    first retest after that) in a way that's straightforward to get right
    imperatively and easy to get subtly wrong trying to hand-vectorize.
    Correctness matters far more here than backtest speed, and the
    base-class fallback (replays generate_signal per bar) is still fast
    enough at this data scale (thousands of daily bars, not hundreds of
    thousands of hourly ones)."""

    name = "market_structure"

    def __init__(self, params: dict):
        super().__init__(params)
        self.swing_window = params.get("swing_window", 5)
        self.retest_window = params.get("retest_window", 10)
        self.retest_tolerance_pct = params.get("retest_tolerance_pct", 0.01)
        self.atr_period = params.get("atr_period", 14)
        self.atr_mult = params.get("atr_mult", 2.0)
        # room for: a swing point plus its confirmation lag on each side,
        # the breakout-to-retest span, and the ATR trailing stop's own warm-up
        self.min_lookback = self.swing_window * 4 + self.retest_window + self.atr_period * 3

    def _find_confirmed_swings(self, df: pd.DataFrame) -> tuple[list[int], list[int]]:
        """Positional indices of confirmed swing highs/lows within df — a
        point needs `swing_window` bars on both sides, so only ones that far
        from either edge can be confirmed yet."""
        n = len(df)
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        w = self.swing_window
        swing_highs = []
        swing_lows = []
        for i in range(w, n - w):
            if high[i] == high[i - w : i + w + 1].max():
                swing_highs.append(i)
            if low[i] == low[i - w : i + w + 1].min():
                swing_lows.append(i)
        return swing_highs, swing_lows

    def _check_direction(self, direction, close, low, high, timestamp, swing_points, last_idx):
        if not swing_points:
            return None
        level_idx = swing_points[-1]
        level_price = high[level_idx] if direction == "long" else low[level_idx]

        breakout_idx = None
        for i in range(level_idx + 1, last_idx + 1):
            if direction == "long" and close[i] > level_price:
                breakout_idx = i
                break
            if direction == "short" and close[i] < level_price:
                breakout_idx = i
                break
        if breakout_idx is None or breakout_idx >= last_idx:
            return None  # no breakout yet, or it just happened this bar — no time to retest

        tolerance = level_price * self.retest_tolerance_pct
        retest_idx = None
        window_end = min(breakout_idx + self.retest_window, last_idx)
        for i in range(breakout_idx + 1, window_end + 1):
            if direction == "long" and low[i] <= level_price + tolerance:
                retest_idx = i
                break
            if direction == "short" and high[i] >= level_price - tolerance:
                retest_idx = i
                break
        if retest_idx is None:
            return None

        entry = close[last_idx]
        if direction == "long" and entry > level_price:
            stop = low[retest_idx : last_idx + 1].min()
            return Signal(
                symbol="", timeframe="", direction="long", entry_price=entry, stop_loss=stop,
                take_profit=None, reason=f"swing high {level_price:.2f} broken, retested, rejected",
                timestamp=timestamp,
            )
        if direction == "short" and entry < level_price:
            stop = high[retest_idx : last_idx + 1].max()
            return Signal(
                symbol="", timeframe="", direction="short", entry_price=entry, stop_loss=stop,
                take_profit=None, reason=f"swing low {level_price:.2f} broken, retested, rejected",
                timestamp=timestamp,
            )
        return None

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_lookback:
            return None

        swing_highs, swing_lows = self._find_confirmed_swings(df)
        last_idx = len(df) - 1
        close = df["close"].to_numpy()
        low = df["low"].to_numpy()
        high = df["high"].to_numpy()
        timestamp = df.index[last_idx]

        long_signal = self._check_direction("long", close, low, high, timestamp, swing_highs, last_idx)
        if long_signal is not None:
            return long_signal
        return self._check_direction("short", close, low, high, timestamp, swing_lows, last_idx)

    def trail_stop(self, df: pd.DataFrame, direction: Direction, current_stop: float) -> float:
        atr_val = atr(df["high"], df["low"], df["close"], self.atr_period)
        last_atr = atr_val.iloc[-1]
        if pd.isna(last_atr):
            return current_stop
        last_close = df["close"].iloc[-1]
        if direction == "long":
            return max(current_stop, last_close - self.atr_mult * last_atr)
        return min(current_stop, last_close + self.atr_mult * last_atr)

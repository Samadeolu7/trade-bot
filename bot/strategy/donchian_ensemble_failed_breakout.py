import pandas as pd

from bot.strategy.base import Signal, Strategy
from bot.strategy.donchian_ensemble import DonchianEnsembleStrategy
from bot.strategy.failed_breakout_signal import min_lookback_for, scan_bearish_failed_breakouts

# donchian_ensemble is long-only (see its own docstring), so the only
# failed-breakout direction that can matter to its risk is the bearish one
# (a resistance swept, reclaimed, and broken down against a held long) —
# neither wrapper below looks at the bullish mirror image.


class DonchianEnsembleFailedBreakoutFilterStrategy(Strategy):
    """Hypothesis B (family `reversal/failed-breakout-filter`,
    pre-registered 2026-10-25, notes/research_log.md): stays flat instead
    of *increasing* exposure on a bar where a bearish failed breakout
    (resistance swept, reclaimed, structure broken down — see
    failed_breakout_signal.py) completed within `veto_lookback` bars. Never
    forces an exit on an existing position — only suppresses opening or
    adding to one. Closing an existing position early is a different
    mechanism (hypothesis C, DonchianEnsembleFailedBreakoutExitStrategy),
    deliberately not folded in here so the two effects aren't conflated.

    Untested — a new research candidate."""

    name = "donchian_ensemble_fb_filter"

    def __init__(self, base: DonchianEnsembleStrategy, params: dict):
        super().__init__(params)
        self.base = base
        self.swing_window = params.get("swing_window", 5)
        self.reclaim_window = params.get("reclaim_window", 5)
        self.follow_through_window = params.get("follow_through_window", 10)
        self.veto_lookback = params.get("veto_lookback", 10)
        self.rebalance_threshold = base.rebalance_threshold
        self.min_lookback = max(
            base.min_lookback,
            min_lookback_for(self.swing_window, self.reclaim_window, self.follow_through_window) + self.veto_lookback,
        )

    def _veto_mask(self, df: pd.DataFrame) -> pd.Series:
        events = scan_bearish_failed_breakouts(df, self.swing_window, self.reclaim_window, self.follow_through_window)
        return (
            events["follow_through"]
            .rolling(self.veto_lookback, min_periods=1)
            .max()
            .fillna(0)
            .astype(bool)
        )

    def target_weights(self, df: pd.DataFrame) -> pd.Series:
        base_weights = self.base.target_weights(df)
        veto = self._veto_mask(df).reindex(df.index).fillna(False)
        out = []
        held = 0.0
        for ts in df.index:
            want = float(base_weights.loc[ts])
            if veto.loc[ts] and want > held:
                want = held  # block the increase; a decrease/exit still passes through
            held = want
            out.append(want)
        return pd.Series(out, index=df.index)

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        return None


class DonchianEnsembleFailedBreakoutExitStrategy(Strategy):
    """Hypothesis C (family `reversal/failed-breakout-exit`,
    pre-registered 2026-10-25, notes/research_log.md): closes an *open*
    position immediately — pre-empting the ensemble's own ATR/channel
    exit for that bar — when a bearish failed breakout fires against the
    held long direction. Applied to the bare ensemble, not hypothesis B's
    filtered version, so the exit effect isn't conflated with the
    entry-veto effect; a combined variant is a follow-up, not this.

    `exit_trigger` controls how early the exit fires:
    - "reclaim": as soon as price closes back below the swept resistance —
      earlier, cheaper confirmation, more exposed to whipsaw.
    - "follow_through" (default): only once structure is also broken down —
      later, more confirmed, gives back more of the move.

    Untested — a new research candidate."""

    name = "donchian_ensemble_fb_exit"

    def __init__(self, base: DonchianEnsembleStrategy, params: dict):
        super().__init__(params)
        self.base = base
        self.swing_window = params.get("swing_window", 5)
        self.reclaim_window = params.get("reclaim_window", 5)
        self.follow_through_window = params.get("follow_through_window", 10)
        self.exit_trigger = params.get("exit_trigger", "follow_through")
        if self.exit_trigger not in ("reclaim", "follow_through"):
            raise ValueError(f"exit_trigger must be 'reclaim' or 'follow_through', got {self.exit_trigger!r}")
        self.rebalance_threshold = base.rebalance_threshold
        self.min_lookback = max(
            base.min_lookback,
            min_lookback_for(self.swing_window, self.reclaim_window, self.follow_through_window),
        )

    def _exit_mask(self, df: pd.DataFrame) -> pd.Series:
        events = scan_bearish_failed_breakouts(df, self.swing_window, self.reclaim_window, self.follow_through_window)
        return events[self.exit_trigger]

    def target_weights(self, df: pd.DataFrame) -> pd.Series:
        base_weights = self.base.target_weights(df)
        exit_now = self._exit_mask(df).reindex(df.index).fillna(False)
        out = base_weights.copy()
        out[exit_now] = 0.0
        return out

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        return None

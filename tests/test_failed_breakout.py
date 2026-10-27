import pandas as pd

from bot.strategy.donchian_ensemble_failed_breakout import (
    DonchianEnsembleFailedBreakoutExitStrategy,
    DonchianEnsembleFailedBreakoutFilterStrategy,
)
from bot.strategy.failed_breakout import FailedBreakoutStrategy
from bot.strategy.failed_breakout_signal import scan_bearish_failed_breakouts

SWING_WINDOW = 2
RECLAIM_WINDOW = 3
FOLLOW_THROUGH_WINDOW = 3

# A hand-built bearish failed breakout: a confirmed swing low at idx 2
# (structure), a confirmed swing high at idx 8 (the resistance), a close
# above it at idx 11 (break), a close back below it at idx 12 (reclaim),
# and a close below the idx-2 structure low at idx 14 (follow-through).
_ROWS = [
    # open, high, low, close
    (100, 101, 99, 100),
    (101, 102, 100, 101),
    (95, 96, 90, 95),     # structure low (idx 2)
    (101, 102, 99, 101),
    (100, 101, 99, 100),
    (102, 103, 101, 102),
    (103, 103, 102, 103),
    (104, 104, 103, 104),
    (106, 108, 105, 106),  # resistance high (idx 8)
    (105, 105, 104, 105),
    (107, 107, 106, 107),  # pivot confirmed here, no breakout yet
    (109, 110, 108, 109),  # break (close 109 > 108)
    (108, 109, 107, 107),  # reclaim (close 107 < 108)
    (96, 97, 95, 95),      # not yet below structure low (90)
    (90, 91, 88, 89),      # follow-through (close 89 < 90)
    (89, 90, 87, 88),
    (88, 89, 86, 87),
]


def _df() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(_ROWS), freq="4h", tz="UTC")
    return pd.DataFrame(_ROWS, columns=["open", "high", "low", "close"], index=index)


def test_scan_detects_reclaim_and_follow_through_on_the_right_bars():
    df = _df()
    events = scan_bearish_failed_breakouts(df, SWING_WINDOW, RECLAIM_WINDOW, FOLLOW_THROUGH_WINDOW)

    assert list(events.index[events["reclaim"]]) == [df.index[12]]
    assert list(events.index[events["follow_through"]]) == [df.index[14]]
    assert events["sweep_extreme"].iloc[14] == 110.0


def test_scan_abandons_a_genuine_breakout_that_never_reclaims():
    df = _df().copy()
    # close stays above the level for longer than reclaim_window allows
    # (idx 12-14, all > reclaim_window bars past the idx-11 break) — a real
    # breakout, not a failure
    for i in (12, 13, 14):
        df.loc[df.index[i], ["high", "low", "close"]] = [112, 109, 111]
    events = scan_bearish_failed_breakouts(df, SWING_WINDOW, RECLAIM_WINDOW, FOLLOW_THROUGH_WINDOW)
    assert not events["reclaim"].any()
    assert not events["follow_through"].any()


def test_failed_breakout_strategy_enters_short_on_follow_through():
    df = _df()
    strategy = FailedBreakoutStrategy(
        {"swing_window": SWING_WINDOW, "reclaim_window": RECLAIM_WINDOW, "follow_through_window": FOLLOW_THROUGH_WINDOW}
    )
    signals = strategy.entry_signals(df)
    fired = signals[signals["direction"].notna()]
    assert list(fired.index) == [df.index[14]]
    row = fired.iloc[0]
    assert row["direction"] == "short"
    assert row["stop_loss"] == 110.0


class _FakeBase:
    """Minimal stand-in for DonchianEnsembleStrategy: the wrappers only
    read target_weights(df), rebalance_threshold, min_lookback and name."""

    name = "fake_base"

    def __init__(self, weights: pd.Series):
        self._weights = weights
        self.rebalance_threshold = 0.0
        self.min_lookback = 1

    def target_weights(self, df: pd.DataFrame) -> pd.Series:
        return self._weights.reindex(df.index)


def test_filter_blocks_new_exposure_during_veto_window_but_not_an_exit():
    df = _df()
    # base wants full exposure throughout, then wants to cut to zero right
    # after follow-through, then wants back in a few bars later
    base_weights = pd.Series(1.0, index=df.index)
    base_weights.iloc[15] = 0.0  # an exit the base strategy itself makes
    base_weights.iloc[16] = 1.0  # wants back in, still inside the veto window

    strategy = DonchianEnsembleFailedBreakoutFilterStrategy(
        _FakeBase(base_weights),
        {"swing_window": SWING_WINDOW, "reclaim_window": RECLAIM_WINDOW,
         "follow_through_window": FOLLOW_THROUGH_WINDOW, "veto_lookback": 10},
    )
    out = strategy.target_weights(df)

    # follow-through fires at idx 14; exposure was already on (1.0) before
    # that, so the veto (blocks INCREASES only) doesn't touch it
    assert out.iloc[14] == 1.0
    # the base's own exit at idx 15 still passes through
    assert out.iloc[15] == 0.0
    # but re-adding exposure at idx 16, still within the veto window, is blocked
    assert out.iloc[16] == 0.0


def test_exit_closes_an_open_position_on_follow_through():
    df = _df()
    base_weights = pd.Series(1.0, index=df.index)  # base holds full exposure throughout
    strategy = DonchianEnsembleFailedBreakoutExitStrategy(
        _FakeBase(base_weights),
        {"swing_window": SWING_WINDOW, "reclaim_window": RECLAIM_WINDOW,
         "follow_through_window": FOLLOW_THROUGH_WINDOW, "exit_trigger": "follow_through"},
    )
    out = strategy.target_weights(df)
    assert out.iloc[13] == 1.0
    assert out.iloc[14] == 0.0  # forced flat exactly on the follow-through bar


def test_exit_trigger_reclaim_fires_earlier_than_follow_through():
    df = _df()
    base_weights = pd.Series(1.0, index=df.index)
    strategy = DonchianEnsembleFailedBreakoutExitStrategy(
        _FakeBase(base_weights),
        {"swing_window": SWING_WINDOW, "reclaim_window": RECLAIM_WINDOW,
         "follow_through_window": FOLLOW_THROUGH_WINDOW, "exit_trigger": "reclaim"},
    )
    out = strategy.target_weights(df)
    assert out.iloc[12] == 0.0  # reclaim bar, earlier than follow-through's idx 14

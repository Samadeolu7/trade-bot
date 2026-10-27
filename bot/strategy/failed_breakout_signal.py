"""Shared failed-breakout (location/break/reclaim/follow-through) event
detection, used by FailedBreakoutStrategy (hypothesis A, standalone) and the
donchian_ensemble filter/exit wrappers (hypotheses B/C). Pre-registered
2026-10-25 in notes/research_log.md; every judgment call below is pinned to
an explicit parameter, same spirit as crt.py.

Definitions (fixed before any run, per the pre-registration):
- Location: a confirmed swing high/low, a `swing_window`-bar fractal (the
  same rule market_structure.py uses for its own swing detection, kept as
  an independent copy here rather than imported, so the two research
  candidates don't become coupled).
- Break: the first close beyond that level after it's confirmed.
- Reclaim: a close back on the origin side of the level, within
  `reclaim_window` bars of the break. If price never closes back within
  the window, the move is treated as a genuine breakout, not a failure,
  and the setup is abandoned (this is what filters out "repeated sweeps"
  and real trending breakouts per the research notes).
- Follow-through: within `follow_through_window` bars of the reclaim, a
  close beyond the nearest confirmed swing point on the reclaim's new
  (reversed) side that existed before the break — the structure being
  "broken" by the reversal.

Only the most recent confirmed swing is ever tracked as the active level,
and at most one setup is tracked at a time per direction — the same
simplification market_structure.py documents for itself."""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class _Pivots:
    is_high: np.ndarray
    is_low: np.ndarray


def _pivots(df: pd.DataFrame, window: int) -> _Pivots:
    w = window
    high_max = df["high"].rolling(2 * w + 1, center=True, min_periods=2 * w + 1).max()
    low_min = df["low"].rolling(2 * w + 1, center=True, min_periods=2 * w + 1).min()
    return _Pivots(
        is_high=(df["high"] == high_max).fillna(False).to_numpy(),
        is_low=(df["low"] == low_min).fillna(False).to_numpy(),
    )


def _scan(df: pd.DataFrame, window: int, reclaim_window: int, follow_through_window: int,
         level_is_high: bool) -> pd.DataFrame:
    """One forward pass. `level_is_high=True` scans for a failed breakout
    ABOVE a swing high (a bearish reversal, "short" direction); False scans
    for a failed breakdown BELOW a swing low (a bullish reversal, "long").
    Returns a frame aligned to df.index with boolean columns 'reclaim' and
    'follow_through' (True exactly on the confirming bar) and a float
    column 'sweep_extreme' (the break's wick peak, valid on follow_through
    bars, for stop placement)."""
    n = len(df)
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    w = window
    piv = _pivots(df, w)
    level_pivot = piv.is_high if level_is_high else piv.is_low
    structure_pivot = piv.is_low if level_is_high else piv.is_high
    level_price_source = high if level_is_high else low
    structure_price_source = low if level_is_high else high

    structure_points: list[int] = []
    reclaim = np.zeros(n, dtype=bool)
    follow_through = np.zeros(n, dtype=bool)
    sweep_extreme = np.full(n, np.nan)

    level_idx: int | None = None
    state = "idle"  # idle -> broken -> reclaimed -> idle
    breakout_idx: int | None = None
    reclaim_idx: int | None = None
    sweep_peak = np.nan
    structure_idx: int | None = None

    for t in range(n):
        confirm_t = t - w
        if confirm_t >= 0:
            if structure_pivot[confirm_t]:
                structure_points.append(confirm_t)
            if level_pivot[confirm_t] and state == "idle":
                level_idx = confirm_t

        if state == "idle":
            if level_idx is not None:
                broke = close[t] > level_price_source[level_idx] if level_is_high \
                    else close[t] < level_price_source[level_idx]
                if broke:
                    state, breakout_idx = "broken", t
                    sweep_peak = high[t] if level_is_high else low[t]
        elif state == "broken":
            sweep_peak = max(sweep_peak, high[t]) if level_is_high else min(sweep_peak, low[t])
            reclaimed = close[t] < level_price_source[level_idx] if level_is_high \
                else close[t] > level_price_source[level_idx]
            if reclaimed:
                reclaim[t] = True
                state, reclaim_idx = "reclaimed", t
                structure_idx = structure_points[-1] if structure_points else None
            elif t - breakout_idx >= reclaim_window:
                state, level_idx = "idle", None
        elif state == "reclaimed":
            followed = (
                structure_idx is not None
                and (close[t] < structure_price_source[structure_idx] if level_is_high
                     else close[t] > structure_price_source[structure_idx])
            )
            if followed:
                follow_through[t] = True
                sweep_extreme[t] = sweep_peak
                state, level_idx = "idle", None
            elif t - reclaim_idx >= follow_through_window:
                state, level_idx = "idle", None

    return pd.DataFrame(
        {"reclaim": reclaim, "follow_through": follow_through, "sweep_extreme": sweep_extreme},
        index=df.index,
    )


def scan_bearish_failed_breakouts(df: pd.DataFrame, swing_window: int, reclaim_window: int,
                                  follow_through_window: int) -> pd.DataFrame:
    """A swing high (resistance) swept, reclaimed below, then structure
    broken down — the pattern relevant to a long-only strategy's risk."""
    return _scan(df, swing_window, reclaim_window, follow_through_window, level_is_high=True)


def scan_bullish_failed_breakdowns(df: pd.DataFrame, swing_window: int, reclaim_window: int,
                                   follow_through_window: int) -> pd.DataFrame:
    """A swing low (support) swept, reclaimed above, then structure broken
    up — the mirror image, needed only by the standalone two-sided strategy."""
    return _scan(df, swing_window, reclaim_window, follow_through_window, level_is_high=False)


def min_lookback_for(swing_window: int, reclaim_window: int, follow_through_window: int,
                     extra: int = 0) -> int:
    """Room for: a swing point plus its confirmation lag on each side, a
    second swing point for the follow-through's structure, then the
    break-to-reclaim and reclaim-to-follow-through spans."""
    return swing_window * 4 + reclaim_window + follow_through_window + extra

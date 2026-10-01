"""Data health checks run before any signal is made from a candle series.
A strategy fed stale, gappy, duplicated or corrupt candles produces calls
that look normal but aren't, so these failures stop signals instead of
being traded through. A large single-bar move is only a warning: a real
crash looks the same as a bad print, and suppressing on it would hide
exactly the exits that matter."""

from dataclasses import dataclass, field
from datetime import datetime

import ccxt
import numpy as np
import pandas as pd

RECENT_BARS = 120  # how far back gaps and bad candles are looked for
STALE_PERIODS = 2  # the last completed bar may be at most this many periods old
JUMP_WARNING = 0.25  # a single-bar close-to-close move above this is flagged


@dataclass
class HealthReport:
    problems: list[str] = field(default_factory=list)  # blocking: no signals from this data
    warnings: list[str] = field(default_factory=list)  # shown, not blocking

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        return "; ".join(self.problems)


def check_series(df: pd.DataFrame, timeframe: str, now: datetime | None = None) -> HealthReport:
    """`df`: completed candles only (after drop_incomplete_bar), indexed by
    UTC bar open time."""
    report = HealthReport()
    if len(df) == 0:
        report.problems.append("no candles stored")
        return report
    period = pd.Timedelta(seconds=ccxt.Exchange.parse_timeframe(timeframe))
    now_ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")

    closed_at = df.index[-1] + period
    age = now_ts - closed_at
    if age > STALE_PERIODS * period:
        report.problems.append(
            f"stale data: the last completed {timeframe} candle closed {_hours(age)} ago "
            f"(at {closed_at:%Y-%m-%d %H:%M} UTC)")

    recent = df.iloc[-RECENT_BARS:]
    dupes = int(recent.index.duplicated().sum())
    if dupes:
        report.problems.append(f"{dupes} duplicate candle(s) in the last {len(recent)} bars")
    steps = pd.Series(recent.index).diff().dropna()
    missing = int(sum(max(int(s / period) - 1, 0) for s in steps if s > period))
    if missing:
        report.problems.append(f"{missing} missing candle(s) in the last {len(recent)} bars")
    if (steps < period).any() and not dupes:
        report.problems.append("candles closer together than one period (misaligned timestamps)")

    ohlc = recent[["open", "high", "low", "close"]]
    if ohlc.isna().any().any() or not np.isfinite(ohlc.to_numpy(dtype=float)).all():
        report.problems.append("missing or non-numeric prices in recent candles")
    elif (ohlc <= 0).any().any():
        report.problems.append("zero or negative prices in recent candles")
    else:
        bad = (ohlc["high"] < ohlc[["open", "close"]].max(axis=1)) | (ohlc["low"] > ohlc[["open", "close"]].min(axis=1))
        if bad.any():
            report.problems.append(f"{int(bad.sum())} candle(s) with high/low outside open/close")
        jumps = ohlc["close"].pct_change().abs()
        if (jumps > JUMP_WARNING).any():
            at = jumps.idxmax()
            report.warnings.append(f"{jumps.max():.0%} single-bar move at {at:%Y-%m-%d %H:%M} UTC")
    return report


def _hours(delta: pd.Timedelta) -> str:
    hours = delta.total_seconds() / 3600
    return f"{hours / 24:.1f} days" if hours >= 48 else f"{hours:.1f} hours"

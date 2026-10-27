"""Intraday (day-trading) backtests on 15-minute candles, for the
pre-registered hypotheses #2-#5 in notes/research_log.md.

Every trade is entered and closed inside the owner's trading window
(weekdays 07:00-21:00 UTC, weekends 00:00-23:45 UTC), so nothing is held
overnight. Entries fill at a 15m close; a stop fills at the stop price, or
at the bar's open if price gapped through it; otherwise the trade exits at
the close of the last bar in the window (or its time limit). Returns are
per trade at 1x notional, net of a round-trip cost."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

BAR = pd.Timedelta(minutes=15)


@dataclass
class DayTrade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int  # +1 long, -1 short
    entry: float
    exit: float
    reason: str

    def gross(self) -> float:
        return self.direction * (self.exit / self.entry - 1)


def window(day: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(first bar open, last bar open) of the trading window on `day`."""
    if day.weekday() >= 5:
        return day, day + pd.Timedelta(hours=23, minutes=30)  # last bar closes 23:45
    return day + pd.Timedelta(hours=7), day + pd.Timedelta(hours=20, minutes=45)  # closes 21:00


class Bars:
    """Fast positional access to a 15m frame by timestamp."""

    def __init__(self, df: pd.DataFrame):
        self.index = df.index
        self.o, self.h, self.l, self.c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        self.pos = {ts: i for i, ts in enumerate(df.index)}

    def at(self, ts) -> int | None:
        return self.pos.get(ts)

    def days(self):
        return pd.DatetimeIndex(self.index.normalize().unique())


def exit_scan(b: Bars, i_entry: int, direction: int, stop: float | None, i_last: int) -> tuple[int, float, str]:
    """From the bar after entry to `i_last`: the stop if touched, else the
    close of `i_last`."""
    for j in range(i_entry + 1, i_last + 1):
        if stop is not None:
            if direction > 0 and b.l[j] <= stop:
                return j, min(b.o[j], stop), "stop"
            if direction < 0 and b.h[j] >= stop:
                return j, max(b.o[j], stop), "stop"
    return i_last, b.c[i_last], "time"


def _trade(b: Bars, i: int, j: int, d: int, px_exit: float, reason: str) -> DayTrade:
    return DayTrade(b.index[i], b.index[j], d, b.c[i], px_exit, reason)


# --- #2 US-open range breakout ----------------------------------------------------------


def orb(df: pd.DataFrame, range_minutes: int = 30, stop_at: str = "opposite") -> list[DayTrade]:
    b, out = Bars(df), []
    n_range = range_minutes // 15
    for day in b.days():
        if day.weekday() >= 5:
            continue
        first = b.at(day + pd.Timedelta(hours=13, minutes=30))
        last = b.at(window(day)[1])
        cutoff = b.at(day + pd.Timedelta(hours=18, minutes=45))  # entry candle must close by 19:00
        if first is None or last is None or cutoff is None or b.index[first + n_range - 1] != day + pd.Timedelta(
                hours=13, minutes=30) + BAR * (n_range - 1):
            continue
        hi, lo = b.h[first:first + n_range].max(), b.l[first:first + n_range].min()
        mid = (hi + lo) / 2
        for i in range(first + n_range, cutoff + 1):
            d = 1 if b.c[i] > hi else -1 if b.c[i] < lo else 0
            if d:
                stop = mid if stop_at == "mid" else (lo if d > 0 else hi)
                j, px, why = exit_scan(b, i, d, stop, last)
                out.append(_trade(b, i, j, d, px, why))
                break
    return out


# --- #3 volatility breakout ---------------------------------------------------------------


def vol_breakout(df: pd.DataFrame, k: float = 0.5, both: bool = False) -> list[DayTrade]:
    b, out = Bars(df), []
    for day in b.days():
        anchor = b.at(day + pd.Timedelta(hours=7))
        last = b.at(window(day)[1])
        if anchor is None or last is None or anchor < 96:
            continue
        rng = b.h[anchor - 96:anchor].max() - b.l[anchor - 96:anchor].min()
        o = b.o[anchor]
        for i in range(anchor, last):
            d = 1 if b.c[i] >= o + k * rng else (-1 if both and b.c[i] <= o - k * rng else 0)
            if d:
                j, px, why = exit_scan(b, i, d, o, last)
                out.append(_trade(b, i, j, d, px, why))
                break
    return out


# --- #4 intraday momentum -----------------------------------------------------------------


def momentum(df: pd.DataFrame, formation_start_hour: int = 7) -> list[DayTrade]:
    b, out = Bars(df), []
    for day in b.days():
        weekend = day.weekday() >= 5
        enter_bar = day + (pd.Timedelta(hours=21, minutes=30) if weekend else pd.Timedelta(hours=18, minutes=45))
        start, i, last = b.at(day + pd.Timedelta(hours=formation_start_hour)), b.at(enter_bar), b.at(window(day)[1])
        if start is None or i is None or last is None:
            continue
        r = b.c[i] / b.o[start] - 1
        if r == 0:
            continue
        d = 1 if r > 0 else -1
        j, px, why = exit_scan(b, i, d, None, last)
        out.append(_trade(b, i, j, d, px, why))
    return out


# --- #5 overreaction fade -----------------------------------------------------------------


def fade(df: pd.DataFrame, k: float = 2.5, both: bool = False, hold_bars: int = 16) -> list[DayTrade]:
    b, out = Bars(df), []
    c = pd.Series(b.c, index=b.index)
    r1h = c / c.shift(4) - 1
    sigma = r1h.rolling(30 * 96, min_periods=10 * 96).std().shift(1).to_numpy()
    r = r1h.to_numpy()
    day_of = b.index.normalize()
    busy_until = -1
    for i in range(len(b.c)):
        if i <= busy_until or np.isnan(sigma[i]) or np.isnan(r[i]):
            continue
        start, end = window(day_of[i])
        if not (start <= b.index[i] < end):
            continue
        if r[i] < -k * sigma[i]:
            d = 1
        elif both and r[i] > k * sigma[i]:
            d = -1
        else:
            continue
        move = abs(b.c[i] - b.c[i - 4])
        stop = b.c[i] - d * move
        last = b.at(end)
        if last is None:
            continue
        j, px, why = exit_scan(b, i, d, stop, min(i + hold_bars, last))
        out.append(_trade(b, i, j, d, px, why))
        busy_until = j
    return out


# --- results ------------------------------------------------------------------------------


def daily_returns(trades: list[DayTrade], days: pd.DatetimeIndex, cost_rt: float) -> pd.Series:
    """Net return per calendar day (by exit day), zero on days without trades."""
    s = pd.Series(0.0, index=days)
    for t in trades:
        s[t.exit_time.normalize()] += t.gross() - cost_rt
    return s

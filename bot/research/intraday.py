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


# --- existing strategies as day trades (hypothesis #6) ----------------------------------


def _windows(index: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    """Per bar: whether a new position may open on it (it closes before the
    window closes), and whether it's the window's last bar (forced exit at
    its close). Works for 15m and 1h bars."""
    days = index.normalize()
    weekend = days.weekday >= 5
    start = days + pd.to_timedelta(np.where(weekend, 0, 7 * 60), unit="m")
    close_at = days + pd.to_timedelta(np.where(weekend, 23 * 60 + 45, 21 * 60), unit="m")
    span = (index[1] - index[0]) if len(index) > 1 else BAR
    bar_close = index + span
    inside = index >= start
    can_open = np.asarray(inside & (bar_close < close_at))
    last = np.asarray(inside & (bar_close >= close_at) & (index < close_at))
    return can_open, last


def session_signal_trades(df: pd.DataFrame, strategy) -> list[DayTrade]:
    """A signal strategy's own entries, stops, trailing stops and targets,
    restricted to the owner's window and flat at each window's end."""
    signals = strategy.entry_signals(df)
    lookback = strategy.min_lookback
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    sdir = signals["direction"].to_numpy(dtype=object)
    sstop = signals["stop_loss"].to_numpy(dtype=float)
    stp = signals["take_profit"].to_numpy(dtype=float)
    can_open, last = _windows(df.index)
    out, pos = [], None
    for i in range(lookback, len(df)):
        if pos is not None:
            win = df.iloc[max(0, i - lookback + 1): i + 1]
            pos["stop"] = pos["owner"].trail_stop(win, pos["direction"], pos["stop"])
            d = 1 if pos["direction"] == "long" else -1
            exit_px, why = None, None
            if d > 0 and l[i] <= pos["stop"]:
                exit_px, why = min(o[i], pos["stop"]), "stop"
            elif d < 0 and h[i] >= pos["stop"]:
                exit_px, why = max(o[i], pos["stop"]), "stop"
            elif pos["tp"] is not None and ((d > 0 and h[i] >= pos["tp"]) or (d < 0 and l[i] <= pos["tp"])):
                exit_px, why = pos["tp"], "take_profit"
            elif last[i]:
                exit_px, why = c[i], "time"
            if exit_px is not None:
                out.append(DayTrade(pos["time"], df.index[i], d, pos["entry"], float(exit_px), why))
                pos = None
            continue
        if can_open[i] and isinstance(sdir[i], str) and sdir[i] in ("long", "short") and not np.isnan(sstop[i]):
            owner = signals["strategy"].iloc[i] if "strategy" in signals.columns else strategy
            pos = {"direction": sdir[i], "entry": c[i], "stop": float(sstop[i]), "time": df.index[i],
                   "tp": None if np.isnan(stp[i]) else float(stp[i]), "owner": owner}
    return out


def session_exposure_returns(df: pd.DataFrame, strategy, cost_rt: float) -> tuple[pd.Series, list[pd.Timestamp]]:
    """An exposure strategy's target weight, held only inside the window
    (entered at its start, flat at its end). Returns net return per
    calendar day and the entry times (flat -> invested)."""
    w = strategy.target_weights(df).fillna(0.0).to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    can_open, last = _windows(df.index)
    thr = getattr(strategy, "rebalance_threshold", 0.0)
    day = df.index.normalize()
    rets = np.zeros(len(df))
    entries, held = [], 0.0
    for i in range(1, len(df)):
        r = held * (c[i] / c[i - 1] - 1)
        target = w[i] if can_open[i] else 0.0
        if last[i] or not can_open[i]:
            target = 0.0
        if (held == 0.0) != (target == 0.0) or abs(target - held) > thr:
            if held == 0.0 and target > 0.0:
                entries.append(df.index[i])
            r -= abs(target - held) * cost_rt / 2
            held = target
        rets[i] = r
    daily = pd.Series(rets, index=day).groupby(level=0).sum()
    return daily, entries

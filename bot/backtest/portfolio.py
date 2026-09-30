"""Multi-coin rotational backtest for fraction-of-capital strategies, after
the portfolio in Zarattini, Pagani & Barbon, "Catching Crypto Trends"
(2025): each month the universe is re-selected point-in-time by trailing
dollar volume, capital is split into equal slots, and each held coin gets
slot x its own strategy weight (e.g. the donchian_ensemble target).

Same accounting as bot/backtest/exposure.py, generalised to many assets:
at each bar's close, every holding drifts with its coin's return, then
each coin is rebalanced to its target, paying fee + slippage on the traded
notional. A weight computed at bar t's close earns bar t+1's return."""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from bot.backtest.engine import Trade


@dataclass
class PortfolioResult:
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    trades: list[Trade] = field(default_factory=list)  # per-coin exposure episodes
    # month start -> coins selected for that month
    universe: dict[str, list[str]] = field(default_factory=dict)
    exposure: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))  # total fraction invested


def daily_dollar_volume(df: pd.DataFrame) -> pd.Series:
    return (df["close"] * df["volume"]).resample("D").sum()


def select_universe(frames: dict[str, pd.DataFrame], month_starts: pd.DatetimeIndex, size: int,
                    min_history_days: int = 365, volume_days: int = 30) -> dict[pd.Timestamp, list[str]]:
    """For each month start, the `size` coins with at least
    `min_history_days` of history ranked by median daily dollar volume over
    the prior `volume_days`. Uses only data before the month start."""
    volumes = {sym: daily_dollar_volume(df) for sym, df in frames.items() if len(df)}
    first_seen = {sym: df.index[0] for sym, df in frames.items() if len(df)}
    out = {}
    for start in month_starts:
        ranked = []
        for sym, vol in volumes.items():
            if start - first_seen[sym] < pd.Timedelta(days=min_history_days):
                continue
            window = vol[(vol.index < start.normalize()) & (vol.index >= start.normalize() - pd.Timedelta(days=volume_days))]
            window = window[window > 0]  # zero-volume days (halted, delisted) aren't trading days
            if len(window) < volume_days // 2:
                continue  # delisted or a data gap: not tradable this month
            ranked.append((float(window.median()), sym))
        ranked.sort(reverse=True)
        out[start] = [sym for _, sym in ranked[:size]]
    return out


def run_portfolio_backtest(
    frames: dict[str, pd.DataFrame],
    weights: dict[str, pd.Series],
    window_index: pd.DatetimeIndex,
    size: int,
    fee: float = 0.001,
    slippage: float = 0.0005,
    initial_capital: float = 10_000.0,
    rebalance_threshold: float = 0.1,
    min_history_days: int = 365,
) -> PortfolioResult:
    """`frames`: full candle history per coin (for volume ranking and
    prices). `weights`: each coin's own strategy target (0..1), computed on
    its full history, so warmup happens before the window. `window_index`:
    the bars to simulate. `rebalance_threshold` is relative to one slot."""
    if len(window_index) == 0:
        return PortfolioResult()
    symbols = sorted(frames)
    closes = pd.DataFrame({s: frames[s]["close"] for s in symbols}).reindex(window_index)
    targets = pd.DataFrame({s: weights[s] for s in symbols}).reindex(window_index).fillna(0.0)

    bars = pd.Series(window_index, index=window_index)
    month_starts = pd.DatetimeIndex(bars.groupby([window_index.year, window_index.month]).min().to_list())
    universe = select_universe(frames, month_starts, size, min_history_days)
    member = pd.DataFrame(False, index=window_index, columns=symbols)
    for i, start in enumerate(month_starts):
        end = month_starts[i + 1] if i + 1 < len(month_starts) else window_index[-1] + pd.Timedelta(seconds=1)
        rows = (window_index >= start) & (window_index < end)
        for sym in universe[start]:
            member.loc[rows, sym] = True

    slot = 1.0 / size
    cost_rate = fee + slippage
    threshold = rebalance_threshold * slot
    c = closes.to_numpy()
    want_all = (targets.to_numpy() * slot) * member.to_numpy()
    held = np.zeros(len(symbols))
    equity = initial_capital
    values, exposure = [], []
    trades: list[Trade] = []
    episodes: dict[int, dict] = {}

    for t, ts in enumerate(window_index):
        if t > 0:
            r = np.where(np.isfinite(c[t]) & np.isfinite(c[t - 1]), c[t] / np.where(c[t - 1] > 0, c[t - 1], 1) - 1, 0.0)
            growth = 1 + float(held @ r)
            if growth <= 0:
                held[:] = 0.0
                equity = 0.0
            else:
                held = held * (1 + r) / growth
                equity *= growth
        for k in range(len(symbols)):
            want = float(want_all[t, k]) if np.isfinite(c[t, k]) else 0.0
            if abs(want - held[k]) > threshold or (want == 0.0 and held[k] > 0.0):
                if held[k] == 0.0 and want > 0.0:
                    episodes[k] = {"start": ts, "entry_price": float(c[t, k]), "equity_before": equity}
                equity -= equity * abs(want - held[k]) * cost_rate
                held[k] = want
                if want == 0.0 and k in episodes:
                    e = episodes.pop(k)
                    trades.append(Trade("long", e["start"], e["entry_price"], ts, float(c[t, k]) if np.isfinite(c[t, k]) else e["entry_price"],
                                        0.0, equity - e["equity_before"], "flat", symbols[k]))
        values.append(equity)
        exposure.append(float(held.sum()))

    for k, e in episodes.items():
        last = float(c[-1, k]) if np.isfinite(c[-1, k]) else e["entry_price"]
        trades.append(Trade("long", e["start"], e["entry_price"], window_index[-1], last, 0.0,
                            equity - e["equity_before"], "end_of_data", symbols[k]))
    return PortfolioResult(
        equity_curve=pd.Series(values, index=window_index, name="equity"),
        trades=trades,
        universe={f"{start:%Y-%m}": universe[start] for start in month_starts},
        exposure=pd.Series(exposure, index=window_index),
    )

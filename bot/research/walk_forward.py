"""Anchored walk-forward validation. Each fold trains on all history before
its test window and is judged on the next `test_months`; folds step
forward until the end of the data, which callers set to the holdout start
so no fold ever touches the holdout.

Every variant is backtested once over the whole history. The engines are
causal (a bar's decision uses only bars up to it), so a fold's training
returns are exactly what was knowable at its test start, and its test
returns are what the variant chosen then would have earned. With one
variant the folds simply show how stable the strategy is over time."""

import math
from dataclasses import dataclass

import pandas as pd

from bot.backtest.engine import Trade
from bot.backtest.metrics import periods_per_year


@dataclass(frozen=True)
class Fold:
    index: int
    train_start: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp  # exclusive


def make_folds(data_start: pd.Timestamp, first_test: pd.Timestamp, end: pd.Timestamp,
               test_months: int = 6) -> list[Fold]:
    """`end` is exclusive: the holdout start (or the end of the data)."""
    folds, start, i = [], first_test, 1
    while start < end:
        stop = min(start + pd.DateOffset(months=test_months), end)
        folds.append(Fold(i, data_start, start, stop))
        start, i = stop, i + 1
    return folds


def _window(returns: pd.Series, start, end) -> pd.Series:
    return returns[(returns.index >= start) & (returns.index < end)]


def _annual_sharpe(r: pd.Series, ppy: float) -> float:
    if len(r) < 2 or r.std() < 1e-12:
        return 0.0
    return float(r.mean() / r.std() * math.sqrt(ppy))


def _drawdown_pct(r: pd.Series) -> float:
    if len(r) == 0:
        return 0.0
    equity = (1 + r).cumprod()
    return float((equity / equity.cummax() - 1).min() * 100)


def window_stats(r: pd.Series, ppy: float, trades: list[Trade] | None = None, start=None, end=None) -> dict:
    out = {
        "total_return_pct": round(float(((1 + r).prod() - 1) * 100), 2),
        "sharpe_ratio": round(_annual_sharpe(r, ppy), 2),
        "max_drawdown_pct": round(_drawdown_pct(r), 2),
        "bars": len(r),
    }
    if trades is not None:
        out["trades"] = sum(1 for t in trades if start <= t.entry_time < end)
    return out


def walk_forward(curves: dict[str, pd.Series], trades: dict[str, list[Trade]], folds: list[Fold],
                 timeframe: str) -> dict:
    """`curves`: each variant's equity curve over the whole history.
    Per fold, the variant with the best training Sharpe is used for the
    test window. Returns the fold table, the stitched out-of-sample return
    series and its stats."""
    ppy = periods_per_year(timeframe)
    returns = {label: c.pct_change().fillna(0.0) for label, c in curves.items()}
    rows, pieces = [], []
    for fold in folds:
        train = {label: _window(r, fold.train_start, fold.test_start) for label, r in returns.items()}
        if len(returns) > 1:
            chosen = max(train, key=lambda label: (_annual_sharpe(train[label], ppy), -list(train).index(label)))
        else:
            chosen = next(iter(returns))
        test = _window(returns[chosen], fold.test_start, fold.test_end)
        pieces.append(test)
        rows.append({
            "fold": fold.index,
            "train": f"{fold.train_start:%Y-%m-%d}..{fold.test_start - pd.Timedelta(days=1):%Y-%m-%d}",
            "test": f"{fold.test_start:%Y-%m-%d}..{fold.test_end - pd.Timedelta(days=1):%Y-%m-%d}",
            "chosen": chosen,
            "train_sharpe": round(_annual_sharpe(train[chosen], ppy), 2),
            "test_stats": window_stats(test, ppy, trades.get(chosen), fold.test_start, fold.test_end),
        })
    oos = pd.concat(pieces) if pieces else pd.Series(dtype=float)
    stats = window_stats(oos, ppy)
    stats["positive_folds"] = sum(1 for row in rows if row["test_stats"]["total_return_pct"] > 0)
    stats["folds"] = len(rows)
    return {"folds": rows, "oos_returns": oos, "oos": stats}


def daily_returns(returns: pd.Series) -> pd.Series:
    """Bar returns compounded to calendar days, for storage and PBO."""
    return (1 + returns).groupby(returns.index.normalize()).prod() - 1

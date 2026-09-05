import math

import ccxt
import pandas as pd

from bot.backtest.engine import Trade

SECONDS_PER_YEAR = 365 * 24 * 3600


def periods_per_year(timeframe: str) -> float:
    return SECONDS_PER_YEAR / ccxt.Exchange.parse_timeframe(timeframe)


def total_return_pct(equity_curve: pd.Series, initial_capital: float) -> float:
    if len(equity_curve) == 0 or initial_capital <= 0:
        return 0.0
    return (equity_curve.iloc[-1] / initial_capital - 1) * 100


def max_drawdown_pct(equity_curve: pd.Series) -> float:
    if len(equity_curve) == 0:
        return 0.0
    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    return drawdown.min() * 100


def sharpe_ratio(equity_curve: pd.Series, timeframe: str) -> float:
    if len(equity_curve) < 2:
        return 0.0
    returns = equity_curve.pct_change().dropna()
    if returns.std() < 1e-12:
        return 0.0
    return (returns.mean() / returns.std()) * math.sqrt(periods_per_year(timeframe))


def win_rate_pct(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.pnl > 0)
    return (wins / len(trades)) * 100


def profit_factor(trades: list[Trade]) -> float:
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def summarize(
    trades: list[Trade], equity_curve: pd.Series, initial_capital: float, timeframe: str
) -> dict:
    return {
        "trades": len(trades),
        "total_return_pct": round(float(total_return_pct(equity_curve, initial_capital)), 2),
        "max_drawdown_pct": round(float(max_drawdown_pct(equity_curve)), 2),
        "sharpe_ratio": round(float(sharpe_ratio(equity_curve, timeframe)), 2),
        "win_rate_pct": round(float(win_rate_pct(trades)), 2),
        "profit_factor": round(float(profit_factor(trades)), 2) if trades else 0.0,
    }

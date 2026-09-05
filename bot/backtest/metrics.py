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


def buy_hold_return_pct(close: pd.Series) -> float:
    """Simple buy-and-hold return over the same price series/window a
    backtest used — the baseline spec Section 7's evidence note says none of
    the reference strategies actually beat (their edge was a better risk
    profile, not higher raw return). Ignores fees/slippage, same as a real
    single buy-and-hold would."""
    if len(close) < 2 or close.iloc[0] <= 0:
        return 0.0
    return (close.iloc[-1] / close.iloc[0] - 1) * 100


def breakdown_by_strategy(trades: list[Trade]) -> dict[str, dict]:
    """Splits trades by which sub-strategy opened them (Trade.strategy_name)
    and reports each group's own trade count/win rate/profit factor/total
    pnl. For a composite like regime_switched, this answers "how did the
    trades the regime filter actually handed to rsi_bb perform" — as opposed
    to rsi_bb's standalone (ungated) numbers, which include periods it was
    never designed to trade in (spec Section 7b) and so aren't a fair test of
    whether the regime-gated version has a real edge."""
    by_name: dict[str, list[Trade]] = {}
    for t in trades:
        by_name.setdefault(t.strategy_name, []).append(t)

    return {
        name: {
            "trades": len(group),
            "win_rate_pct": round(float(win_rate_pct(group)), 2),
            "profit_factor": round(float(profit_factor(group)), 2) if group else 0.0,
            "total_pnl": round(float(sum(t.pnl for t in group)), 2),
        }
        for name, group in by_name.items()
    }


def summarize(
    trades: list[Trade],
    equity_curve: pd.Series,
    initial_capital: float,
    timeframe: str,
    close: pd.Series | None = None,
) -> dict:
    result = {
        "trades": len(trades),
        "total_return_pct": round(float(total_return_pct(equity_curve, initial_capital)), 2),
        "max_drawdown_pct": round(float(max_drawdown_pct(equity_curve)), 2),
        "sharpe_ratio": round(float(sharpe_ratio(equity_curve, timeframe)), 2),
        "win_rate_pct": round(float(win_rate_pct(trades)), 2),
        "profit_factor": round(float(profit_factor(trades)), 2) if trades else 0.0,
    }
    if close is not None:
        result["buy_hold_pct"] = round(float(buy_hold_return_pct(close)), 2)
    return result

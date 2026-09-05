from dataclasses import dataclass, field

import pandas as pd

from bot.strategy.base import Direction, Signal, Strategy


@dataclass
class Trade:
    direction: Direction
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    size: float
    pnl: float
    exit_reason: str


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))

    @property
    def final_equity(self) -> float:
        return self.equity_curve.iloc[-1] if len(self.equity_curve) else 0.0


def position_size(equity: float, risk_pct: float, entry_price: float, stop_loss: float) -> float:
    """Size off the stop-loss distance (spec Section 7): risk a fixed % of
    equity, not a fixed coin amount."""
    stop_distance = abs(entry_price - stop_loss)
    if stop_distance <= 0:
        return 0.0
    return (equity * risk_pct) / stop_distance


def _open_position(signal: Signal, size: float, fee: float, slippage: float) -> dict:
    if signal.direction == "long":
        effective_entry = signal.entry_price * (1 + slippage)
    else:
        effective_entry = signal.entry_price * (1 - slippage)
    entry_fee = effective_entry * size * fee
    return {
        "direction": signal.direction,
        "entry_price": effective_entry,
        "entry_time": signal.timestamp,
        "size": size,
        "stop": signal.stop_loss,
        "take_profit": signal.take_profit,
        "entry_fee": entry_fee,
    }


def _check_exit(position: dict, bar: pd.Series) -> tuple[float | None, str | None]:
    """Uses the bar's high/low for intrabar stop/target hits. If both could
    have hit within the same bar, assumes the stop hit first — the more
    conservative (less favorable) assumption, since we can't know the true
    intrabar order from OHLC alone."""
    direction = position["direction"]
    stop = position["stop"]
    tp = position["take_profit"]
    if direction == "long":
        if bar["low"] <= stop:
            return stop, "stop"
        if tp is not None and bar["high"] >= tp:
            return tp, "take_profit"
    else:
        if bar["high"] >= stop:
            return stop, "stop"
        if tp is not None and bar["low"] <= tp:
            return tp, "take_profit"
    return None, None


def _close_position(
    position: dict, exit_price: float, fee: float, slippage: float
) -> tuple[float, float]:
    """Returns (net_pnl, effective_exit_price)."""
    direction = position["direction"]
    if direction == "long":
        effective_exit = exit_price * (1 - slippage)
        gross_pnl = (effective_exit - position["entry_price"]) * position["size"]
    else:
        effective_exit = exit_price * (1 + slippage)
        gross_pnl = (position["entry_price"] - effective_exit) * position["size"]
    exit_fee = effective_exit * position["size"] * fee
    net_pnl = gross_pnl - position["entry_fee"] - exit_fee
    return net_pnl, effective_exit


def _unrealized_pnl(position: dict, bar: pd.Series) -> float:
    price = bar["close"]
    if position["direction"] == "long":
        return (price - position["entry_price"]) * position["size"]
    return (position["entry_price"] - price) * position["size"]


def run_backtest(
    df: pd.DataFrame,
    strategy: Strategy,
    fee: float = 0.001,
    slippage: float = 0.0005,
    initial_capital: float = 10_000.0,
    risk_pct: float = 0.01,
) -> BacktestResult:
    """Bar-by-bar simulation: manages at most one open position at a time,
    sized off the stop-loss distance and closed out on stop/target/end-of-data.
    Indicators are recomputed each bar over a bounded trailing window
    (`strategy.min_lookback`), not the full history, so cost stays O(n)."""
    lookback = strategy.min_lookback
    if len(df) <= lookback:
        return BacktestResult()

    equity = initial_capital
    position: dict | None = None
    trades: list[Trade] = []
    times: list = []
    values: list[float] = []

    for i in range(lookback, len(df)):
        window = df.iloc[max(0, i - lookback + 1) : i + 1]
        bar = df.iloc[i]

        if position is not None:
            position["stop"] = strategy.trail_stop(window, position["direction"], position["stop"])
            exit_price, exit_reason = _check_exit(position, bar)
            if exit_price is not None:
                pnl, effective_exit = _close_position(position, exit_price, fee, slippage)
                equity += pnl
                trades.append(
                    Trade(
                        direction=position["direction"],
                        entry_time=position["entry_time"],
                        entry_price=position["entry_price"],
                        exit_time=bar.name,
                        exit_price=effective_exit,
                        size=position["size"],
                        pnl=pnl,
                        exit_reason=exit_reason,
                    )
                )
                position = None

        if position is None:
            signal = strategy.generate_signal(window)
            if signal is not None and signal.direction != "flat":
                size = position_size(equity, risk_pct, signal.entry_price, signal.stop_loss)
                if size > 0:
                    position = _open_position(signal, size, fee, slippage)

        unrealized = _unrealized_pnl(position, bar) if position is not None else 0.0
        times.append(bar.name)
        values.append(equity + unrealized)

    if position is not None:
        last_bar = df.iloc[-1]
        pnl, effective_exit = _close_position(position, last_bar["close"], fee, slippage)
        equity += pnl
        trades.append(
            Trade(
                direction=position["direction"],
                entry_time=position["entry_time"],
                entry_price=position["entry_price"],
                exit_time=last_bar.name,
                exit_price=effective_exit,
                size=position["size"],
                pnl=pnl,
                exit_reason="end_of_data",
            )
        )
        values[-1] = equity

    equity_curve = pd.Series(values, index=pd.Index(times, name="open_time"), name="equity")
    return BacktestResult(trades=trades, equity_curve=equity_curve)

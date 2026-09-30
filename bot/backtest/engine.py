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
    strategy_name: str
    # extra units added to the position after the first entry (pyramiding)
    adds: int = 0


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))

    @property
    def final_equity(self) -> float:
        return self.equity_curve.iloc[-1] if len(self.equity_curve) else 0.0


def position_size(equity: float, risk_pct: float, entry_price: float, stop_loss: float) -> float:
    """Size off the stop-loss distance (spec Section 7): risk a fixed % of
    equity, not a fixed coin amount. Capped at what equity can actually buy —
    Quidax is spot-only, no margin (spec Section 2), so a tight stop (small
    ATR relative to price) must not imply a notional position worth many
    times the account's equity."""
    stop_distance = abs(entry_price - stop_loss)
    if stop_distance <= 0 or entry_price <= 0:
        return 0.0
    risk_based_size = (equity * risk_pct) / stop_distance
    max_affordable_size = equity / entry_price
    return min(risk_based_size, max_affordable_size)


def open_position(signal: Signal, size: float, fee: float, slippage: float, owner: Strategy) -> dict:
    """Public (not backtest-private): the shadow runner (bot/shadow/) reuses
    this, check_exit, and close_position directly, so live position/exit/pnl
    math is guaranteed identical to what the backtest models — the entire
    point of comparing shadow-run behavior against backtest expectations
    (spec Phase 4) falls apart if the two paths can silently drift apart."""
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
        "strategy": owner,
        "context": signal.context,
    }


def check_exit(position: dict, bar: pd.Series) -> tuple[float | None, str | None]:
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


def close_position(
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


def add_to_position(position: dict, price: float, size: float, stop: float, fee: float, slippage: float) -> None:
    """Pyramiding: adds `size` at `price` to an open position. The position
    becomes one blended position (size-weighted entry, summed entry fees),
    so check_exit/close_position work on it unchanged. Its stop only ever
    tightens: the new signal's stop is taken only if it's closer."""
    long = position["direction"] == "long"
    effective = price * (1 + slippage) if long else price * (1 - slippage)
    total = position["size"] + size
    position["entry_price"] = (position["entry_price"] * position["size"] + effective * size) / total
    position["size"] = total
    position["entry_fee"] += effective * size * fee
    position["stop"] = max(position["stop"], stop) if long else min(position["stop"], stop)
    position["adds"] = position.get("adds", 0) + 1
    position["last_fill"] = effective


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
    pyramid: dict | None = None,
) -> BacktestResult:
    """Bar-by-bar simulation: manages at most one open position at a time,
    sized off the stop-loss distance and closed out on stop/target/end-of-data.

    Entry signals are computed once up front via `strategy.entry_signals(df)`
    (vectorized over the whole series) rather than by re-slicing a trailing
    window and calling `generate_signal` at every bar — the latter is both
    O(n * lookback) and, for recursive indicators (EMA, Wilder smoothing),
    numerically restarts each one's "memory" every window instead of letting
    it run continuously. Trailing-stop updates still use a bounded window,
    since they're only needed on the much rarer bars where a position is
    actually open.

    `pyramid` (research option, off by default): {"max_adds": N,
    "add_step_pct": x}. While in a position, a fresh entry signal in the same
    direction adds a unit, sized like an entry (risk_pct of current equity
    over the new signal's stop distance), up to N adds, and only once price
    has moved at least x (a fraction) in the trade's favour since the last
    fill. Total notional is capped at equity: spot, no leverage."""
    max_adds = int((pyramid or {}).get("max_adds", 0) or 0)
    add_step = float((pyramid or {}).get("add_step_pct", 0.0) or 0.0)
    lookback = strategy.min_lookback
    if len(df) <= lookback:
        return BacktestResult()

    signals = strategy.entry_signals(df)
    has_owner_column = "strategy" in signals.columns

    equity = initial_capital
    position: dict | None = None
    trades: list[Trade] = []
    times: list = []
    values: list[float] = []

    for i in range(lookback, len(df)):
        bar = df.iloc[i]

        if position is not None:
            window = df.iloc[max(0, i - lookback + 1) : i + 1]
            position["stop"] = position["strategy"].trail_stop(
                window, position["direction"], position["stop"]
            )
            exit_price, exit_reason = check_exit(position, bar)
            if exit_price is not None:
                pnl, effective_exit = close_position(position, exit_price, fee, slippage)
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
                        strategy_name=position["strategy"].name,
                        adds=position.get("adds", 0),
                    )
                )
                position = None

        if position is not None and max_adds and position.get("adds", 0) < max_adds:
            sig_row = signals.iloc[i]
            if sig_row["direction"] == position["direction"]:
                price = float(sig_row["entry_price"])
                sign = 1 if position["direction"] == "long" else -1
                moved = (price / position["last_fill"] - 1) * sign
                if moved >= add_step:
                    equity_now = equity + _unrealized_pnl(position, bar)
                    size = position_size(equity_now, risk_pct, price, float(sig_row["stop_loss"]))
                    size = min(size, max(0.0, equity_now / price - position["size"]))
                    if size > 0:
                        add_to_position(position, price, size, float(sig_row["stop_loss"]), fee, slippage)
        elif position is None:
            sig_row = signals.iloc[i]
            if pd.notna(sig_row["direction"]):
                take_profit = sig_row["take_profit"]
                signal = Signal(
                    symbol="",
                    timeframe="",
                    direction=sig_row["direction"],
                    entry_price=sig_row["entry_price"],
                    stop_loss=sig_row["stop_loss"],
                    take_profit=None if pd.isna(take_profit) else take_profit,
                    reason=sig_row["reason"] or "",
                    timestamp=bar.name,
                )
                size = position_size(equity, risk_pct, signal.entry_price, signal.stop_loss)
                if size > 0:
                    owner = sig_row["strategy"] if has_owner_column else strategy
                    position = open_position(signal, size, fee, slippage, owner)
                    position["last_fill"] = position["entry_price"]

        unrealized = _unrealized_pnl(position, bar) if position is not None else 0.0
        times.append(bar.name)
        values.append(equity + unrealized)

    if position is not None:
        last_bar = df.iloc[-1]
        pnl, effective_exit = close_position(position, last_bar["close"], fee, slippage)
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
                strategy_name=position["strategy"].name,
                adds=position.get("adds", 0),
            )
        )
        values[-1] = equity

    equity_curve = pd.Series(values, index=pd.Index(times, name="open_time"), name="equity")
    return BacktestResult(trades=trades, equity_curve=equity_curve)

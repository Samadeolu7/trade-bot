import pandas as pd

from bot.backtest.engine import BacktestResult, Trade
from bot.strategy.base import Strategy


def mark_to_market(held: float, equity: float, prev_close: float, close: float) -> tuple[float, float]:
    """Apply one bar's price move to a position holding fraction `held` of
    `equity`. Returns (new_held, new_equity): the held fraction drifts,
    since the invested part grew or shrank relative to the cash part."""
    if held <= 0:
        return 0.0, equity
    r = close / prev_close - 1
    growth = 1 + held * r
    if growth <= 0:
        return 0.0, 0.0
    return held * (1 + r) / growth, equity * growth


def rebalance(held: float, equity: float, want: float, threshold: float, cost_rate: float) -> tuple[float, float, bool]:
    """Move from `held` to target `want` at the bar's close, paying
    `cost_rate` on the traded notional. Changes smaller than `threshold`
    are skipped, except going fully flat, which always executes. Returns
    (new_held, new_equity, traded).

    Public, together with mark_to_market: the live exposure paper bot
    (bot/shadow/exposure_runner.py) calls these same two functions bar by
    bar, so live and backtest math can't drift apart."""
    if abs(want - held) > threshold or (want == 0.0 and held > 0.0):
        return want, equity - equity * abs(want - held) * cost_rate, True
    return held, equity, False


def run_exposure_backtest(
    df: pd.DataFrame,
    strategy: Strategy,
    fee: float = 0.001,
    slippage: float = 0.0005,
    initial_capital: float = 10_000.0,
    weights: pd.Series | None = None,
) -> BacktestResult:
    """Backtest for strategies that output a target fraction of capital
    each bar (`strategy.target_weights(df)`) instead of discrete entries
    with stops — e.g. the vol-targeted Donchian ensemble.

    At each bar's close: first mark the held fraction to market (it drifts
    with price), then rebalance to that bar's target, paying fee + slippage
    on the traded notional. So a weight computed at bar t's close earns bar
    t+1's return, the same no-lookahead timing as the stop-based engine's
    close-of-bar entries. Rebalances smaller than the strategy's
    `rebalance_threshold` are skipped (going fully flat always executes).

    `weights` lets the caller precompute targets on a longer history (for
    warmup: a 360-day lookback shouldn't sit idle for the first year of the
    window being tested) and pass them in aligned to `df`.

    "Trades" for the win-rate/profit-factor metrics are exposure episodes:
    from the bar exposure goes above zero until it returns to zero, with
    pnl = equity change over the episode, costs included."""
    if len(df) == 0:
        return BacktestResult()
    target = (weights if weights is not None else strategy.target_weights(df)).reindex(df.index).fillna(0.0)
    threshold = getattr(strategy, "rebalance_threshold", 0.0)
    cost_rate = fee + slippage

    close = df["close"].to_numpy()
    equity = initial_capital
    held = 0.0
    values: list[float] = []
    trades: list[Trade] = []
    episode: dict | None = None

    for i, ts in enumerate(df.index):
        if i > 0:
            held, equity = mark_to_market(held, equity, close[i - 1], close[i])

        want = float(target.iloc[i])
        was_flat, equity_before = held == 0.0, equity
        held, equity, traded = rebalance(held, equity, want, threshold, cost_rate)
        if traded and was_flat and held > 0.0:
            episode = {"start": ts, "entry_price": close[i], "equity_before": equity_before, "peak": held}
        if episode is not None:
            episode["peak"] = max(episode["peak"], held)
            if held == 0.0:
                trades.append(_episode_trade(episode, ts, close[i], equity, "flat", strategy.name))
                episode = None
        values.append(equity)

    if episode is not None:
        trades.append(_episode_trade(episode, df.index[-1], close[-1], equity, "end_of_data", strategy.name))

    equity_curve = pd.Series(values, index=pd.Index(df.index, name="open_time"), name="equity")
    return BacktestResult(trades=trades, equity_curve=equity_curve)


def _episode_trade(episode: dict, exit_time, exit_price: float, equity: float, reason: str, name: str) -> Trade:
    return Trade(
        direction="long",
        entry_time=episode["start"],
        entry_price=float(episode["entry_price"]),
        exit_time=exit_time,
        exit_price=float(exit_price),
        size=float(episode["peak"]),  # peak fraction of capital held during the episode
        pnl=float(equity - episode["equity_before"]),
        exit_reason=reason,
        strategy_name=name,
    )

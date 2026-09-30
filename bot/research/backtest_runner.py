"""Backtest helpers shared by the CLI (main.py) and the web platform's
research worker, so a research job started from the app computes exactly
what `python main.py research-report` would."""

import logging

import pandas as pd

from bot.backtest.engine import run_backtest
from bot.backtest.exposure import run_exposure_backtest
from bot.backtest.metrics import breakdown_by_strategy, summarize
from bot.strategy.registry import build_strategy

logger = logging.getLogger(__name__)


def with_override(strategy_config: dict, section: str, key: str, value) -> dict:
    if value is None:
        return strategy_config
    return {**strategy_config, section: {**strategy_config.get(section, {}), key: value}}


def run_backtest_summary(
    df: pd.DataFrame,
    strategy_name: str,
    strategy_config: dict,
    backtest_config: dict,
    timeframe: str,
    funding_df: pd.DataFrame | None = None,
    warmup_df: pd.DataFrame | None = None,
) -> tuple[dict, pd.Series]:
    """Returns (summary, equity_curve). `warmup_df`: longer history ending
    where `df` ends, used only by exposure strategies (fraction-of-capital
    sizing, e.g. donchian_ensemble) to compute targets before the window
    starts — their longest lookback is a year, which would otherwise sit
    idle through the first year of `df`."""
    strategy = build_strategy(strategy_name, strategy_config, funding_df=funding_df)
    if hasattr(strategy, "target_weights"):
        weights = None
        if warmup_df is not None and len(warmup_df):
            weights = strategy.target_weights(warmup_df).reindex(df.index)
        result = run_exposure_backtest(
            df,
            strategy,
            fee=backtest_config.get("fee", 0.001),
            slippage=backtest_config.get("slippage", 0.0005),
            initial_capital=backtest_config.get("initial_capital", 10_000.0),
            weights=weights,
        )
    else:
        result = run_backtest(
            df,
            strategy,
            fee=backtest_config.get("fee", 0.001),
            slippage=backtest_config.get("slippage", 0.0005),
            initial_capital=backtest_config.get("initial_capital", 10_000.0),
            risk_pct=backtest_config.get("risk_pct", 0.01),
            pyramid=strategy_config.get("pyramid"),
        )
    summary = summarize(
        result.trades,
        result.equity_curve,
        backtest_config.get("initial_capital", 10_000.0),
        timeframe,
        close=df["close"],
    )
    if int((strategy_config.get("pyramid") or {}).get("max_adds", 0) or 0):
        summary["adds"] = sum(t.adds for t in result.trades)
    by_strategy = breakdown_by_strategy(result.trades)
    if len(by_strategy) > 1:
        summary["by_strategy"] = by_strategy
    return summary, result.equity_curve


def apply_holdout_guard(
    df: pd.DataFrame, holdout_start: str | None, allow_holdout: bool, context_label: str
) -> tuple[pd.DataFrame, bool]:
    """Excludes any bar at/after `holdout_start` unless `allow_holdout` is
    set, so tuning/exploration can't silently peek at the reserved
    out-of-sample window. Returns (possibly-truncated df, whether holdout
    data was actually included). A no-op if holdout_start is unset or the
    data doesn't reach it anyway."""
    if not holdout_start or len(df) == 0:
        return df, False
    holdout_ts = pd.Timestamp(holdout_start, tz="UTC")
    if df.index.max() < holdout_ts:
        return df, False
    if not allow_holdout:
        excluded = int((df.index >= holdout_ts).sum())
        logger.warning(
            "%s: excluding %d reserved holdout bar(s) from %s onward "
            "(pass --allow-holdout for a deliberate final confirmatory check)",
            context_label, excluded, holdout_start,
        )
        return df[df.index < holdout_ts], False
    logger.warning(
        "%s: HOLDOUT DATA INCLUDED (from %s onward) — this should be a rare, "
        "deliberate final check per candidate strategy, not part of routine tuning",
        context_label, holdout_start,
    )
    return df, True

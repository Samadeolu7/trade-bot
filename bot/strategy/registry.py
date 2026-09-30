"""The one place a strategy name becomes a Strategy object.

Used by the CLI (main.py) and by the web platform's engine and research
worker, so a bot running in the app is built exactly the way its
backtests were. Adding a strategy means writing the class, adding it to
`build_strategy` and describing it in `STRATEGY_CATALOG`; the app's "New
bot" form and research page read the catalog, so nothing else changes."""

from dataclasses import dataclass

import pandas as pd

from bot.strategy.base import Strategy
from bot.strategy.crt import CandleRangeTheoryStrategy
from bot.strategy.donchian import DonchianBreakoutStrategy
from bot.strategy.donchian_ensemble import DonchianEnsembleStrategy
from bot.strategy.ema_cross import EmaCrossStrategy
from bot.strategy.flat import FlatStrategy
from bot.strategy.funding_filter import FundingFilteredStrategy
from bot.strategy.long_only import LongOnlyStrategy
from bot.strategy.market_structure import MarketStructureBreakoutStrategy
from bot.strategy.multi_timeframe import MultiTimeframeTrendPullbackStrategy
from bot.strategy.regime import NatrRegimeFilter, RegimeFilter, Sma200RegimeFilter
from bot.strategy.regime_switch import RegimeSwitchedStrategy
from bot.strategy.rsi_bb import RsiBollingerStrategy
from bot.strategy.vol_expansion import VolatilityExpansionBreakoutStrategy


@dataclass(frozen=True)
class StrategyInfo:
    name: str
    # "signal": discrete entry/stop/exit positions (generate_signal).
    # "exposure": holds a changing fraction of capital (target_weights).
    kind: str
    can_short: bool
    description: str
    # config.yaml `strategy.<section>` entries this strategy reads; the
    # app shows these as the bot's editable parameters. "pyramid" is the
    # backtest engine's research-only pyramiding option (bot/backtest/engine.py)
    config_sections: tuple[str, ...]


STRATEGY_CATALOG: dict[str, StrategyInfo] = {
    info.name: info
    for info in [
        StrategyInfo(
            "donchian", "signal", True,
            "Breakout of the prior N-bar high/low channel, exit on a wider channel or ATR trail.",
            ("donchian", "pyramid"),
        ),
        StrategyInfo(
            "donchian_ensemble", "exposure", False,
            "Nine Donchian lookbacks averaged into one signal, sized to a volatility target.",
            ("donchian_ensemble",),
        ),
        StrategyInfo(
            "regime_switched", "signal", True,
            "Runs a trend strategy while the regime filter says trending, a ranging one otherwise.",
            ("regime_switched", "regime", "regime_sma", "regime_natr", "donchian", "ema_cross", "rsi_bb", "pyramid"),
        ),
        StrategyInfo(
            "funding_filtered", "signal", True,
            "A trend strategy whose entries are vetoed when perpetual funding is crowded.",
            ("funding_filtered", "donchian", "pyramid"),
        ),
        StrategyInfo(
            "ema_cross", "signal", True,
            "Fast/slow EMA cross with an ATR trailing stop.",
            ("ema_cross", "pyramid"),
        ),
        StrategyInfo(
            "rsi_bb", "signal", True,
            "RSI/Bollinger mean reversion back to the midline.",
            ("rsi_bb", "pyramid"),
        ),
        StrategyInfo(
            "market_structure", "signal", True,
            "Swing break, retest, then entry on confirmed rejection.",
            ("market_structure", "pyramid"),
        ),
        StrategyInfo(
            "multi_timeframe", "signal", True,
            "Higher-timeframe trend with a daily pullback and reclaim.",
            ("multi_timeframe", "pyramid"),
        ),
        StrategyInfo(
            "vol_expansion", "signal", True,
            "Bollinger squeeze followed by a volatility-expansion breakout.",
            ("vol_expansion", "pyramid"),
        ),
        StrategyInfo(
            "crt", "signal", True,
            "Candle Range Theory: sweep of the prior candle's range, close back inside.",
            ("crt", "pyramid"),
        ),
    ]
}

STRATEGY_CHOICES = list(STRATEGY_CATALOG)
TREND_STRATEGY_CHOICES = [
    "ema_cross", "donchian", "market_structure", "multi_timeframe", "vol_expansion",
]


def empty_funding_df() -> pd.DataFrame:
    return pd.DataFrame(
        {"funding_rate": []}, index=pd.DatetimeIndex([], tz="UTC", name="funding_time")
    )


def build_trend_strategy(name: str, strategy_config: dict) -> Strategy:
    if name == "donchian":
        return DonchianBreakoutStrategy(strategy_config.get("donchian", {}))
    if name == "market_structure":
        return MarketStructureBreakoutStrategy(strategy_config.get("market_structure", {}))
    if name == "multi_timeframe":
        return MultiTimeframeTrendPullbackStrategy(strategy_config.get("multi_timeframe", {}))
    if name == "vol_expansion":
        return VolatilityExpansionBreakoutStrategy(strategy_config.get("vol_expansion", {}))
    return EmaCrossStrategy(strategy_config.get("ema_cross", {}))


def build_ranging_strategy(name: str, strategy_config: dict) -> Strategy:
    if name == "rsi_bb":
        return RsiBollingerStrategy(strategy_config.get("rsi_bb", {}))
    return FlatStrategy()


def build_regime_filter(strategy_config: dict):
    regime_config = strategy_config.get("regime", {})
    regime_type = regime_config.get("type", "adx")
    if regime_type == "sma200":
        return Sma200RegimeFilter(**strategy_config.get("regime_sma", {}))
    if regime_type == "natr":
        return NatrRegimeFilter(**strategy_config.get("regime_natr", {}))
    return RegimeFilter(
        adx_period=regime_config.get("adx_period", 14),
        adx_threshold=regime_config.get("adx_threshold", 25),
    )


def build_strategy(
    name: str,
    strategy_config: dict,
    funding_df: pd.DataFrame | None = None,
    funding_refresh_fn=None,
) -> Strategy:
    """`strategy_config` is config.yaml's `strategy:` mapping (with any
    overrides already applied). `long_only: true` in the named strategy's
    own section wraps the result in LongOnlyStrategy."""
    strategy = _build_unwrapped(name, strategy_config, funding_df, funding_refresh_fn)
    if strategy_config.get(name, {}).get("long_only") and not hasattr(strategy, "target_weights"):
        return LongOnlyStrategy(strategy)
    return strategy


def _build_unwrapped(
    name: str,
    strategy_config: dict,
    funding_df: pd.DataFrame | None,
    funding_refresh_fn,
) -> Strategy:
    if name == "ema_cross":
        return EmaCrossStrategy(strategy_config.get("ema_cross", {}))
    if name == "rsi_bb":
        return RsiBollingerStrategy(strategy_config.get("rsi_bb", {}))
    if name == "donchian":
        return DonchianBreakoutStrategy(strategy_config.get("donchian", {}))
    if name == "market_structure":
        return MarketStructureBreakoutStrategy(strategy_config.get("market_structure", {}))
    if name == "multi_timeframe":
        return MultiTimeframeTrendPullbackStrategy(strategy_config.get("multi_timeframe", {}))
    if name == "vol_expansion":
        return VolatilityExpansionBreakoutStrategy(strategy_config.get("vol_expansion", {}))
    if name == "crt":
        return CandleRangeTheoryStrategy(strategy_config.get("crt", {}))
    if name == "donchian_ensemble":
        return DonchianEnsembleStrategy(strategy_config.get("donchian_ensemble", {}))
    if name == "funding_filtered":
        # base_strategy/high_threshold/low_threshold are config-driven (same
        # pattern as regime_switched's trend_strategy), not separate
        # strategy choices — funding_df/funding_refresh_fn come from the
        # caller since building them needs a DB connection this function
        # doesn't otherwise take.
        funding_filtered_config = strategy_config.get("funding_filtered", {})
        base_name = funding_filtered_config.get("base_strategy", "donchian")
        base = build_trend_strategy(base_name, strategy_config)
        high_threshold = funding_filtered_config.get("high_threshold", 0.0005)
        low_threshold = funding_filtered_config.get("low_threshold", -0.0005)
        return FundingFilteredStrategy(
            base,
            funding_df if funding_df is not None else empty_funding_df(),
            high_threshold,
            low_threshold,
            refresh_fn=funding_refresh_fn,
        )

    # regime_switched: trend/ranging sub-strategies and regime-filter type are
    # config-driven (spec Section 1), not separate strategy choices, so
    # e.g. swapping ADX for SMA(200) or ema_cross for donchian is a
    # config/config.yaml edit, not a code change. ranging_strategy defaults to
    # "flat" (spec Section 8 pilot findings: rsi_bb didn't demonstrate a real
    # edge and is opt-in only) rather than "rsi_bb".
    regime_switched_config = strategy_config.get("regime_switched", {})
    trend_name = regime_switched_config.get("trend_strategy", "ema_cross")
    ranging_name = regime_switched_config.get("ranging_strategy", "flat")
    trending_strategy = build_trend_strategy(trend_name, strategy_config)
    ranging_strategy = build_ranging_strategy(ranging_name, strategy_config)
    regime_filter = build_regime_filter(strategy_config)
    return RegimeSwitchedStrategy(trending_strategy, ranging_strategy, regime_filter)

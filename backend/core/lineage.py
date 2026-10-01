"""Lineage for every signal: exactly what produced a call (code, settings,
data), so any alert can be traced and reproduced, and a feed whose
settings drifted from what was validated is caught."""

import hashlib
import json

import pandas as pd

from bot.strategy.registry import STRATEGY_CATALOG

DIGEST_BARS = 120  # the recent candles hashed into data_digest


def config_hash(strategy: str, symbol: str, timeframe: str, strategy_config: dict) -> str:
    """Hash of the settings that decide this strategy's calls: only its own
    config sections, so editing another strategy's settings doesn't count."""
    from research.jobs import _effective_config

    info = STRATEGY_CATALOG.get(strategy)
    sections = info.config_sections if info else tuple(strategy_config)
    own = {s: strategy_config.get(s) for s in sorted(sections)}
    identity = {"strategy": strategy, "symbol": symbol, "timeframe": timeframe, "config": _effective_config(own)}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:16]


def data_digest(df: pd.DataFrame, bars: int = DIGEST_BARS) -> str:
    recent = df[["open", "high", "low", "close", "volume"]].iloc[-bars:]
    payload = recent.to_csv(float_format="%.8g").encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def lineage(strategy: str, symbol: str, timeframe: str, strategy_config: dict, df: pd.DataFrame) -> dict:
    """Fields stored on every Recommendation and BotDecision."""
    from research.jobs import code_version

    return {
        "code_version": code_version(),
        "config_hash": config_hash(strategy, symbol, timeframe, strategy_config),
        "data_from": df.index[0].to_pydatetime() if len(df) else None,
        "data_to": df.index[-1].to_pydatetime() if len(df) else None,
        "data_rows": len(df),
        "data_digest": data_digest(df) if len(df) else "",
    }

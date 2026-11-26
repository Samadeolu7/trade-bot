"""Lag levels for research jobs: the delays a backtest applies to outside
data (bot/data/availability.py), measured from the live engine's fetch
logs where there are enough samples, otherwise config.yaml's `data_lag`
assumptions."""

from bot.data.availability import LAG_LEVELS, LagModel, lag_levels
from bot.strategy.registry import EXTERNAL_DATA
from market.latency import funding_latency, stats_dict
from trading.services.bots import trade_bot_config


class LagError(ValueError):
    pass


def _source_config(config: dict, source: str) -> tuple[dict, dict]:
    data_lag = config.get("data_lag", {})
    return data_lag.get("assumptions", {}).get(source, {}), data_lag


def levels_for(strategy: str, config: dict | None = None) -> tuple[dict[str, LagModel], dict]:
    """(level name -> LagModel, what they're based on) for a strategy that
    reads outside data."""
    source = EXTERNAL_DATA.get(strategy)
    if source is None:
        raise LagError(f"{strategy} reads no outside data, so there's no data lag to simulate; "
                       f"lag settings apply to: {', '.join(sorted(EXTERNAL_DATA))}")
    config = config or trade_bot_config()
    assumptions, data_lag = _source_config(config, source)
    use_at = data_lag.get("use_at", "bar_close")
    if source == "funding":
        fc = config.get("funding", {})
        stats = funding_latency(fc.get("exchange_id", "binanceusdm"), fc.get("symbol", "BTC/USDT:USDT"))
    else:  # pragma: no cover - every EXTERNAL_DATA source needs a branch here
        raise LagError(f"no latency source for {source}")
    levels = lag_levels(stats, assumptions, use_at=use_at)
    basis = {"source": source, "measured": stats_dict(stats), "basis": levels["p95"].basis,
             "use_at": use_at}
    return levels, basis


def validate_levels(strategy: str, requested) -> list[str]:
    if not requested:
        return []
    if not isinstance(requested, list) or not all(isinstance(x, str) for x in requested):
        raise LagError("lag_levels must be a list of level names")
    unknown = [x for x in requested if x not in LAG_LEVELS]
    if unknown:
        raise LagError(f"unknown lag level(s) {', '.join(unknown)}; choose from {', '.join(LAG_LEVELS)}")
    if strategy not in EXTERNAL_DATA:
        raise LagError(f"{strategy} reads no outside data, so lag_levels don't apply; "
                       f"they apply to: {', '.join(sorted(EXTERNAL_DATA))}")
    return list(dict.fromkeys(requested))

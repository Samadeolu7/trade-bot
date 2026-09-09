import argparse
import itertools
import logging
import os
from pathlib import Path

import pandas as pd

from bot.alerting.telegram import TelegramAlerter
from bot.backtest.engine import run_backtest
from bot.backtest.metrics import breakdown_by_strategy, summarize
from bot.config import load_config
from bot.data.backfill import backfill_candles
from bot.data.exchange import create_exchange
from bot.data.funding import create_funding_exchange
from bot.data.funding_backfill import backfill_funding_rates
from bot.data.poll import run_poll_loop
from bot.logging_setup import configure_logging
from bot.shadow.runner import drop_incomplete_bar, run_shadow_loop
from bot.storage.db import connect, query_candles_df, query_funding_rates_df
from bot.strategy.donchian import DonchianBreakoutStrategy
from bot.strategy.ema_cross import EmaCrossStrategy
from bot.strategy.flat import FlatStrategy
from bot.strategy.funding_filter import FundingFilteredStrategy
from bot.strategy.market_structure import MarketStructureBreakoutStrategy
from bot.strategy.multi_timeframe import MultiTimeframeTrendPullbackStrategy
from bot.strategy.regime import NatrRegimeFilter, RegimeFilter, Sma200RegimeFilter
from bot.strategy.regime_switch import RegimeSwitchedStrategy
from bot.strategy.rsi_bb import RsiBollingerStrategy
from bot.strategy.vol_expansion import VolatilityExpansionBreakoutStrategy
from bot.strategy.base import Strategy

logger = logging.getLogger(__name__)

STRATEGY_CHOICES = [
    "ema_cross", "rsi_bb", "donchian", "market_structure", "multi_timeframe", "vol_expansion",
    "funding_filtered", "regime_switched",
]
TREND_STRATEGY_CHOICES = [
    "ema_cross", "donchian", "market_structure", "multi_timeframe", "vol_expansion",
]


def _build_trend_strategy(name: str, strategy_config: dict) -> Strategy:
    if name == "donchian":
        return DonchianBreakoutStrategy(strategy_config.get("donchian", {}))
    if name == "market_structure":
        return MarketStructureBreakoutStrategy(strategy_config.get("market_structure", {}))
    if name == "multi_timeframe":
        return MultiTimeframeTrendPullbackStrategy(strategy_config.get("multi_timeframe", {}))
    if name == "vol_expansion":
        return VolatilityExpansionBreakoutStrategy(strategy_config.get("vol_expansion", {}))
    return EmaCrossStrategy(strategy_config.get("ema_cross", {}))


def _add_strategy_override_args(parser: argparse.ArgumentParser) -> None:
    """Shared by `shadow` and `diagnose` — both need to build the exact same
    strategy from the exact same config/override plumbing, so a diagnosis
    reflects what a live shadow run would actually do."""
    parser.add_argument(
        "--trend-strategy",
        choices=TREND_STRATEGY_CHOICES,
        default=None,
        help="only for --strategy regime_switched: overrides strategy.regime_switched.trend_strategy",
    )
    parser.add_argument(
        "--ranging-strategy",
        choices=["flat", "rsi_bb"],
        default=None,
        help="only for --strategy regime_switched: overrides strategy.regime_switched.ranging_strategy",
    )
    parser.add_argument(
        "--regime-type",
        choices=["adx", "sma200", "natr"],
        default=None,
        help="only for --strategy regime_switched: overrides strategy.regime.type",
    )
    parser.add_argument(
        "--exit-channel-period",
        type=int,
        default=None,
        help="donchian (standalone or as regime_switched's trend strategy): "
        "overrides strategy.donchian.exit_channel_period",
    )
    parser.add_argument(
        "--exit-method",
        choices=["channel", "atr"],
        default=None,
        help="donchian: overrides strategy.donchian.exit_method",
    )
    parser.add_argument(
        "--donchian-atr-period",
        type=int,
        default=None,
        help="donchian with --exit-method atr: overrides strategy.donchian.atr_period",
    )
    parser.add_argument(
        "--donchian-atr-mult",
        type=float,
        default=None,
        help="donchian with --exit-method atr: overrides strategy.donchian.atr_mult",
    )
    parser.add_argument(
        "--funding-base-strategy",
        choices=TREND_STRATEGY_CHOICES,
        default=None,
        help="only for --strategy funding_filtered: overrides strategy.funding_filtered.base_strategy",
    )
    parser.add_argument(
        "--funding-high-threshold",
        type=float,
        default=None,
        help="only for --strategy funding_filtered: overrides strategy.funding_filtered.high_threshold",
    )
    parser.add_argument(
        "--funding-low-threshold",
        type=float,
        default=None,
        help="only for --strategy funding_filtered: overrides strategy.funding_filtered.low_threshold",
    )


def _apply_strategy_overrides(strategy_config: dict, args: argparse.Namespace) -> dict:
    """Applies every override from _add_strategy_override_args's flags."""
    strategy_config = _with_override(
        strategy_config, "regime_switched", "trend_strategy", args.trend_strategy
    )
    strategy_config = _with_override(
        strategy_config, "regime_switched", "ranging_strategy", args.ranging_strategy
    )
    strategy_config = _with_override(strategy_config, "regime", "type", args.regime_type)
    strategy_config = _with_override(
        strategy_config, "donchian", "exit_channel_period", args.exit_channel_period
    )
    strategy_config = _with_override(strategy_config, "donchian", "exit_method", args.exit_method)
    strategy_config = _with_override(
        strategy_config, "donchian", "atr_period", args.donchian_atr_period
    )
    strategy_config = _with_override(
        strategy_config, "donchian", "atr_mult", args.donchian_atr_mult
    )
    strategy_config = _with_override(
        strategy_config, "funding_filtered", "base_strategy", args.funding_base_strategy
    )
    strategy_config = _with_override(
        strategy_config, "funding_filtered", "high_threshold", args.funding_high_threshold
    )
    strategy_config = _with_override(
        strategy_config, "funding_filtered", "low_threshold", args.funding_low_threshold
    )
    return strategy_config


def _empty_funding_df() -> pd.DataFrame:
    return pd.DataFrame(
        {"funding_rate": []}, index=pd.DatetimeIndex([], tz="UTC", name="funding_time")
    )


def _load_funding_df(config: dict, conn) -> pd.DataFrame:
    """Backfills (resume=True — cheap no-op if already caught up) then reads
    back funding-rate history from its own exchange identity (funding is a
    perpetual-futures concept, unavailable on the spot client used for
    candles)."""
    funding_config = config.get("funding", {})
    exchange_id = funding_config.get("exchange_id", "binanceusdm")
    symbol = funding_config.get("symbol", "BTC/USDT:USDT")
    start_date = funding_config.get("start_date", config["backfill"]["start_date"])
    funding_exchange = create_funding_exchange(exchange_id)
    backfill_funding_rates(funding_exchange, conn, exchange_id, symbol, start_date, resume=True)
    return query_funding_rates_df(conn, exchange_id, symbol)


def _build_ranging_strategy(name: str, strategy_config: dict) -> Strategy:
    if name == "rsi_bb":
        return RsiBollingerStrategy(strategy_config.get("rsi_bb", {}))
    return FlatStrategy()


def _build_regime_filter(strategy_config: dict):
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


def _with_override(strategy_config: dict, section: str, key: str, value) -> dict:
    if value is None:
        return strategy_config
    return {**strategy_config, section: {**strategy_config.get(section, {}), key: value}}


def _coerce_param_value(raw: str):
    try:
        f = float(raw)
    except ValueError:
        return raw
    return int(f) if f.is_integer() and "." not in raw and "e" not in raw.lower() else f


def parse_param_arg(arg: str) -> tuple[str, str, list]:
    """Parses a `--param section.key=v1,v2,...` sweep argument into
    (section, key, [coerced values]). Values are coerced to int/float where
    possible, else kept as strings (e.g. for exit_method=channel,atr)."""
    path, raw_values = arg.split("=", 1)
    section, key = path.split(".", 1)
    values = [_coerce_param_value(v) for v in raw_values.split(",")]
    return section, key, values


def _run_backtest_once(
    df: pd.DataFrame,
    strategy_name: str,
    strategy_config: dict,
    backtest_config: dict,
    timeframe: str,
    funding_df: pd.DataFrame | None = None,
) -> dict:
    strategy = _build_strategy(strategy_name, strategy_config, funding_df=funding_df)
    result = run_backtest(
        df,
        strategy,
        fee=backtest_config.get("fee", 0.001),
        slippage=backtest_config.get("slippage", 0.0005),
        initial_capital=backtest_config.get("initial_capital", 10_000.0),
        risk_pct=backtest_config.get("risk_pct", 0.01),
    )
    summary = summarize(
        result.trades,
        result.equity_curve,
        backtest_config.get("initial_capital", 10_000.0),
        timeframe,
        close=df["close"],
    )
    by_strategy = breakdown_by_strategy(result.trades)
    if len(by_strategy) > 1:
        summary["by_strategy"] = by_strategy
    return summary


HOLDOUT_LOG_PATH = Path("notes/holdout_validations.md")


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


def log_holdout_validation(
    strategy_label: str, symbol: str, timeframe: str, start: str | None, end: str | None, summary: dict
) -> None:
    """Every deliberate holdout check gets appended here — a visible audit
    trail of when the reserved out-of-sample data was actually consulted,
    since that should happen rarely and deliberately, not routinely."""
    HOLDOUT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HOLDOUT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n## {pd.Timestamp.now(tz='UTC').isoformat()} — {strategy_label} on {symbol} {timeframe}\n")
        f.write(f"- window: {start or '(full history)'} to {end or '(latest)'}\n")
        f.write(f"- result: {summary}\n")


def _build_strategy(
    name: str,
    strategy_config: dict,
    funding_df: pd.DataFrame | None = None,
    funding_refresh_fn=None,
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
    if name == "funding_filtered":
        # base_strategy/high_threshold/low_threshold are config-driven (same
        # pattern as regime_switched's trend_strategy), not separate
        # --strategy choices — funding_df/funding_refresh_fn come from the
        # caller since building them needs a DB connection this function
        # doesn't otherwise take.
        funding_filtered_config = strategy_config.get("funding_filtered", {})
        base_name = funding_filtered_config.get("base_strategy", "donchian")
        base = _build_trend_strategy(base_name, strategy_config)
        high_threshold = funding_filtered_config.get("high_threshold", 0.0005)
        low_threshold = funding_filtered_config.get("low_threshold", -0.0005)
        return FundingFilteredStrategy(
            base,
            funding_df if funding_df is not None else _empty_funding_df(),
            high_threshold,
            low_threshold,
            refresh_fn=funding_refresh_fn,
        )

    # regime_switched: trend/ranging sub-strategies and regime-filter type are
    # config-driven (spec Section 1), not separate --strategy choices, so
    # e.g. swapping ADX for SMA(200) or ema_cross for donchian is a
    # config/config.yaml edit, not a code change. ranging_strategy defaults to
    # "flat" (spec Section 8 pilot findings: rsi_bb didn't demonstrate a real
    # edge and is opt-in only) rather than "rsi_bb".
    regime_switched_config = strategy_config.get("regime_switched", {})
    trend_name = regime_switched_config.get("trend_strategy", "ema_cross")
    ranging_name = regime_switched_config.get("ranging_strategy", "flat")
    trending_strategy = _build_trend_strategy(trend_name, strategy_config)
    ranging_strategy = _build_ranging_strategy(ranging_name, strategy_config)
    regime_filter = _build_regime_filter(strategy_config)
    return RegimeSwitchedStrategy(trending_strategy, ranging_strategy, regime_filter)


def main() -> None:
    parser = argparse.ArgumentParser(description="BTC/USD signal bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill_parser = subparsers.add_parser(
        "backfill", help="Backfill historical OHLCV candles into SQLite"
    )
    backfill_parser.add_argument("--symbol", default=None)
    backfill_parser.add_argument("--timeframe", required=True)
    backfill_parser.add_argument("--start", default=None, help="ISO8601, e.g. 2020-01-01T00:00:00Z")
    backfill_parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore any already-stored candles and fetch from --start regardless",
    )

    poll_parser = subparsers.add_parser(
        "poll", help="Poll for the latest candle on a fixed interval"
    )
    poll_parser.add_argument("--symbol", default=None)
    poll_parser.add_argument("--timeframe", default=None)
    poll_parser.add_argument("--interval", type=int, default=None, help="seconds between polls")

    backtest_parser = subparsers.add_parser(
        "backtest", help="Backtest a strategy against stored candles"
    )
    backtest_parser.add_argument("--strategy", choices=STRATEGY_CHOICES, required=True)
    backtest_parser.add_argument("--symbol", default=None)
    backtest_parser.add_argument("--timeframe", required=True)
    backtest_parser.add_argument("--start", default=None, help="ISO8601, restricts the backtest window")
    backtest_parser.add_argument("--end", default=None, help="ISO8601, restricts the backtest window")
    backtest_parser.add_argument(
        "--trend-strategy",
        choices=TREND_STRATEGY_CHOICES,
        default=None,
        help="only for --strategy regime_switched: overrides strategy.regime_switched.trend_strategy",
    )
    backtest_parser.add_argument(
        "--ranging-strategy",
        choices=["flat", "rsi_bb"],
        default=None,
        help="only for --strategy regime_switched: overrides strategy.regime_switched.ranging_strategy",
    )
    backtest_parser.add_argument(
        "--regime-type",
        choices=["adx", "sma200", "natr"],
        default=None,
        help="only for --strategy regime_switched: overrides strategy.regime.type",
    )
    backtest_parser.add_argument(
        "--exit-channel-period",
        type=int,
        default=None,
        help="donchian (standalone or as regime_switched's trend strategy): "
        "overrides strategy.donchian.exit_channel_period",
    )
    backtest_parser.add_argument(
        "--exit-method",
        choices=["channel", "atr"],
        default=None,
        help="donchian: overrides strategy.donchian.exit_method",
    )
    backtest_parser.add_argument(
        "--donchian-atr-period",
        type=int,
        default=None,
        help="donchian with --exit-method atr: overrides strategy.donchian.atr_period",
    )
    backtest_parser.add_argument(
        "--donchian-atr-mult",
        type=float,
        default=None,
        help="donchian with --exit-method atr: overrides strategy.donchian.atr_mult",
    )
    backtest_parser.add_argument(
        "--rsi-oversold", type=float, default=None, help="overrides strategy.rsi_bb.rsi_oversold"
    )
    backtest_parser.add_argument(
        "--rsi-overbought", type=float, default=None, help="overrides strategy.rsi_bb.rsi_overbought"
    )
    backtest_parser.add_argument(
        "--bb-std", type=float, default=None, help="overrides strategy.rsi_bb.bb_std"
    )
    backtest_parser.add_argument(
        "--stop-band-mult", type=float, default=None, help="overrides strategy.rsi_bb.stop_band_mult"
    )
    backtest_parser.add_argument(
        "--funding-base-strategy",
        choices=TREND_STRATEGY_CHOICES,
        default=None,
        help="only for --strategy funding_filtered: overrides strategy.funding_filtered.base_strategy",
    )
    backtest_parser.add_argument(
        "--funding-high-threshold",
        type=float,
        default=None,
        help="only for --strategy funding_filtered: overrides strategy.funding_filtered.high_threshold "
        "(veto new longs when funding rate exceeds this)",
    )
    backtest_parser.add_argument(
        "--funding-low-threshold",
        type=float,
        default=None,
        help="only for --strategy funding_filtered: overrides strategy.funding_filtered.low_threshold "
        "(veto new shorts when funding rate is below this)",
    )
    backtest_parser.add_argument(
        "--allow-holdout",
        action="store_true",
        help="include the reserved out-of-sample window (validation.holdout_start in config.yaml) "
        "instead of excluding it — use this only for a deliberate final confirmatory check per "
        "candidate strategy, never during routine tuning. Logged to notes/holdout_validations.md.",
    )

    sweep_parser = subparsers.add_parser(
        "sweep", help="Grid-search strategy parameters, print a ranked comparison table"
    )
    sweep_parser.add_argument("--strategy", choices=STRATEGY_CHOICES, required=True)
    sweep_parser.add_argument("--symbol", default=None)
    sweep_parser.add_argument("--timeframe", required=True)
    sweep_parser.add_argument("--start", default=None, help="ISO8601, restricts the backtest window")
    sweep_parser.add_argument("--end", default=None, help="ISO8601, restricts the backtest window")
    sweep_parser.add_argument(
        "--trend-strategy",
        choices=TREND_STRATEGY_CHOICES,
        default=None,
        help="only for --strategy regime_switched: overrides strategy.regime_switched.trend_strategy "
        "for every combination in the sweep (use --param if you want to sweep this too)",
    )
    sweep_parser.add_argument(
        "--ranging-strategy",
        choices=["flat", "rsi_bb"],
        default=None,
        help="only for --strategy regime_switched: overrides strategy.regime_switched.ranging_strategy "
        "for every combination in the sweep",
    )
    sweep_parser.add_argument(
        "--regime-type",
        choices=["adx", "sma200", "natr"],
        default=None,
        help="only for --strategy regime_switched: overrides strategy.regime.type for every "
        "combination in the sweep",
    )
    sweep_parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="SECTION.KEY=V1,V2,...",
        help="repeatable; grid-searches the cartesian product of every --param's value list, e.g. "
        "--param rsi_bb.rsi_oversold=20,25,30 --param rsi_bb.stop_band_mult=0.5,0.75,1.0",
    )
    sweep_parser.add_argument(
        "--rank-by",
        choices=["profit_factor", "total_return_pct", "sharpe_ratio", "win_rate_pct"],
        default="profit_factor",
        help="sort the printed table by this metric, best first",
    )

    shadow_parser = subparsers.add_parser(
        "shadow",
        help="Phase 4: run a strategy live in paper/alert-only mode — no orders placed, ever, "
        "at this stage. Multiple concurrent shadow runs (distinct --strategy-label) can share "
        "the same candle data and Telegram chat with fully independent paper positions.",
    )
    shadow_parser.add_argument("--symbol", default=None)
    shadow_parser.add_argument("--timeframe", default=None)
    shadow_parser.add_argument("--interval", type=int, default=None, help="seconds between checks")
    shadow_parser.add_argument(
        "--strategy",
        choices=STRATEGY_CHOICES,
        default="regime_switched",
        help="defaults to regime_switched (the deployed control: donchian trend, flat ranging)",
    )
    shadow_parser.add_argument(
        "--strategy-label",
        default=None,
        help="identifies this run's paper-trading state (DB rows, Telegram messages) so concurrent "
        "shadow runs don't collide; defaults to --strategy's name",
    )
    _add_strategy_override_args(shadow_parser)

    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="Print a strategy's current read of the market right now — regime state, distance to "
        "entry, near-miss state — without waiting for a live signal or an alert. For manually "
        "sanity-checking the bot against your own reading of the chart (e.g. \"BTC looks like it's "
        "pulling back after a trend, why hasn't multi_timeframe fired?\").",
    )
    diagnose_parser.add_argument("--symbol", default=None)
    diagnose_parser.add_argument("--timeframe", default=None)
    diagnose_parser.add_argument(
        "--strategy", choices=STRATEGY_CHOICES, default="regime_switched",
    )
    _add_strategy_override_args(diagnose_parser)

    args = parser.parse_args()

    config = load_config()
    configure_logging(**config["logging"])

    exchange_id = config["exchange"]["id"]
    symbol = args.symbol or config["exchange"]["symbol"]
    exchange = create_exchange(exchange_id)
    conn = connect(config["storage"]["db_path"])

    if args.command == "backfill":
        start_date = args.start or config["backfill"]["start_date"]
        total = backfill_candles(
            exchange, conn, exchange_id, symbol, args.timeframe, start_date,
            resume=not args.no_resume,
        )
        logger.info("backfill complete: %d candles stored for %s %s", total, symbol, args.timeframe)

    elif args.command == "poll":
        timeframe = args.timeframe or config["poll"]["timeframe"]
        interval = args.interval or config["poll"]["interval_seconds"]
        run_poll_loop(exchange, conn, exchange_id, symbol, timeframe, interval)

    elif args.command == "backtest":
        df = query_candles_df(conn, exchange_id, symbol, args.timeframe)
        if args.start:
            df = df[df.index >= pd.Timestamp(args.start, tz="UTC")]
        if args.end:
            df = df[df.index <= pd.Timestamp(args.end, tz="UTC")]

        holdout_start = config.get("validation", {}).get("holdout_start")
        df, touched_holdout = apply_holdout_guard(
            df, holdout_start, args.allow_holdout, f"backtest {args.strategy}"
        )

        strategy_config = config.get("strategy", {})
        backtest_config = config.get("backtest", {})

        strategy_config = _with_override(
            strategy_config, "regime_switched", "trend_strategy", args.trend_strategy
        )
        strategy_config = _with_override(
            strategy_config, "regime_switched", "ranging_strategy", args.ranging_strategy
        )
        strategy_config = _with_override(strategy_config, "regime", "type", args.regime_type)
        strategy_config = _with_override(
            strategy_config, "donchian", "exit_channel_period", args.exit_channel_period
        )
        strategy_config = _with_override(strategy_config, "donchian", "exit_method", args.exit_method)
        strategy_config = _with_override(
            strategy_config, "donchian", "atr_period", args.donchian_atr_period
        )
        strategy_config = _with_override(
            strategy_config, "donchian", "atr_mult", args.donchian_atr_mult
        )
        strategy_config = _with_override(strategy_config, "rsi_bb", "rsi_oversold", args.rsi_oversold)
        strategy_config = _with_override(
            strategy_config, "rsi_bb", "rsi_overbought", args.rsi_overbought
        )
        strategy_config = _with_override(strategy_config, "rsi_bb", "bb_std", args.bb_std)
        strategy_config = _with_override(
            strategy_config, "rsi_bb", "stop_band_mult", args.stop_band_mult
        )
        strategy_config = _with_override(
            strategy_config, "funding_filtered", "base_strategy", args.funding_base_strategy
        )
        strategy_config = _with_override(
            strategy_config, "funding_filtered", "high_threshold", args.funding_high_threshold
        )
        strategy_config = _with_override(
            strategy_config, "funding_filtered", "low_threshold", args.funding_low_threshold
        )

        funding_df = _load_funding_df(config, conn) if args.strategy == "funding_filtered" else None
        summary = _run_backtest_once(
            df, args.strategy, strategy_config, backtest_config, args.timeframe, funding_df
        )

        logger.info(
            "backtest complete: %s %s %s over %d candles -> %s",
            args.strategy, symbol, args.timeframe, len(df), summary,
        )
        print(summary)

        if touched_holdout:
            log_holdout_validation(args.strategy, symbol, args.timeframe, args.start, args.end, summary)
            logger.warning("Holdout check logged to %s", HOLDOUT_LOG_PATH)

    elif args.command == "sweep":
        df = query_candles_df(conn, exchange_id, symbol, args.timeframe)
        if args.start:
            df = df[df.index >= pd.Timestamp(args.start, tz="UTC")]
        if args.end:
            df = df[df.index <= pd.Timestamp(args.end, tz="UTC")]

        # sweep never gets holdout access, no override flag — a grid search
        # is exploration/tuning by definition, exactly what the reserved
        # window exists to stay untouched by. A single deliberate --allow-holdout
        # backtest is the only sanctioned way to consult it.
        holdout_start = config.get("validation", {}).get("holdout_start")
        df, _ = apply_holdout_guard(df, holdout_start, allow_holdout=False, context_label=f"sweep {args.strategy}")

        base_strategy_config = config.get("strategy", {})
        backtest_config = config.get("backtest", {})
        base_strategy_config = _with_override(
            base_strategy_config, "regime_switched", "trend_strategy", args.trend_strategy
        )
        base_strategy_config = _with_override(
            base_strategy_config, "regime_switched", "ranging_strategy", args.ranging_strategy
        )
        base_strategy_config = _with_override(base_strategy_config, "regime", "type", args.regime_type)

        # loaded once up front (not per combo) — funding history doesn't
        # depend on any strategy parameter being swept; sweep thresholds
        # themselves via --param funding_filtered.high_threshold=v1,v2,...
        funding_df = _load_funding_df(config, conn) if args.strategy == "funding_filtered" else None

        grid = [parse_param_arg(p) for p in args.param]
        sections_keys = [(section, key) for section, key, _ in grid]
        value_lists = [values for _, _, values in grid]
        combos = list(itertools.product(*value_lists)) if grid else [()]

        rows = []
        for combo in combos:
            strategy_config = base_strategy_config
            for (section, key), value in zip(sections_keys, combo):
                strategy_config = _with_override(strategy_config, section, key, value)

            summary = _run_backtest_once(
                df, args.strategy, strategy_config, backtest_config, args.timeframe, funding_df
            )
            params_label = ", ".join(f"{s}.{k}={v}" for (s, k), v in zip(sections_keys, combo))
            rows.append((params_label or "(defaults)", summary))

        rows.sort(key=lambda row: row[1].get(args.rank_by, 0.0), reverse=True)

        logger.info(
            "sweep complete: %s %s %s over %d candles, %d combo(s) ranked by %s",
            args.strategy, symbol, args.timeframe, len(df), len(rows), args.rank_by,
        )
        for params_label, summary in rows:
            print(f"{params_label} -> {summary}")

    elif args.command == "shadow":
        timeframe = args.timeframe or config["poll"]["timeframe"]
        interval = args.interval or config["poll"]["interval_seconds"]
        strategy_config = _apply_strategy_overrides(config.get("strategy", {}), args)
        backtest_config = config.get("backtest", {})
        alerting_config = config.get("alerting", {})
        strategy_label = args.strategy_label or args.strategy

        funding_df = None
        funding_refresh_fn = None
        if args.strategy == "funding_filtered":
            funding_df = _load_funding_df(config, conn)
            # live shadow runs need fresh funding data (~every 8h) —
            # backtests/sweeps get a fixed funding_df for the whole
            # historical window and pass no refresh_fn
            funding_refresh_fn = lambda: _load_funding_df(config, conn)  # noqa: E731

        strategy = _build_strategy(
            args.strategy, strategy_config, funding_df=funding_df, funding_refresh_fn=funding_refresh_fn
        )

        alerter = TelegramAlerter(
            os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        )
        if not alerter.enabled:
            logger.warning(
                "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set in .env — "
                "shadow run continues, but alerts will only be logged, not sent"
            )

        run_shadow_loop(
            exchange, conn, alerter, exchange_id, symbol, timeframe, strategy, strategy_label,
            fee=backtest_config.get("fee", 0.001),
            slippage=backtest_config.get("slippage", 0.0005),
            backfill_start_date=config["backfill"]["start_date"],
            interval_seconds=interval,
            heartbeat_interval_seconds=alerting_config.get("heartbeat_interval_seconds", 86400),
        )

    elif args.command == "diagnose":
        timeframe = args.timeframe or config["poll"]["timeframe"]
        strategy_config = _apply_strategy_overrides(config.get("strategy", {}), args)

        funding_df = _load_funding_df(config, conn) if args.strategy == "funding_filtered" else None
        strategy = _build_strategy(args.strategy, strategy_config, funding_df=funding_df)

        # refresh candle data first so the diagnosis reflects the latest
        # close, same as a live shadow poll would — not whatever happens to
        # already be sitting in the local DB
        backfill_candles(
            exchange, conn, exchange_id, symbol, timeframe, config["backfill"]["start_date"], resume=True
        )
        df = query_candles_df(conn, exchange_id, symbol, timeframe).tail(
            max(500, strategy.min_lookback + 5)
        )
        df = drop_incomplete_bar(df, timeframe)

        if len(df) < strategy.min_lookback:
            print(f"not enough complete history yet ({len(df)}/{strategy.min_lookback} bars)")
        else:
            signal = strategy.generate_signal(df)
            diagnosis = strategy.diagnose(df)
            print(f"=== {args.strategy} diagnosis for {symbol} {timeframe} as of {df.index[-1]} ===")
            if signal is not None:
                print(
                    f"SIGNAL WOULD FIRE: {signal.direction} @ {signal.entry_price:.2f} "
                    f"stop={signal.stop_loss:.2f} reason={signal.reason}"
                )
            else:
                print("no signal right now")
            for key, value in diagnosis.items():
                print(f"  {key}: {value}")


if __name__ == "__main__":
    main()

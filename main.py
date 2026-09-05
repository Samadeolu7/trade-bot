import argparse
import logging

import pandas as pd

from bot.backtest.engine import run_backtest
from bot.backtest.metrics import breakdown_by_strategy, summarize
from bot.config import load_config
from bot.data.backfill import backfill_candles
from bot.data.exchange import create_exchange
from bot.data.poll import run_poll_loop
from bot.logging_setup import configure_logging
from bot.storage.db import connect, query_candles_df
from bot.strategy.donchian import DonchianBreakoutStrategy
from bot.strategy.ema_cross import EmaCrossStrategy
from bot.strategy.regime import RegimeFilter, Sma200RegimeFilter
from bot.strategy.regime_switch import RegimeSwitchedStrategy
from bot.strategy.rsi_bb import RsiBollingerStrategy
from bot.strategy.base import Strategy

logger = logging.getLogger(__name__)

STRATEGY_CHOICES = ["ema_cross", "rsi_bb", "donchian", "regime_switched"]


def _build_trend_strategy(name: str, strategy_config: dict) -> Strategy:
    if name == "donchian":
        return DonchianBreakoutStrategy(strategy_config.get("donchian", {}))
    return EmaCrossStrategy(strategy_config.get("ema_cross", {}))


def _build_regime_filter(strategy_config: dict):
    regime_config = strategy_config.get("regime", {})
    if regime_config.get("type", "adx") == "sma200":
        return Sma200RegimeFilter(**strategy_config.get("regime_sma", {}))
    return RegimeFilter(
        adx_period=regime_config.get("adx_period", 14),
        adx_threshold=regime_config.get("adx_threshold", 25),
    )


def _with_override(strategy_config: dict, section: str, key: str, value) -> dict:
    if value is None:
        return strategy_config
    return {**strategy_config, section: {**strategy_config.get(section, {}), key: value}}


def _build_strategy(name: str, strategy_config: dict) -> Strategy:
    if name == "ema_cross":
        return EmaCrossStrategy(strategy_config.get("ema_cross", {}))
    if name == "rsi_bb":
        return RsiBollingerStrategy(strategy_config.get("rsi_bb", {}))
    if name == "donchian":
        return DonchianBreakoutStrategy(strategy_config.get("donchian", {}))

    # regime_switched: trend sub-strategy and regime-filter type are
    # config-driven (spec Section 1), not separate --strategy choices, so
    # e.g. swapping ADX for SMA(200) or ema_cross for donchian is a
    # config/config.yaml edit, not a code change.
    rsi_strategy = RsiBollingerStrategy(strategy_config.get("rsi_bb", {}))
    trend_name = strategy_config.get("regime_switched", {}).get("trend_strategy", "ema_cross")
    trending_strategy = _build_trend_strategy(trend_name, strategy_config)
    regime_filter = _build_regime_filter(strategy_config)
    return RegimeSwitchedStrategy(trending_strategy, rsi_strategy, regime_filter)


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
        choices=["ema_cross", "donchian"],
        default=None,
        help="only for --strategy regime_switched: overrides strategy.regime_switched.trend_strategy",
    )
    backtest_parser.add_argument(
        "--regime-type",
        choices=["adx", "sma200"],
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
            df = df[df.index >= pd.Timestamp(args.start)]
        if args.end:
            df = df[df.index <= pd.Timestamp(args.end)]

        strategy_config = config.get("strategy", {})
        backtest_config = config.get("backtest", {})

        strategy_config = _with_override(
            strategy_config, "regime_switched", "trend_strategy", args.trend_strategy
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

        strategy = _build_strategy(args.strategy, strategy_config)

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
            args.timeframe,
            close=df["close"],
        )
        by_strategy = breakdown_by_strategy(result.trades)
        if len(by_strategy) > 1:
            summary["by_strategy"] = by_strategy

        logger.info(
            "backtest complete: %s %s %s over %d candles -> %s",
            args.strategy, symbol, args.timeframe, len(df), summary,
        )
        print(summary)


if __name__ == "__main__":
    main()

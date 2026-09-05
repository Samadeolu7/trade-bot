import argparse
import logging

from bot.config import load_config
from bot.data.backfill import backfill_candles
from bot.data.exchange import create_exchange
from bot.data.poll import run_poll_loop
from bot.logging_setup import configure_logging
from bot.storage.db import connect

logger = logging.getLogger(__name__)


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


if __name__ == "__main__":
    main()

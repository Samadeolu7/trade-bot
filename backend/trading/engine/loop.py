"""The engine: one process, separate from the website, that runs every
bot and watches every stop.

Every tick (a few seconds):
  - heartbeat, so the app can show when the engine is down
  - fill resting paper limit orders that the price has reached
  - trigger engine-held stops and take-profits (Quidax has no stop orders,
    so this loop is what protects open positions)
  - publish fresh quotes to the browser
Every bar poll (about a minute):
  - fetch new candles for every (symbol, timeframe) a running bot uses
  - evaluate each running bot once per newly completed bar
Every few minutes: equity snapshots. Daily: CFD financing on paper accounts.

Each account, position and bot is handled in isolation: one failing never
stops the others."""

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone as dj_timezone

from bot.broker.base import BrokerError
from bot.data.exchange import create_exchange
from bot.shadow.runner import drop_incomplete_bar
from market.candles import backfill_candles, backfill_funding, candles_df, funding_df
from market.health import check_series
from alerts.models import AlertRule
from alerts.service import PriceHistory, daily_summaries, evaluate_prices, notify
from trading.engine.adapters import Outcome, evaluate_exposure_bot, evaluate_signal_bot, jsonable
from recommendations.models import Feed
from recommendations.runner import run_feed
from trading.events import SYSTEM_GROUP, market_group, publish
from trading.models import Bot, BotDecision, EquitySnapshot, LedgerEntry, Order, Position, TradingAccount
from trading.services import books
from trading.services.bots import TIMEFRAMES, build_bot_strategy, trade_bot_config
from trading.services.brokers import get_broker, quote_source
from trading.services.orders import close_position, sync_open_orders

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "engine:heartbeat"
QUOTE_KEY = "quote:{venue}:{symbol}"
MAX_CONSECUTIVE_ERRORS = 5
# signal strategies look at a trailing window; exposure strategies (a
# 360-day lookback on 4h bars) need the whole history, like the paper runner
SIGNAL_HISTORY_BARS = 500
CHART_SYMBOL = "BTC/USDT"


def heartbeat(extra: dict | None = None) -> None:
    cache.set(HEARTBEAT_KEY, {"at": dj_timezone.now().isoformat(), **(extra or {})}, timeout=None)


class Engine:
    def __init__(self):
        cfg = settings.ENGINE
        self.tick_seconds = cfg["tick_seconds"]
        self.bar_poll_seconds = cfg["bar_poll_seconds"]
        self.snapshot_seconds = cfg["equity_snapshot_seconds"]
        self.last_bar_poll = 0.0
        self.last_snapshot = 0.0
        self.financing_day = datetime.now(timezone.utc).date()
        trade_config = trade_bot_config()
        self.exchange_id = trade_config["exchange"]["id"]
        self.backfill_start = trade_config["backfill"]["start_date"]
        self.funding_config = trade_config.get("funding", {})
        self._exchange = None
        self.price_history = PriceHistory()

    @property
    def exchange(self):
        if self._exchange is None:
            self._exchange = create_exchange(self.exchange_id)
        return self._exchange

    # --- loop -----------------------------------------------------------------

    def run_forever(self) -> None:
        logger.info("engine started: tick %ss, bar poll %ss", self.tick_seconds, self.bar_poll_seconds)
        notify(AlertRule.Kind.BOT_ERROR, "Engine started", "Bots resume on their next completed bar.")
        while True:
            started = time.monotonic()
            try:
                self.tick()
            except Exception:
                logger.exception("engine tick failed")
            time.sleep(max(0.5, self.tick_seconds - (time.monotonic() - started)))

    def tick(self) -> None:
        now = time.monotonic()
        heartbeat({"tick_seconds": self.tick_seconds})
        self.sync_resting_orders()
        self.check_protections()
        mids = self.publish_quotes()
        try:
            evaluate_prices(mids, self.price_history)
            daily_summaries()
        except Exception:
            logger.exception("alert evaluation failed")
        if now - self.last_bar_poll >= self.bar_poll_seconds:
            self.last_bar_poll = now
            self.poll_bars()
        if now - self.last_snapshot >= self.snapshot_seconds:
            self.last_snapshot = now
            self.snapshot_equity()
        today = datetime.now(timezone.utc).date()
        if today != self.financing_day:
            self.financing_day = today
            self.charge_financing()

    # --- every tick -------------------------------------------------------------

    def sync_resting_orders(self) -> None:
        accounts = TradingAccount.objects.filter(
            is_active=True, orders__status=Order.Status.OPEN
        ).distinct()
        for account in accounts:
            try:
                sync_open_orders(account)
            except Exception:
                logger.exception("syncing resting orders for %s failed", account)

    def check_protections(self) -> None:
        positions = (
            Position.objects.exclude(quantity=0)
            .filter(Q(stop_price__isnull=False) | Q(take_profit__isnull=False))
            .select_related("account", "bot")
        )
        for position in positions:
            try:
                self._check_protection(position)
            except Exception:
                logger.exception("checking protection on position %s failed", position.pk)

    def _check_protection(self, position: Position) -> None:
        pending = Order.objects.filter(
            account=position.account, book=position.book, symbol=position.symbol,
            status__in=[Order.Status.PENDING, Order.Status.OPEN],
            source__in=[Order.Source.STOP, Order.Source.TAKE_PROFIT, Order.Source.CLOSE],
        ).exists()
        if pending:
            return
        quote = quote_source(position.account.venue).quote(position.symbol)
        long = position.quantity > 0
        # a long exits by selling at the bid, a short by buying at the ask
        price = quote.bid if long else quote.ask
        stop, tp = position.stop_price, position.take_profit
        if stop is not None and (price <= stop if long else price >= stop):
            source, reason = Order.Source.STOP, f"stop {stop:.2f} hit ({'bid' if long else 'ask'} {price:.2f})"
        elif tp is not None and (price >= tp if long else price <= tp):
            source, reason = Order.Source.TAKE_PROFIT, f"take-profit {tp:.2f} hit ({'bid' if long else 'ask'} {price:.2f})"
        else:
            return
        entry = position.average_price
        qty = position.quantity
        order = close_position(position, source=source, reason=reason)
        if order is None:
            return
        who = position.bot.name if position.bot else "Manual"
        what = "Stop" if source == Order.Source.STOP else "Take profit"
        if order.status == Order.Status.REJECTED:
            body = f"The exit order was rejected: {order.reject_reason}"
        else:
            pnl_pct = (order.average_price - entry) / entry * 100 * (1 if long else -1) if entry and order.average_price else None
            body = f"Closed {abs(qty)} {position.symbol} at {order.average_price:,.2f} (entry {entry:,.2f}"
            body += f", {pnl_pct:+.2f}%)" if pnl_pct is not None else ")"
        notify(AlertRule.Kind.STOP_HIT, f"{what} hit: {who} on {position.account.name}", body,
               account=position.account, bot=position.bot)

    def publish_quotes(self) -> dict:
        """Returns the mid price per (venue, symbol), for price alerts."""
        mids = {}
        pairs = set(
            Bot.objects.exclude(status=Bot.Status.STOPPED).values_list("account__venue", "symbol")
        ) | set(
            Position.objects.exclude(quantity=0).values_list("account__venue", "symbol")
        )
        pairs |= {(venue, "BTC/USDT") for venue in TradingAccount.objects.filter(is_active=True).values_list("venue", flat=True)}
        for venue, symbol in pairs:
            try:
                quote = quote_source(venue).quote(symbol)
            except BrokerError as exc:
                logger.warning("quote %s %s unavailable: %s", venue, symbol, exc)
                continue
            data = {
                "venue": venue, "symbol": symbol, "bid": str(quote.bid), "ask": str(quote.ask),
                "last": str(quote.last), "time": quote.time.isoformat(),
            }
            cache.set(QUOTE_KEY.format(venue=venue, symbol=symbol), data, timeout=120)
            publish(market_group(venue, symbol), "quote", data)
            mids[(venue, symbol)] = quote.mid
            self.price_history.add(venue, symbol, dj_timezone.now(), quote.mid)
        return mids

    # --- bars -------------------------------------------------------------------

    def poll_bars(self) -> None:
        bots = list(Bot.objects.filter(status=Bot.Status.RUNNING).select_related("account"))
        feeds = list(Feed.objects.filter(enabled=True))
        # the trade page's chart needs every timeframe, whether or not a bot uses it
        series = (
            {(b.symbol, b.timeframe) for b in bots}
            | {(f.symbol, f.timeframe) for f in feeds}
            | {(CHART_SYMBOL, tf) for tf in TIMEFRAMES}
        )
        for symbol, timeframe in sorted(series):
            try:
                backfill_candles(self.exchange, self.exchange_id, symbol, timeframe, self.backfill_start)
            except Exception:
                logger.exception("candle backfill %s %s failed", symbol, timeframe)
        if any(x.strategy == "funding_filtered" for x in [*bots, *feeds]):
            try:
                backfill_funding(self.funding_config.get("exchange_id", "binanceusdm"),
                                 self.funding_config.get("symbol", "BTC/USDT:USDT"),
                                 self.funding_config.get("start_date", self.backfill_start))
            except Exception:
                logger.exception("funding backfill failed")
        for bot in bots:
            self.run_bot(bot)
        for feed in feeds:
            try:
                run_feed(feed, self.exchange_id, self.funding_config)
            except Exception as exc:
                logger.exception("recommendation feed %s failed", feed.name)
                feed.status_reason = f"error: {exc}"[:300]
                feed.save(update_fields=["status_reason"])

    def run_bot(self, bot: Bot) -> BotDecision | None:
        try:
            return self._run_bot(bot)
        except Exception as exc:
            logger.exception("bot %s failed", bot.name)
            bot.consecutive_errors += 1
            bot.status_reason = f"error: {exc}"[:300]
            if bot.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                bot.status = Bot.Status.ERROR
                notify(AlertRule.Kind.BOT_ERROR, f"{bot.name} stopped after {bot.consecutive_errors} errors",
                       f"{str(exc)[:300]}. Restart it from the app once fixed.", account=bot.account, bot=bot)
            bot.save(update_fields=["consecutive_errors", "status_reason", "status"])
            publish(f"account.{bot.account_id}", "bot", {"id": bot.pk})
            return None

    def _run_bot(self, bot: Bot) -> BotDecision | None:
        funding = None
        if bot.strategy == "funding_filtered":
            funding = funding_df(self.funding_config.get("exchange_id", "binanceusdm"),
                                 self.funding_config.get("symbol", "BTC/USDT:USDT"))
        strategy = build_bot_strategy(bot, funding_df=funding)
        exposure = hasattr(strategy, "target_weights")
        limit = None if exposure else max(SIGNAL_HISTORY_BARS, strategy.min_lookback + 5)
        df = drop_incomplete_bar(candles_df(self.exchange_id, bot.symbol, bot.timeframe, limit), bot.timeframe)

        if len(df) < strategy.min_lookback:
            reason = f"warming up: {len(df)}/{strategy.min_lookback} completed bars"
            if bot.status_reason != reason:
                bot.status_reason = reason
                bot.save(update_fields=["status_reason"])
            return None
        bar_time = df.index[-1].to_pydatetime()
        if bot.last_bar_at is not None and bar_time <= bot.last_bar_at:
            return None
        health = check_series(df, bot.timeframe)
        if not health.ok:
            # no decision from bad data; the bar is retried each tick in case
            # the candles are repaired, and stops are still watched live
            reason = f"data check failed: {health.summary()}"[:300]
            decision, _ = BotDecision.objects.update_or_create(
                bot=bot, bar_time=bar_time,
                defaults={"action": BotDecision.Action.BLOCKED, "reason": reason, "diagnosis": {},
                          "order": None, "price": float(df["close"].iloc[-1])},
            )
            if bot.status_reason != reason:
                bot.status_reason = reason
                bot.save(update_fields=["status_reason"])
                notify(AlertRule.Kind.BOT_ERROR, f"SIGNALS PAUSED: {bot.name}",
                       f"Reason: {reason}. No new trades from this bot until the data is healthy again; "
                       "open positions keep their stops.", account=bot.account, bot=bot)
            return decision

        quote = quote_source(bot.account.venue).quote(bot.symbol)
        adapter = evaluate_exposure_bot if exposure else evaluate_signal_bot
        with transaction.atomic():
            outcome: Outcome = adapter(bot, strategy, df, quote)
            decision, _ = BotDecision.objects.update_or_create(
                bot=bot, bar_time=bar_time,
                defaults={
                    "action": outcome.action, "reason": outcome.reason[:500],
                    "diagnosis": jsonable(outcome.diagnosis), "order": outcome.order,
                    "price": float(df["close"].iloc[-1]),
                },
            )
            bot.last_bar_at = bar_time
            bot.last_run_at = dj_timezone.now()
            bot.consecutive_errors = 0
            bot.status_reason = ""
            bot.save(update_fields=["last_bar_at", "last_run_at", "consecutive_errors", "status_reason"])
        publish(f"account.{bot.account_id}", "decision", {"bot": bot.pk, "action": outcome.action})
        self._near_miss(bot, outcome)
        self._repeat_signal(bot, outcome)

        if outcome.order is not None:
            order = outcome.order
            if order.status == Order.Status.REJECTED:
                title = f"{bot.name}: order rejected"
            else:
                verb = "bought" if order.side == "buy" else "sold"
                title = f"{bot.name} {verb} {order.filled_quantity.normalize()} {bot.symbol.split('/')[0]}"
                if order.average_price:
                    title += f" at {order.average_price:,.2f}"
            notify(AlertRule.Kind.BOT_TRADE, title, f"{outcome.reason} ({bot.account.name})",
                   account=bot.account, bot=bot)
        return decision

    def _near_miss(self, bot: Bot, outcome: Outcome) -> None:
        """A flat bot whose strategy says an entry looks close: alert once per
        condition, as the CLI's shadow runs did."""
        diagnosis = outcome.diagnosis
        near = outcome.action == BotDecision.Action.NONE and diagnosis.get("near_miss")
        key = diagnosis.get("near_miss_key") if near else None
        if (key or "") == bot.near_miss_key:
            return
        bot.near_miss_key = key or ""
        bot.save(update_fields=["near_miss_key"])
        if key:
            notify(AlertRule.Kind.NEAR_MISS, f"Near miss: {bot.name} ({bot.timeframe})",
                   diagnosis.get("near_miss_reason") or key, account=bot.account, bot=bot)

    def _repeat_signal(self, bot: Bot, outcome: Outcome) -> None:
        """Entry conditions met again while in a trade: alert once per
        position and direction, not on every candle it stays true."""
        repeat = outcome.repeat_signal
        if repeat is None:
            return
        position = Position.objects.filter(book=bot.book, symbol=bot.symbol).first()
        opened = position.opened_at.isoformat() if position and position.opened_at else ""
        key = f"{opened}:{repeat['direction']}"[:80]
        if key == bot.repeat_signal_key:
            return
        bot.repeat_signal_key = key
        bot.save(update_fields=["repeat_signal_key"])
        held = position.direction if position else ""
        if repeat["direction"] == held:
            title = f"{bot.name}: {held} entry signal again, already {held}"
            body = f"{repeat['reason']} at {repeat['entry']:,.2f}. Not adding: bots hold one position at a time."
        else:
            title = f"{bot.name}: {repeat['direction']} signal while {held}"
            body = f"{repeat['reason']} at {repeat['entry']:,.2f}. Staying {held} until its stop."
        notify(AlertRule.Kind.REPEAT_SIGNAL, title, body, account=bot.account, bot=bot)

    # --- periodic -----------------------------------------------------------------

    def snapshot_equity(self) -> None:
        now = dj_timezone.now()
        for account in TradingAccount.objects.filter(is_active=True):
            try:
                all_books = books.all_book_balances(account)
                assets = {a for balances in all_books.values() for a in balances}
                prices = books.marks(account, assets)
                total = Decimal(0)
                rows = []
                for book, balances in all_books.items():
                    value = books.equity(balances, account.quote_asset, prices)
                    total += value
                    rows.append(EquitySnapshot(account=account, book=book, time=now, equity=value))
                rows.append(EquitySnapshot(account=account, book="", time=now, equity=total))
                EquitySnapshot.objects.bulk_create(rows)
            except Exception:
                logger.exception("equity snapshot for %s failed", account)
        publish(SYSTEM_GROUP, "equity", {})

    def charge_financing(self) -> None:
        """Daily CFD swap on paper CFD accounts, charged to each book in
        proportion to its own position."""
        accounts = TradingAccount.objects.filter(is_active=True, mode=TradingAccount.Mode.PAPER)
        for account in accounts:
            venue = account.venue_profile
            if venue.kind != "cfd" or not venue.daily_swap_rate:
                continue
            try:
                broker = get_broker(account)
                with transaction.atomic():
                    for position in Position.objects.filter(account=account).exclude(quantity=0):
                        mid = quote_source(account.venue).quote(position.symbol).mid
                        charge = abs(position.quantity) * mid * venue.daily_swap_rate
                        broker.deposit(venue.quote_asset, -charge)
                        books.post(account, position.book, LedgerEntry.Kind.FINANCING, venue.quote_asset,
                                   -charge, note=f"overnight swap on {position.quantity} {position.symbol}")
            except Exception:
                logger.exception("financing for %s failed", account)

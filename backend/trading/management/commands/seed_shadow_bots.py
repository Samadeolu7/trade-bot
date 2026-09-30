"""Recreates the CLI's deployed shadow runs (docker-compose.yml's trade-bot*
services) as paper bots on one Quidax paper account, continuing from each
run's current position rather than starting flat. Safe to re-run: bots that
already exist are left alone.

Carrying a position over, since the shadow runs had no capital:
- signal bots: sized as the engine would have sized that entry (risk_pct of
  the allocation over the entry-to-stop distance), booked at the shadow
  run's own entry price and time, with its current trailed stop.
- shorts: Quidax spot can't hold one, so that bot starts flat and says why.
- the ensemble: holds the same fraction of its allocation as the shadow
  run, costed at the last close it processed; it resumes after that bar.

The shadow runs' closed trades aren't replayed into the new books; they're
kept as history by `import_sqlite` (Research > shadow history).

It also recreates the MT5 recommendation feeds (config.yaml `recommend`),
continuing each feed's open call and importing its past entries, exits and
rebalances, so the Recommendations page picks up where Telegram left off."""

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bot.backtest.engine import position_size
from bot.broker.base import BrokerFill, OrderResult
from core.models import User
from recommendations.models import Feed, Recommendation
from research.legacy import open_legacy_db
from trading.models import Bot, BotDecision, Order, TradingAccount
from trading.services.bots import create_bot, set_bot_status, trade_bot_config
from trading.services.brokers import DbPaperStore
from trading.services.orders import apply_result, fund_paper_account

SYMBOL = "BTC/USDT"
EXCHANGE = "binance"

# name (= the shadow run's strategy_label), strategy, timeframe, params;
# mirrors docker-compose.yml's shadow services
SHADOW_RUNS = [
    ("donchian_adx_control", "regime_switched", "1d", {}),
    ("donchian_natr_regime", "regime_switched", "1d", {"regime.type": "natr"}),
    ("donchian_funding_filtered", "funding_filtered", "1d", {}),
    ("donchian_4h", "donchian", "4h", {}),
    ("donchian_adx_4h", "regime_switched", "4h", {}),
    ("donchian_ensemble_4h", "donchian_ensemble", "4h",
     {"donchian_ensemble.bars_per_day": 6, "donchian_ensemble.rebalance_threshold": 0.1}),
]


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def book_carried_buy(bot: Bot, qty: Decimal, price: Decimal, at: datetime, reason: str, stop=None) -> Order:
    """Books a fill at a historical price: the paper venue's balances and
    our books move together, exactly as for a live fill."""
    account = bot.account
    venue = account.venue_profile
    fee = qty * price * venue.taker_fee
    order = Order.objects.create(
        account=account, book=bot.book, bot=bot, source=Order.Source.BOT, symbol=bot.symbol, side="buy",
        order_type="market", quantity=qty, reason=reason[:500], stop_price=stop,
    )
    DbPaperStore(account).apply_balance_deltas({venue.base_asset(bot.symbol): qty, venue.quote_asset: -(qty * price + fee)})
    apply_result(order, OrderResult(f"carry-{bot.pk}", "filled", [BrokerFill(price, qty, fee, "taker", at)]))
    return order


class Command(BaseCommand):
    help = "Recreate the deployed shadow runs as paper bots, continuing from their current positions."

    def add_arguments(self, parser):
        parser.add_argument("sqlite_path", help="the shadow runs' trades.db")
        parser.add_argument("--account", default="Quidax paper")
        parser.add_argument("--owner", required=True, help="username the bots are created by")
        parser.add_argument("--per-bot", type=Decimal, default=Decimal(10_000))
        parser.add_argument("--manual", type=Decimal, default=Decimal(10_000),
                            help="extra paper cash left in the manual book for your own trades")

    def handle(self, *args, sqlite_path, account, owner, per_bot, manual, **options):
        user = User.objects.filter(username=owner).first()
        if user is None:
            raise CommandError(f"no user {owner!r}")
        conn = open_legacy_db(sqlite_path)
        self._seed_bots(conn, user, account, per_bot, manual)
        self._seed_feeds(conn)

    def _seed_bots(self, conn, user, account, per_bot, manual):
        with transaction.atomic():
            acct, created = TradingAccount.objects.get_or_create(name=account, defaults={"venue": "quidax_spot"})
            missing = [run for run in SHADOW_RUNS if not Bot.objects.filter(name=run[0]).exists()]
            if not missing:
                self.stdout.write("every shadow run already has a bot")
                return
            fund_paper_account(acct, per_bot * len(missing) + (manual if created else 0), user=user)

            for name, strategy, timeframe, params in missing:
                bot = create_bot(acct, name=name, strategy=strategy, timeframe=timeframe, allocation=per_bot,
                                 params=params, user=user)
                if strategy == "donchian_ensemble":
                    note = self._carry_exposure(conn, bot, per_bot)
                else:
                    note = self._carry_signal(conn, bot, per_bot)
                set_bot_status(bot, Bot.Status.RUNNING, user=user, reason="")
                self.stdout.write(f"{name}: {note}")

    def _carry_signal(self, conn, bot: Bot, allocation: Decimal) -> str:
        row = conn.execute(
            "SELECT direction, entry_price, stop_loss, entry_time FROM paper_position "
            "WHERE exchange=? AND symbol=? AND timeframe=? AND strategy_label=?",
            (EXCHANGE, SYMBOL, bot.timeframe, bot.name),
        ).fetchone()
        if row is None:
            return "flat in the shadow run; starts flat"
        direction, entry, stop, entry_ms = row
        at = _dt(entry_ms)
        if direction != "long":
            BotDecision.objects.create(
                bot=bot, bar_time=at, action=BotDecision.Action.SKIP, price=entry,
                reason=f"shadow run was {direction} since {at:%Y-%m-%d %H:%M} at {entry:,.2f}; "
                       f"Quidax spot is long-only, so this bot starts flat",
            )
            return f"was {direction}; starts flat (spot can't short)"
        venue = bot.account.venue_profile
        price = Decimal(str(round(entry, 2)))
        size = Decimal(str(position_size(float(allocation), bot.risk_pct, entry, stop)))
        affordable = allocation / (price * (1 + venue.taker_fee))
        qty = venue.round_qty(min(size, affordable))
        if qty < venue.min_qty:
            return f"long position too small to carry ({qty}); starts flat"
        book_carried_buy(
            bot, qty, price, at, stop=Decimal(str(round(stop, 2))),
            reason=f"carried over from the shadow run: long since {at:%Y-%m-%d %H:%M} at {entry:,.2f}",
        )
        return f"carried long {qty} BTC from {entry:,.2f} ({at:%Y-%m-%d}), stop {stop:,.2f}"

    def _carry_exposure(self, conn, bot: Bot, allocation: Decimal) -> str:
        raw = conn.execute("SELECT value FROM bot_state WHERE key=?", (f"{bot.name}:exposure_state",)).fetchone()
        if raw is None:
            return "no shadow state yet; starts flat"
        state = json.loads(raw[0])
        held, close, last_ms = state.get("held") or 0.0, state.get("last_close"), state.get("last_bar_ms")
        if last_ms:
            bot.last_bar_at = _dt(last_ms)
            bot.save(update_fields=["last_bar_at"])
        if not held or not close:
            return "flat in the shadow run; starts flat"
        venue = bot.account.venue_profile
        price = Decimal(str(round(close, 2)))
        qty = venue.round_qty(allocation * Decimal(str(held)) / (price * (1 + venue.taker_fee)))
        if qty < venue.min_qty:
            return "position too small to carry; starts flat"
        at = _dt(last_ms)
        book_carried_buy(
            bot, qty, price, at,
            reason=f"carried over from the shadow run: {held:.1%} of capital at {close:,.2f}",
        )
        return f"carried {held:.1%} of capital ({qty} BTC at {close:,.2f}), resumes after {at:%Y-%m-%d %H:%M}"

    # --- recommendation feeds ---------------------------------------------------

    def _seed_feeds(self, conn):
        config = trade_bot_config()
        reco = config.get("recommend", {})
        default_timeframe = reco.get("timeframe") or config.get("poll", {}).get("timeframe", "1d")
        for entry in reco.get("strategies", []):
            name = entry["label"]
            if Feed.objects.filter(name=name).exists():
                continue
            params = dict(entry.get("params") or {})
            if entry.get("regime_type"):
                params["regime.type"] = entry["regime_type"]
            with transaction.atomic():
                feed = Feed.objects.create(
                    name=name, strategy=entry["strategy"], params=params,
                    timeframe=entry.get("timeframe", default_timeframe),
                    fee=reco.get("fee", 0.0), slippage=reco.get("slippage", 0.0003),
                )
                note = self._carry_feed(conn, feed)
                imported = self._import_feed_history(conn, feed)
            self.stdout.write(f"recommendations {name}: {note}; {imported} past calls imported")

    def _carry_feed(self, conn, feed: Feed) -> str:
        label = f"reco_{feed.name}"
        if feed.strategy == "donchian_ensemble":
            raw = conn.execute("SELECT value FROM bot_state WHERE key=?", (f"{label}:exposure_state",)).fetchone()
            if raw is None:
                return "no previous state; starts flat"
            state = json.loads(raw[0])
            feed.weight = state.get("held") or 0.0
            feed.equity = state.get("equity") or 10_000.0
            feed.last_close = state.get("last_close")
            if state.get("last_bar_ms"):
                feed.last_bar_at = _dt(state["last_bar_ms"])
            feed.save()
            return f"holding {feed.weight:.1%} of capital"
        row = conn.execute(
            "SELECT direction, entry_price, stop_loss, take_profit, entry_time, context FROM paper_position "
            "WHERE exchange=? AND symbol=? AND timeframe=? AND strategy_label=?",
            (EXCHANGE, SYMBOL, feed.timeframe, label),
        ).fetchone()
        if row is None:
            return "flat"
        direction, entry, stop, target, entry_ms, context = row
        feed.direction, feed.entry_price, feed.stop, feed.take_profit = direction, entry, stop, target
        feed.entry_time = _dt(entry_ms)
        feed.entry_context = json.loads(context) if context else {}
        feed.save()
        return f"{direction} since {feed.entry_time:%Y-%m-%d} at {entry:,.2f}, stop {stop:,.2f}"

    def _import_feed_history(self, conn, feed: Feed) -> int:
        label = f"reco_{feed.name}"
        events = []

        def rows(sql):
            try:
                return conn.execute(sql, (label,)).fetchall()
            except sqlite3.OperationalError:  # table absent in an older database
                return []

        for direction, entry, stop, target, reason, context, fired in rows(
            "SELECT direction, entry_price, stop_loss, take_profit, reason, context, fired_at "
            "FROM signals WHERE strategy_label=?"
        ):
            events.append(Recommendation(
                feed=feed, kind=Recommendation.Kind.ENTRY, bar_time=_dt(fired), direction=direction, price=entry,
                stop=stop, take_profit=target, reason=reason or "", context=json.loads(context) if context else {},
                imported=True,
            ))
        for direction, entry, exit_ms, exit_price, pnl_pct, exit_reason in rows(
            "SELECT direction, entry_price, exit_time, exit_price, pnl_pct, exit_reason "
            "FROM paper_trades WHERE strategy_label=?"
        ):
            events.append(Recommendation(
                feed=feed, kind=Recommendation.Kind.EXIT, bar_time=_dt(exit_ms), direction=direction,
                price=exit_price, pnl_pct=pnl_pct, imported=True,
                reason=f"{exit_reason.replace('_', ' ')} at {exit_price:,.2f} (entry {entry:,.2f})",
            ))
        for bar_ms, price, from_w, to_w in rows(
            "SELECT bar_time, price, from_weight, to_weight FROM exposure_rebalances WHERE strategy_label=?"
        ):
            events.append(Recommendation(
                feed=feed, kind=Recommendation.Kind.REBALANCE, bar_time=_dt(bar_ms), price=price,
                from_weight=from_w, to_weight=to_w, imported=True,
                reason=f"resize from {from_w:.1%} to {to_w:.1%} of capital",
            ))
        Recommendation.objects.bulk_create(events)
        return len(events)

"""Turns events into alerts. Two ways in:

- `notify(kind, ...)` for things that happen: a bot trade, a stop hit, a
  bot error, a finished research report. Every enabled rule of that kind
  whose scope matches fires.
- `evaluate_prices(...)` and `daily_summaries(...)`, called by the engine
  every tick, for rules that watch the market or the clock.

Each firing is logged as an AlertEvent (the in-app alert log), pushed to
the person's open pages, and sent to their Telegram chat if they have one."""

import logging
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from alerts import push
from alerts.models import AlertEvent, AlertRule
from bot.alerting.telegram import TelegramAlerter
from trading.events import _send
from trading.permissions import can_view_account, visible_accounts

logger = logging.getLogger(__name__)

PRICE_KINDS = (AlertRule.Kind.PRICE_ABOVE, AlertRule.Kind.PRICE_BELOW, AlertRule.Kind.PRICE_MOVE)


def user_group(user_id: int) -> str:
    return f"user.{user_id}"


def chat_for(user) -> str:
    if user.telegram_chat_id:
        return user.telegram_chat_id
    return settings.TELEGRAM_CHAT_ID if user.is_owner else ""


def deliver(user, title: str, body: str = "") -> bool:
    chat = chat_for(user)
    if not chat or not settings.TELEGRAM_BOT_TOKEN:
        return False
    text = f"{title}\n{body}" if body else title
    return TelegramAlerter(settings.TELEGRAM_BOT_TOKEN, chat).send(text)


def _cooling(rule: AlertRule, now: datetime) -> bool:
    return bool(rule.cooldown_minutes and rule.last_fired_at
                and now - rule.last_fired_at < timedelta(minutes=rule.cooldown_minutes))


def fire(rule: AlertRule, title: str, body: str = "", at: datetime | None = None) -> AlertEvent:
    delivered = deliver(rule.user, title, body)
    event = AlertEvent.objects.create(rule=rule, user=rule.user, kind=rule.kind, title=title[:200],
                                      body=body, delivered=delivered)
    try:
        push.send(rule.user, title, body, tag=f"alert-{event.pk}")
    except Exception:
        logger.exception("browser push for rule %s failed", rule.pk)
    rule.last_fired_at = at or event.created_at
    fields = ["last_fired_at", "state"]
    if rule.once:
        rule.enabled = False
        fields.append("enabled")
    rule.save(update_fields=fields)
    _send(user_group(rule.user_id), "alert", {"id": event.pk, "title": event.title, "body": event.body})
    return event


def notify(kind: str, title: str, body: str = "", *, account=None, bot=None) -> int:
    """Fires every matching rule of `kind`. Returns how many fired."""
    now = timezone.now()
    fired = 0
    rules = AlertRule.objects.filter(enabled=True, kind=kind).select_related("user")
    for rule in rules:
        if not rule.user.is_active:
            continue
        if rule.account_id and (account is None or rule.account_id != account.pk):
            continue
        if rule.bot_id and (bot is None or rule.bot_id != bot.pk):
            continue
        if account is not None and not can_view_account(rule.user, account.pk):
            continue
        if _cooling(rule, now):
            continue
        try:
            fire(rule, title, body)
            fired += 1
        except Exception:
            logger.exception("alert rule %s failed to fire", rule.pk)
    return fired


class PriceHistory:
    """Recent mid prices per (venue, symbol), kept in the engine's memory
    for sudden-move rules. Resets when the engine restarts."""

    def __init__(self, max_age: timedelta = timedelta(hours=24)):
        self.max_age = max_age
        self.series: dict[tuple[str, str], deque] = {}

    def add(self, venue: str, symbol: str, at: datetime, mid: Decimal) -> None:
        points = self.series.setdefault((venue, symbol), deque())
        points.append((at, mid))
        while points and at - points[0][0] > self.max_age:
            points.popleft()

    def price_at_or_after(self, venue: str, symbol: str, since: datetime) -> tuple[datetime, Decimal] | None:
        for at, mid in self.series.get((venue, symbol), ()):
            if at >= since:
                return at, mid
        return None


def _fmt(value) -> str:
    return f"{float(value):,.2f}"


def evaluate_prices(mids: dict[tuple[str, str], Decimal], history: PriceHistory, now: datetime | None = None) -> int:
    now = now or timezone.now()
    fired = 0
    for rule in AlertRule.objects.filter(enabled=True, kind__in=PRICE_KINDS).select_related("user"):
        p = rule.params
        key = (p.get("venue", "quidax_spot"), p.get("symbol", "BTC/USDT"))
        mid = mids.get(key)
        if mid is None or not rule.user.is_active:
            continue
        where = f"{key[1]} on {key[0].replace('_', ' ')}"
        try:
            if rule.kind in (AlertRule.Kind.PRICE_ABOVE, AlertRule.Kind.PRICE_BELOW):
                level = Decimal(str(p["price"]))
                last = rule.state.get("last")
                rule.state = {"last": str(mid)}
                crossed = last is not None and (
                    Decimal(last) < level <= mid if rule.kind == AlertRule.Kind.PRICE_ABOVE else Decimal(last) > level >= mid
                )
                if crossed and not _cooling(rule, now):
                    arrow = "▲ above" if rule.kind == AlertRule.Kind.PRICE_ABOVE else "▼ below"
                    fire(rule, f"{where} crossed {arrow} {_fmt(level)}", f"now {_fmt(mid)}{'. ' + rule.note if rule.note else ''}", now)
                    fired += 1
                else:
                    rule.save(update_fields=["state"])
            else:
                minutes = float(p.get("minutes", 60))
                threshold = float(p["pct"]) / 100
                past = history.price_at_or_after(*key, now - timedelta(minutes=minutes))
                # only judge once the engine has seen (most of) the whole window
                if past is None or (now - past[0]).total_seconds() < minutes * 60 * 0.9:
                    continue
                change = float(mid / past[1] - 1)
                if abs(change) >= threshold and not _cooling(rule, now):
                    direction = "up" if change > 0 else "down"
                    fire(rule, f"{where} {direction} {abs(change):.2%} in {minutes:g} minutes",
                         f"from {_fmt(past[1])} to {_fmt(mid)}{'. ' + rule.note if rule.note else ''}", now)
                    fired += 1
        except (KeyError, ValueError, ArithmeticError):
            logger.warning("alert rule %s has unusable params %s", rule.pk, p)
    return fired


def _bot_state(bot) -> str:
    from trading.models import Position

    position = Position.objects.filter(book=bot.book, symbol=bot.symbol).first()
    if position is None or position.quantity == 0:
        state = "flat"
    else:
        state = f"{position.direction} {position.quantity.normalize()} since {position.opened_at:%Y-%m-%d}"
        if position.stop_price:
            state += f", stop {_fmt(position.stop_price)}"
    if bot.status != bot.Status.RUNNING:
        state += f" ({bot.status})"
    decision = bot.decisions.order_by("-bar_time").first()
    if decision and decision.diagnosis.get("near_miss"):
        state += f"; near miss: {decision.diagnosis.get('near_miss_reason') or decision.diagnosis.get('near_miss_key')}"
    return state


def _feed_state(feed) -> str:
    if feed.strategy == "donchian_ensemble":
        return f"hold {feed.weight:.0%} of capital"
    if feed.direction:
        return f"{feed.direction} since {feed.entry_time:%Y-%m-%d} at {_fmt(feed.entry_price)}, stop {_fmt(feed.stop)}"
    miss = feed.last_diagnosis.get("near_miss_reason") if feed.last_diagnosis.get("near_miss") else None
    return f"flat; near miss: {miss}" if miss else "flat"


def daily_summaries(now: datetime | None = None) -> int:
    from recommendations.models import Feed
    from trading.models import Bot, Position
    from trading.services import books

    now = now or timezone.now()
    fired = 0
    rules = AlertRule.objects.filter(enabled=True, kind=AlertRule.Kind.DAILY_SUMMARY).select_related("user")
    for rule in rules:
        if int(rule.params.get("hour", 7)) != now.hour:
            continue
        if rule.last_fired_at and rule.last_fired_at.date() == now.date():
            continue
        lines = []
        for account in visible_accounts(rule.user).filter(is_active=True):
            all_books = books.all_book_balances(account)
            assets = {a for b in all_books.values() for a in b}
            prices = books.marks(account, assets)
            equity = sum((books.equity(b, account.quote_asset, prices) for b in all_books.values()), Decimal(0))
            running = Bot.objects.filter(account=account, status=Bot.Status.RUNNING).count()
            troubled = Bot.objects.filter(account=account, status=Bot.Status.ERROR).count()
            positions = Position.objects.filter(account=account).exclude(quantity=0).count()
            line = f"{account.name} ({account.mode}): {_fmt(equity)} {account.quote_asset}, {running} bots running, {positions} open positions"
            if troubled:
                line += f", {troubled} bots stopped by errors"
            lines.append(line)
            for bot in Bot.objects.filter(account=account).exclude(status=Bot.Status.STOPPED).order_by("name"):
                lines.append(f"  {bot.name}: {_bot_state(bot)}")
        feeds = Feed.objects.filter(enabled=True).order_by("name")
        if feeds:
            lines.append("MT5 recommendations:")
            lines.extend(f"  {feed.name}: {_feed_state(feed)}" for feed in feeds)
        fire(rule, f"Daily summary {now:%Y-%m-%d}", "\n".join(lines) or "No accounts to report on.", now)
        fired += 1
    return fired


DEFAULT_RULES = [
    {"kind": AlertRule.Kind.RECOMMENDATION},
    {"kind": AlertRule.Kind.NEAR_MISS},
    {"kind": AlertRule.Kind.REPEAT_SIGNAL},
    {"kind": AlertRule.Kind.BOT_TRADE},
    {"kind": AlertRule.Kind.STOP_HIT},
    {"kind": AlertRule.Kind.BOT_ERROR},
    {"kind": AlertRule.Kind.RESEARCH_DONE},
    {"kind": AlertRule.Kind.DAILY_SUMMARY, "params": {"hour": 7}},
    {"kind": AlertRule.Kind.PRICE_MOVE, "params": {"venue": "quidax_spot", "symbol": "BTC/USDT", "pct": 3, "minutes": 60},
     "cooldown_minutes": 60},
]


def add_default_rules(user) -> list[AlertRule]:
    existing = set(AlertRule.objects.filter(user=user).values_list("kind", flat=True))
    created = []
    for spec in DEFAULT_RULES:
        if spec["kind"] in existing:
            continue
        created.append(AlertRule.objects.create(user=user, **spec))
    return created


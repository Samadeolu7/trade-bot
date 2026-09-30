"""Creating and controlling bots. A bot is a strategy from the registry,
run by the engine against one account with its own book of capital."""

import copy
from decimal import Decimal
from functools import lru_cache

from django.conf import settings
from django.db import transaction

from bot.config import load_config
from bot.strategy.registry import STRATEGY_CATALOG, build_strategy
from core.audit import audit
from research.models import StrategyLifecycle
from trading.events import account_group, publish
from trading.models import MANUAL_BOOK, Bot, LedgerEntry, Order, Position, TradingAccount
from trading.services import books
from trading.services.orders import OrderError, close_position

TIMEFRAMES = ["1h", "4h", "1d"]
ZERO = Decimal(0)


@lru_cache(maxsize=1)
def trade_bot_config() -> dict:
    return load_config(settings.TRADE_BOT_CONFIG_PATH)


def strategy_defaults(name: str) -> dict[str, dict]:
    """config.yaml's defaults for every section a strategy reads — what
    the app shows as the bot's editable parameters."""
    strategy_config = trade_bot_config().get("strategy", {})
    return {s: copy.deepcopy(strategy_config.get(s, {})) for s in STRATEGY_CATALOG[name].config_sections}


def strategy_config_with(params: dict | None) -> dict:
    """config.yaml's strategy settings with "section.key" overrides applied."""
    config = copy.deepcopy(trade_bot_config().get("strategy", {}))
    for path, value in (params or {}).items():
        section, key = path.split(".", 1)
        config.setdefault(section, {})[key] = value
    return config


def strategy_config_for(bot: Bot) -> dict:
    return strategy_config_with(bot.params)


def build_bot_strategy(bot: Bot, funding_df=None, funding_refresh_fn=None):
    return build_strategy(bot.strategy, strategy_config_for(bot), funding_df=funding_df,
                          funding_refresh_fn=funding_refresh_fn)


def _validate_params(strategy: str, params: dict) -> dict:
    allowed = set(STRATEGY_CATALOG[strategy].config_sections)
    clean = {}
    for path, value in (params or {}).items():
        if "." not in path:
            raise OrderError(f"parameter {path!r} must look like section.key")
        section = path.split(".", 1)[0]
        if section not in allowed:
            raise OrderError(f"{strategy} doesn't read the {section!r} section")
        clean[path] = value
    return clean


def create_bot(
    account: TradingAccount,
    *,
    name: str,
    strategy: str,
    symbol: str = "BTC/USDT",
    timeframe: str = "4h",
    allocation,
    params: dict | None = None,
    risk_pct: float = 0.01,
    user=None,
    request=None,
) -> Bot:
    if strategy not in STRATEGY_CATALOG:
        raise OrderError(f"unknown strategy {strategy!r}")
    if timeframe not in TIMEFRAMES:
        raise OrderError(f"timeframe must be one of {', '.join(TIMEFRAMES)}")
    if not 0 < risk_pct <= 0.05:
        raise OrderError("risk per trade must be between 0 and 5%")
    allocation = Decimal(str(allocation))
    if allocation <= 0:
        raise OrderError("allocation must be positive")
    if Bot.objects.filter(name=name).exists():
        raise OrderError(f"a bot named {name!r} already exists")
    params = _validate_params(strategy, params or {})
    if account.mode == TradingAccount.Mode.LIVE:
        stage = StrategyLifecycle.objects.filter(label=name).values_list("stage", flat=True).first()
        if stage != "automation_ready":
            raise OrderError(
                f"live bots must be named after a strategy label at automation_ready; "
                f"{name!r} is at {stage or 'research'}"
            )

    with transaction.atomic():
        cash = books.book_balances(account, MANUAL_BOOK).get(account.quote_asset, ZERO)
        if allocation > cash:
            raise OrderError(f"the manual book has only {cash:.2f} {account.quote_asset} to allocate")
        bot = Bot.objects.create(
            name=name, strategy=strategy, params=params, symbol=symbol, timeframe=timeframe,
            account=account, allocation=allocation, risk_pct=risk_pct, created_by=user,
        )
        books.post(account, MANUAL_BOOK, LedgerEntry.Kind.ALLOCATION, account.quote_asset, -allocation,
                   note=f"to bot {name}")
        books.post(account, bot.book, LedgerEntry.Kind.ALLOCATION, account.quote_asset, allocation,
                   note="initial allocation")
    audit("bot.created", request=request, user=user, account=account, target=f"bot:{bot.pk}",
          strategy=strategy, timeframe=timeframe, allocation=str(allocation), params=params)
    publish(account_group(account.pk), "bot", {"id": bot.pk})
    return bot


def set_bot_status(bot: Bot, status: str, *, user=None, request=None, reason: str = "") -> Bot:
    if status == Bot.Status.STOPPED:
        return stop_bot(bot, user=user, request=request, reason=reason)
    if status == Bot.Status.RUNNING and bot.account.halted:
        raise OrderError(f"account is halted ({bot.account.halted_reason}); resume it first")
    bot.status = status
    bot.status_reason = reason
    if status == Bot.Status.RUNNING:
        bot.consecutive_errors = 0
    bot.save(update_fields=["status", "status_reason", "consecutive_errors"])
    audit(f"bot.{status}", request=request, user=user, account=bot.account, target=f"bot:{bot.pk}", reason=reason)
    publish(account_group(bot.account_id), "bot", {"id": bot.pk})
    return bot


def stop_bot(bot: Bot, *, user=None, request=None, reason: str = "") -> Bot:
    """Cancels the bot's resting orders, closes its position and stops it.
    Its capital stays in its book, so its record carries on if restarted."""
    from trading.services.orders import cancel_order

    for order in Order.objects.filter(bot=bot, status=Order.Status.OPEN):
        cancel_order(order, user=user, request=request)
    for position in Position.objects.filter(book=bot.book).exclude(quantity=0):
        order = close_position(position, source=Order.Source.CLOSE, user=user, request=request,
                               reason=f"bot stopped{': ' + reason if reason else ''}")
        if order is not None and order.status == Order.Status.REJECTED:
            raise OrderError(f"couldn't close {bot.name}'s position: {order.reject_reason}")
    bot.status = Bot.Status.STOPPED
    bot.status_reason = reason
    bot.save(update_fields=["status", "status_reason"])
    audit("bot.stopped", request=request, user=user, account=bot.account, target=f"bot:{bot.pk}", reason=reason)
    publish(account_group(bot.account_id), "bot", {"id": bot.pk})
    return bot

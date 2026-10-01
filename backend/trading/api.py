from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from bot.broker.base import BrokerError
from bot.broker.venues import VENUES
from bot.strategy.registry import STRATEGY_CATALOG
from core.audit import audit
from market.candles import candles_df
from research.keys import research_auth
from trading.engine.loop import HEARTBEAT_KEY, QUOTE_KEY
from trading.models import (
    MANUAL_BOOK,
    Bot,
    BotDecision,
    EquitySnapshot,
    Fill,
    LedgerEntry,
    Order,
    Position,
    TradingAccount,
)
from trading.permissions import (
    can_trade_account,
    require_owner,
    require_trade,
    require_view,
    visible_accounts,
)
from trading.services import books
from trading.services.bots import create_bot, set_bot_status, strategy_defaults, trade_bot_config
from trading.services.brokers import quote_source
from trading.services.orders import (
    cancel_order,
    close_position,
    fund_paper_account,
    set_protection,
    submit_order,
)

router = Router(tags=["trading"])
ZERO = Decimal(0)


def _f(value) -> float | None:
    return float(value) if value is not None else None


def _book_label(book: str, bot_names: dict[str, str]) -> str:
    return "Manual" if book == MANUAL_BOOK else bot_names.get(book, book)


# --- venues & market -------------------------------------------------------------------


class VenueOut(Schema):
    key: str
    label: str
    kind: str
    quote_asset: str
    maker_fee: float
    taker_fee: float
    long_only: bool
    max_leverage: float
    min_qty: float
    qty_step: float
    supports_live: bool


def _venue_out(v) -> dict:
    return {
        "key": v.key, "label": v.label, "kind": v.kind, "quote_asset": v.quote_asset,
        "maker_fee": float(v.maker_fee), "taker_fee": float(v.taker_fee), "long_only": v.long_only,
        "max_leverage": float(v.max_leverage), "min_qty": float(v.min_qty), "qty_step": float(v.qty_step),
        "supports_live": v.supports_live,
    }


@router.get("/venues", response=list[VenueOut])
def list_venues(request):
    return [_venue_out(v) for v in VENUES.values()]


class QuoteOut(Schema):
    venue: str
    symbol: str
    bid: float
    ask: float
    last: float
    time: datetime


@router.get("/quote", response=QuoteOut)
def get_quote(request, venue: str, symbol: str = "BTC/USDT"):
    if venue not in VENUES:
        raise HttpError(400, "unknown venue")
    cached = cache.get(QUOTE_KEY.format(venue=venue, symbol=symbol))
    if cached:
        return cached
    try:
        q = quote_source(venue).quote(symbol)
    except BrokerError as exc:
        raise HttpError(502, str(exc)) from exc
    return {"venue": venue, "symbol": symbol, "bid": q.bid, "ask": q.ask, "last": q.last, "time": q.time}


class CandleOut(Schema):
    time: int  # epoch seconds, what the chart library expects
    open: float
    high: float
    low: float
    close: float
    volume: float


@router.get("/candles", response=list[CandleOut])
def get_candles(request, symbol: str = "BTC/USDT", timeframe: str = "4h", limit: int = 500):
    exchange_id = trade_bot_config()["exchange"]["id"]
    df = candles_df(exchange_id, symbol, timeframe, min(limit, 5000))
    return [
        {"time": int(ts.timestamp()), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
         "volume": r.volume}
        for ts, r in df.iterrows()
    ]


# --- accounts ------------------------------------------------------------------------------


class BookOut(Schema):
    book: str
    label: str
    bot_id: int | None
    balances: dict[str, float]
    equity: float | None


class AccountOut(Schema):
    id: int
    name: str
    mode: str
    venue: VenueOut
    is_active: bool
    halted: bool
    halted_reason: str
    can_trade: bool
    equity: float | None
    # trading result over the last 30 days: the equity change less money
    # deposited or withdrawn in that time
    change_30d: float | None
    cash: float
    books: list[BookOut]
    created_at: datetime


def _account_out(account: TradingAccount, user) -> dict:
    all_books = books.all_book_balances(account)
    assets = {a for balances in all_books.values() for a in balances}
    prices = books.marks(account, assets)
    priced = all(a in prices or a == account.quote_asset for a in assets)
    bot_ids = {b.book: b.pk for b in account.bots.all()}
    bot_names = {b.book: b.name for b in account.bots.all()}
    book_rows = []
    total = ZERO
    cash = ZERO
    for book, balances in sorted(all_books.items(), key=lambda kv: (kv[0] != MANUAL_BOOK, kv[0])):
        value = books.equity(balances, account.quote_asset, prices)
        total += value
        cash += balances.get(account.quote_asset, ZERO)
        book_rows.append({
            "book": book, "label": _book_label(book, bot_names), "bot_id": bot_ids.get(book),
            "balances": {k: float(v) for k, v in balances.items() if v},
            "equity": float(value) if priced else None,
        })
    change = None
    start = (EquitySnapshot.objects.filter(account=account, book="", time__gte=timezone.now() - timedelta(days=30))
             .order_by("time").first())
    if start is not None and priced:
        flows = LedgerEntry.objects.filter(
            account=account, created_at__gt=start.time, asset=account.quote_asset,
            kind__in=[LedgerEntry.Kind.DEPOSIT, LedgerEntry.Kind.WITHDRAWAL],
        ).aggregate(total=Sum("amount"))["total"] or ZERO
        change = float(total - start.equity - flows)
    return {
        "id": account.pk, "name": account.name, "mode": account.mode, "venue": _venue_out(account.venue_profile),
        "change_30d": change,
        "is_active": account.is_active, "halted": account.halted, "halted_reason": account.halted_reason,
        "can_trade": can_trade_account(user, account), "equity": float(total) if priced else None,
        "cash": float(cash), "books": book_rows, "created_at": account.created_at,
    }


@router.get("/accounts", response=list[AccountOut])
def list_accounts(request):
    return [_account_out(a, request.user) for a in visible_accounts(request.user).prefetch_related("bots")]


@router.get("/accounts/{account_id}", response=AccountOut)
def get_account(request, account_id: int):
    return _account_out(require_view(request, account_id), request.user)


class AccountIn(Schema):
    name: str
    venue: str = "quidax_spot"
    mode: str = "paper"
    initial_deposit: Decimal = Decimal(10_000)


@router.post("/accounts", response=AccountOut)
def create_account(request, payload: AccountIn):
    require_owner(request)
    if payload.venue not in VENUES:
        raise HttpError(400, "unknown venue")
    if payload.mode != TradingAccount.Mode.PAPER:
        # Phase 4 adds the Quidax connector; until then only paper accounts exist
        raise HttpError(400, "live accounts can't be created until the live connector is built")
    if TradingAccount.objects.filter(name=payload.name).exists():
        raise HttpError(400, "an account with that name exists")
    with transaction.atomic():
        account = TradingAccount.objects.create(name=payload.name, venue=payload.venue, mode=payload.mode)
        audit("account.created", request=request, account=account, target=f"account:{account.pk}",
              venue=account.venue, mode=account.mode)
        if payload.initial_deposit > 0:
            fund_paper_account(account, payload.initial_deposit, user=request.user, request=request)
    return _account_out(account, request.user)


class FundsIn(Schema):
    amount: Decimal
    withdraw: bool = False


@router.post("/accounts/{account_id}/funds", response=AccountOut)
def move_funds(request, account_id: int, payload: FundsIn):
    account = require_view(request, account_id)
    require_owner(request)
    fund_paper_account(account, payload.amount, user=request.user, request=request, withdraw=payload.withdraw)
    return _account_out(account, request.user)


class EquityPoint(Schema):
    time: int
    equity: float


@router.get("/accounts/{account_id}/equity", response=list[EquityPoint])
def account_equity(request, account_id: int, book: str = "", days: int = 90):
    account = require_view(request, account_id)
    since = timezone.now() - timedelta(days=min(days, 3650))
    rows = EquitySnapshot.objects.filter(account=account, book=book, time__gte=since).order_by("time")
    return [{"time": int(r.time.timestamp()), "equity": float(r.equity)} for r in rows]


class LedgerOut(Schema):
    id: int
    created_at: datetime
    book: str
    kind: str
    asset: str
    amount: float
    order_id: int | None
    note: str


@router.get("/accounts/{account_id}/ledger", response=list[LedgerOut])
def account_ledger(request, account_id: int, book: str | None = None, limit: int = 200):
    account = require_view(request, account_id)
    rows = LedgerEntry.objects.filter(account=account).order_by("-id")
    if book:
        rows = rows.filter(book=book)
    return [
        {"id": r.pk, "created_at": r.created_at, "book": r.book, "kind": r.kind, "asset": r.asset,
         "amount": float(r.amount), "order_id": r.order_id, "note": r.note}
        for r in rows[: min(limit, 1000)]
    ]


# --- orders & positions -------------------------------------------------------------------


class FillOut(Schema):
    price: float
    quantity: float
    fee: float
    liquidity: str
    time: datetime


class OrderOut(Schema):
    id: int
    account_id: int
    book: str
    book_label: str
    bot_id: int | None
    placed_by: str | None
    source: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    limit_price: float | None
    status: str
    filled_quantity: float
    average_price: float | None
    fees: float
    reject_reason: str
    reason: str
    stop_price: float | None
    take_profit: float | None
    created_at: datetime
    fills: list[FillOut]


def _order_out(o: Order) -> dict:
    return {
        "id": o.pk, "account_id": o.account_id, "book": o.book,
        "book_label": o.bot.name if o.bot else ("Manual" if o.book == MANUAL_BOOK else o.book),
        "bot_id": o.bot_id, "placed_by": o.placed_by.username if o.placed_by else None, "source": o.source,
        "symbol": o.symbol, "side": o.side, "order_type": o.order_type, "quantity": float(o.quantity),
        "limit_price": _f(o.limit_price), "status": o.status, "filled_quantity": float(o.filled_quantity),
        "average_price": _f(o.average_price), "fees": float(o.fees), "reject_reason": o.reject_reason,
        "reason": o.reason, "stop_price": _f(o.stop_price), "take_profit": _f(o.take_profit),
        "created_at": o.created_at,
        "fills": [
            {"price": float(f.price), "quantity": float(f.quantity), "fee": float(f.fee),
             "liquidity": f.liquidity, "time": f.time}
            for f in o.fills.all()
        ],
    }


@router.get("/orders", response=list[OrderOut])
def list_orders(request, account_id: int | None = None, status: str | None = None, bot_id: int | None = None,
                book: str | None = None, limit: int = 100):
    orders = Order.objects.filter(account__in=visible_accounts(request.user)).select_related("bot", "placed_by")
    if account_id is not None:
        orders = orders.filter(account_id=account_id)
    if status == "active":
        orders = orders.filter(status__in=[Order.Status.PENDING, Order.Status.OPEN])
    elif status:
        orders = orders.filter(status=status)
    if bot_id is not None:
        orders = orders.filter(bot_id=bot_id)
    if book:
        orders = orders.filter(book=book)
    return [_order_out(o) for o in orders.prefetch_related("fills").order_by("-id")[: min(limit, 1000)]]


class OrderIn(Schema):
    account_id: int
    symbol: str = "BTC/USDT"
    side: str
    order_type: str = "market"
    quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_profit: Decimal | None = None
    note: str = ""


@router.post("/orders", response=OrderOut)
def place_order(request, payload: OrderIn):
    """Manual orders always go to the account's manual book; bots trade
    their own books through the engine."""
    account = require_view(request, payload.account_id)
    require_trade(request, account)
    order = submit_order(
        account, book=MANUAL_BOOK, symbol=payload.symbol, side=payload.side, quantity=payload.quantity,
        order_type=payload.order_type, limit_price=payload.limit_price, source=Order.Source.MANUAL,
        user=request.user, reason=payload.note, stop_price=payload.stop_price,
        take_profit=payload.take_profit, request=request,
    )
    return _order_out(order)


@router.post("/orders/{order_id}/cancel", response=OrderOut)
def cancel(request, order_id: int):
    order = Order.objects.filter(pk=order_id, account__in=visible_accounts(request.user)).first()
    if order is None:
        raise HttpError(404, "no such order")
    require_trade(request, order.account)
    return _order_out(cancel_order(order, user=request.user, request=request))


class PositionOut(Schema):
    id: int
    account_id: int
    book: str
    book_label: str
    bot_id: int | None
    symbol: str
    direction: str
    quantity: float
    average_price: float
    mark_price: float | None
    unrealized_pnl: float | None
    realized_pnl: float
    stop_price: float | None
    take_profit: float | None
    opened_at: datetime | None


def _position_out(p: Position) -> dict:
    try:
        mark = quote_source(p.account.venue).quote(p.symbol).mid
    except BrokerError:
        mark = None
    unrealized = (mark - p.average_price) * p.quantity if mark is not None and p.quantity else None
    return {
        "id": p.pk, "account_id": p.account_id, "book": p.book,
        "book_label": p.bot.name if p.bot else "Manual", "bot_id": p.bot_id, "symbol": p.symbol,
        "direction": p.direction, "quantity": float(p.quantity), "average_price": float(p.average_price),
        "mark_price": _f(mark), "unrealized_pnl": _f(unrealized), "realized_pnl": float(p.realized_pnl),
        "stop_price": _f(p.stop_price), "take_profit": _f(p.take_profit), "opened_at": p.opened_at,
    }


@router.get("/positions", response=list[PositionOut])
def list_positions(request, account_id: int | None = None, include_flat: bool = False, bot_id: int | None = None):
    positions = Position.objects.filter(account__in=visible_accounts(request.user)).select_related("account", "bot")
    if account_id is not None:
        positions = positions.filter(account_id=account_id)
    if bot_id is not None:
        positions = positions.filter(bot_id=bot_id)
    if not include_flat:
        positions = positions.exclude(quantity=0)
    return [_position_out(p) for p in positions]


def _position_for(request, position_id: int) -> Position:
    position = Position.objects.filter(pk=position_id, account__in=visible_accounts(request.user)).first()
    if position is None:
        raise HttpError(404, "no such position")
    require_trade(request, position.account)
    return position


class ProtectionIn(Schema):
    stop_price: Decimal | None = None
    take_profit: Decimal | None = None


@router.put("/positions/{position_id}/protection", response=PositionOut)
def update_protection(request, position_id: int, payload: ProtectionIn):
    position = _position_for(request, position_id)
    if position.book != MANUAL_BOOK:
        raise HttpError(400, "a bot manages its own stop; pause or stop the bot to intervene")
    set_protection(position, stop_price=payload.stop_price, take_profit=payload.take_profit,
                   user=request.user, request=request)
    return _position_out(position)


@router.post("/positions/{position_id}/close", response=OrderOut)
def close(request, position_id: int):
    position = _position_for(request, position_id)
    if position.bot and position.bot.status == Bot.Status.RUNNING:
        raise HttpError(400, "pause or stop the bot before closing its position by hand")
    order = close_position(position, source=Order.Source.CLOSE, user=request.user,
                           reason="closed by hand", request=request)
    if order is None:
        raise HttpError(400, "position is already flat")
    return _order_out(order)


class TradeOut(Schema):
    id: int
    order_id: int
    account_id: int
    book_label: str
    symbol: str
    side: str
    price: float
    quantity: float
    fee: float
    liquidity: str
    source: str
    time: datetime


@router.get("/fills", response=list[TradeOut])
def list_fills(request, account_id: int | None = None, bot_id: int | None = None, limit: int = 200):
    fills = Fill.objects.filter(order__account__in=visible_accounts(request.user)).select_related("order", "order__bot")
    if account_id is not None:
        fills = fills.filter(order__account_id=account_id)
    if bot_id is not None:
        fills = fills.filter(order__bot_id=bot_id)
    return [
        {"id": f.pk, "order_id": f.order_id, "account_id": f.order.account_id,
         "book_label": f.order.bot.name if f.order.bot else "Manual", "symbol": f.order.symbol,
         "side": f.order.side, "price": float(f.price), "quantity": float(f.quantity), "fee": float(f.fee),
         "liquidity": f.liquidity, "source": f.order.source, "time": f.time}
        for f in fills.order_by("-time")[: min(limit, 2000)]
    ]


# --- strategies & bots --------------------------------------------------------------------


class StrategyOut(Schema):
    name: str
    kind: str
    can_short: bool
    description: str
    defaults: dict


@router.get("/strategies", response=list[StrategyOut], auth=research_auth)
def list_strategies(request, include_research: bool = False):
    """`include_research`: also the research-only options (pyramiding), for
    the research report form; bots can't use them."""
    return [
        {"name": s.name, "kind": s.kind, "can_short": s.can_short, "description": s.description,
         "defaults": strategy_defaults(s.name, include_research)}
        for s in STRATEGY_CATALOG.values()
    ]


class DecisionOut(Schema):
    id: int
    bar_time: datetime
    action: str
    reason: str
    price: float | None
    order_id: int | None
    diagnosis: dict
    code_version: str
    config_hash: str
    data_to: datetime | None
    data_rows: int | None
    data_digest: str


class BotOut(Schema):
    id: int
    name: str
    strategy: str
    strategy_kind: str
    params: dict
    symbol: str
    timeframe: str
    account_id: int
    account_name: str
    account_mode: str
    allocation: float
    risk_pct: float
    status: str
    status_reason: str
    last_bar_at: datetime | None
    last_run_at: datetime | None
    equity: float | None
    pnl: float | None
    position_quantity: float
    position_stop: float | None
    # the share of the bot's equity currently in the asset
    invested_weight: float | None
    last_decision: DecisionOut | None
    created_at: datetime


def _decision_out(d: BotDecision | None) -> dict | None:
    if d is None:
        return None
    return {"id": d.pk, "bar_time": d.bar_time, "action": d.action, "reason": d.reason, "price": d.price,
            "order_id": d.order_id, "diagnosis": d.diagnosis, "code_version": d.code_version,
            "config_hash": d.config_hash, "data_to": d.data_to, "data_rows": d.data_rows,
            "data_digest": d.data_digest}


def _invested(balances, prices, base, equity) -> float | None:
    if base not in prices or equity <= 0:
        return None
    return float(balances.get(base, ZERO) * prices[base] / equity)


def _bot_out(bot: Bot) -> dict:
    account = bot.account
    balances = books.book_balances(account, bot.book)
    prices = books.marks(account, balances)
    value = books.equity(balances, account.quote_asset, prices)
    priced = all(a in prices or a == account.quote_asset for a in balances)
    position = Position.objects.filter(account=account, book=bot.book, symbol=bot.symbol).first()
    return {
        "id": bot.pk, "name": bot.name, "strategy": bot.strategy,
        "strategy_kind": STRATEGY_CATALOG[bot.strategy].kind if bot.strategy in STRATEGY_CATALOG else "signal",
        "params": bot.params, "symbol": bot.symbol, "timeframe": bot.timeframe, "account_id": account.pk,
        "account_name": account.name, "account_mode": account.mode, "allocation": float(bot.allocation),
        "risk_pct": bot.risk_pct, "status": bot.status, "status_reason": bot.status_reason,
        "last_bar_at": bot.last_bar_at, "last_run_at": bot.last_run_at,
        "equity": float(value) if priced else None,
        "pnl": float(value - bot.allocation) if priced else None,
        "position_quantity": float(position.quantity) if position else 0.0,
        "position_stop": _f(position.stop_price) if position else None,
        "invested_weight": _invested(balances, prices, account.venue_profile.base_asset(bot.symbol), value),
        "last_decision": _decision_out(bot.decisions.order_by("-bar_time").first()),
        "created_at": bot.created_at,
    }


@router.get("/bots", response=list[BotOut])
def list_bots(request, account_id: int | None = None):
    bots = Bot.objects.filter(account__in=visible_accounts(request.user)).select_related("account")
    if account_id is not None:
        bots = bots.filter(account_id=account_id)
    return [_bot_out(b) for b in bots]


def _bot_for(request, bot_id: int) -> Bot:
    bot = Bot.objects.filter(pk=bot_id, account__in=visible_accounts(request.user)).select_related("account").first()
    if bot is None:
        raise HttpError(404, "no such bot")
    return bot


@router.get("/bots/{bot_id}", response=BotOut)
def get_bot(request, bot_id: int):
    return _bot_out(_bot_for(request, bot_id))


class BotIn(Schema):
    account_id: int
    name: str
    strategy: str
    symbol: str = "BTC/USDT"
    timeframe: str = "4h"
    allocation: Decimal
    risk_pct: float = 0.01
    params: dict = {}
    start: bool = True


@router.post("/bots", response=BotOut)
def new_bot(request, payload: BotIn):
    account = require_view(request, payload.account_id)
    require_trade(request, account)
    bot = create_bot(
        account, name=payload.name.strip(), strategy=payload.strategy, symbol=payload.symbol,
        timeframe=payload.timeframe, allocation=payload.allocation, params=payload.params,
        risk_pct=payload.risk_pct, user=request.user, request=request,
    )
    if payload.start:
        set_bot_status(bot, Bot.Status.RUNNING, user=request.user, request=request)
    return _bot_out(bot)


class BotStatusIn(Schema):
    status: str
    reason: str = ""


@router.post("/bots/{bot_id}/status", response=BotOut)
def change_bot_status(request, bot_id: int, payload: BotStatusIn):
    bot = _bot_for(request, bot_id)
    require_trade(request, bot.account)
    if payload.status not in (Bot.Status.RUNNING, Bot.Status.PAUSED, Bot.Status.STOPPED):
        raise HttpError(400, "status must be running, paused or stopped")
    set_bot_status(bot, payload.status, user=request.user, request=request, reason=payload.reason)
    bot.refresh_from_db()
    return _bot_out(bot)


@router.get("/bots/{bot_id}/decisions", response=list[DecisionOut])
def bot_decisions(request, bot_id: int, action: str | None = None, limit: int = 200, before: datetime | None = None):
    bot = _bot_for(request, bot_id)
    decisions = bot.decisions.all()
    if action:
        decisions = decisions.filter(action=action)
    if before:
        decisions = decisions.filter(bar_time__lt=before)
    return [_decision_out(d) for d in decisions.order_by("-bar_time")[: min(limit, 2000)]]


# --- system ----------------------------------------------------------------------------------


class SystemOut(Schema):
    engine_last_seen: datetime | None
    engine_healthy: bool
    require_2fa: bool
    server_time: datetime


@router.get("/system/status", response=SystemOut)
def system_status(request):
    beat = cache.get(HEARTBEAT_KEY)
    last = datetime.fromisoformat(beat["at"]) if beat else None
    stale = settings.ENGINE["heartbeat_stale_seconds"]
    healthy = last is not None and (timezone.now() - last).total_seconds() < stale
    return {"engine_last_seen": last, "engine_healthy": healthy, "require_2fa": settings.REQUIRE_2FA,
            "server_time": timezone.now()}

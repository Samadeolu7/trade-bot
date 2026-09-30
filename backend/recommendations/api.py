from datetime import datetime

from ninja import Router, Schema
from ninja.errors import HttpError

from bot.broker.base import BrokerError
from core.audit import audit
from recommendations.models import Feed, Recommendation
from trading.permissions import require_owner
from trading.services.brokers import quote_source

router = Router(tags=["recommendations"])


class FeedOut(Schema):
    id: int
    name: str
    strategy: str
    timeframe: str
    symbol: str
    enabled: bool
    kind: str  # "position" | "exposure"
    direction: str
    entry_price: float | None
    entry_time: datetime | None
    stop: float | None
    take_profit: float | None
    open_pnl_pct: float | None
    weight: float
    equity: float
    near_miss: str | None
    last_bar_at: datetime | None
    last_run_at: datetime | None
    status_reason: str
    capital: float | None
    current_capital: float | None
    contract_size: float
    min_lot: float
    lot_step: float
    risk_pct: float
    lots_held: float


def _mid(symbol: str) -> float | None:
    # the feeds read Binance candles; for a live "how's this call doing" the
    # CFD profile's reference price is the closest thing to what MT5 shows
    try:
        return float(quote_source("exness_cfd").quote(symbol).mid)
    except BrokerError:
        return None


def _feed_out(feed: Feed, prices: dict) -> dict:
    price = prices.setdefault(feed.symbol, _mid(feed.symbol))
    pnl = None
    if feed.direction and feed.entry_price and price:
        pnl = (price / feed.entry_price - 1) * 100 * (1 if feed.direction == "long" else -1)
    d = feed.last_diagnosis or {}
    return {
        "id": feed.pk, "name": feed.name, "strategy": feed.strategy, "timeframe": feed.timeframe,
        "symbol": feed.symbol, "enabled": feed.enabled,
        "kind": "exposure" if feed.strategy == "donchian_ensemble" else "position",
        "direction": feed.direction, "entry_price": feed.entry_price, "entry_time": feed.entry_time,
        "stop": feed.stop, "take_profit": feed.take_profit, "open_pnl_pct": pnl, "weight": feed.weight,
        "equity": feed.equity,
        "near_miss": (d.get("near_miss_reason") or d.get("near_miss_key")) if d.get("near_miss") else None,
        "last_bar_at": feed.last_bar_at, "last_run_at": feed.last_run_at, "status_reason": feed.status_reason,
        "capital": feed.capital, "current_capital": feed.current_capital, "contract_size": feed.contract_size,
        "min_lot": feed.min_lot, "lot_step": feed.lot_step, "risk_pct": feed.risk_pct, "lots_held": feed.lots_held,
    }


@router.get("/feeds", response=list[FeedOut])
def list_feeds(request):
    prices: dict = {}
    return [_feed_out(f, prices) for f in Feed.objects.all()]


class EnabledIn(Schema):
    enabled: bool


@router.post("/feeds/{feed_id}/enabled", response=FeedOut)
def set_enabled(request, feed_id: int, payload: EnabledIn):
    require_owner(request)
    feed = Feed.objects.filter(pk=feed_id).first()
    if feed is None:
        raise HttpError(404, "no such feed")
    feed.enabled = payload.enabled
    feed.save(update_fields=["enabled"])
    audit("recommendations.enabled", request=request, target=f"feed:{feed.name}", enabled=payload.enabled)
    return _feed_out(feed, {})


class SizingIn(Schema):
    # your MT5 balance for this feed, in USD; null clears it
    capital: float | None = None
    contract_size: float = 1.0
    min_lot: float = 0.01
    lot_step: float = 0.01
    risk_pct: float = 0.01
    # the lots you actually hold for this feed right now (usually leave as is)
    lots_held: float | None = None


@router.post("/feeds/{feed_id}/sizing", response=FeedOut)
def set_sizing(request, feed_id: int, payload: SizingIn):
    """Lets alerts give exact MT5 lot sizes for this feed."""
    require_owner(request)
    feed = Feed.objects.filter(pk=feed_id).first()
    if feed is None:
        raise HttpError(404, "no such feed")
    if payload.capital is not None and payload.capital <= 0:
        raise HttpError(400, "capital must be above 0")
    if payload.contract_size <= 0 or payload.min_lot <= 0 or payload.lot_step <= 0:
        raise HttpError(400, "contract size, minimum lot and lot step must be above 0")
    if not 0 < payload.risk_pct <= 0.05:
        raise HttpError(400, "risk per trade must be between 0 and 5%")
    feed.capital = payload.capital
    # amounts from here on scale with the feed's own profit and loss
    feed.capital_equity_base = feed.equity if payload.capital else None
    feed.contract_size, feed.min_lot, feed.lot_step = payload.contract_size, payload.min_lot, payload.lot_step
    feed.risk_pct = payload.risk_pct
    if payload.lots_held is not None:
        if payload.lots_held < 0:
            raise HttpError(400, "lots held can't be negative")
        feed.lots_held = payload.lots_held
    feed.save(update_fields=["capital", "capital_equity_base", "contract_size", "min_lot", "lot_step",
                             "risk_pct", "lots_held"])
    audit("recommendations.sizing", request=request, target=f"feed:{feed.name}", capital=payload.capital,
          contract_size=payload.contract_size, min_lot=payload.min_lot, risk_pct=payload.risk_pct,
          lots_held=feed.lots_held)
    return _feed_out(feed, {})


class RecommendationOut(Schema):
    id: int
    feed: str
    timeframe: str
    kind: str
    bar_time: datetime
    direction: str
    price: float | None
    stop: float | None
    take_profit: float | None
    from_weight: float | None
    to_weight: float | None
    pnl_pct: float | None
    reason: str
    context: dict
    fear_greed: str
    imported: bool


@router.get("/events", response=list[RecommendationOut])
def list_events(request, feed: str | None = None, kind: str | None = None, include_near_misses: bool = False,
                limit: int = 200):
    rows = Recommendation.objects.select_related("feed")
    if feed:
        rows = rows.filter(feed__name=feed)
    if kind:
        rows = rows.filter(kind=kind)
    elif not include_near_misses:
        rows = rows.exclude(kind=Recommendation.Kind.NEAR_MISS)
    return [
        {
            "id": r.pk, "feed": r.feed.name, "timeframe": r.feed.timeframe, "kind": r.kind, "bar_time": r.bar_time,
            "direction": r.direction, "price": r.price, "stop": r.stop, "take_profit": r.take_profit,
            "from_weight": r.from_weight, "to_weight": r.to_weight, "pnl_pct": r.pnl_pct, "reason": r.reason,
            "context": r.context, "fear_greed": r.fear_greed, "imported": r.imported,
        }
        for r in rows[: min(limit, 1000)]
    ]

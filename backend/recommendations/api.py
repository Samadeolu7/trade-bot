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

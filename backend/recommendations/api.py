from datetime import datetime

from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from bot.broker.base import BrokerError
from core.audit import audit
from recommendations.models import Feed, Recommendation, SignalSwitch
from recommendations.runner import feed_config_hash, halt_feed, resume_feed
from research.models import Experiment
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
    # you trade this one on MT5: only followed feeds send alerts
    following: bool
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
    halted: bool
    halt_reason: str
    halted_at: datetime | None
    config_hash: str
    approved_config_hash: str
    approved_experiment_id: int | None
    approved_at: datetime | None
    approved_by: str


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
        "symbol": feed.symbol, "enabled": feed.enabled, "following": feed.following,
        "kind": "exposure" if feed.strategy == "donchian_ensemble" else "position",
        "direction": feed.direction, "entry_price": feed.entry_price, "entry_time": feed.entry_time,
        "stop": feed.stop, "take_profit": feed.take_profit, "open_pnl_pct": pnl, "weight": feed.weight,
        "equity": feed.equity,
        "near_miss": (d.get("near_miss_reason") or d.get("near_miss_key")) if d.get("near_miss") else None,
        "last_bar_at": feed.last_bar_at, "last_run_at": feed.last_run_at, "status_reason": feed.status_reason,
        "capital": feed.capital, "current_capital": feed.current_capital, "contract_size": feed.contract_size,
        "min_lot": feed.min_lot, "lot_step": feed.lot_step, "risk_pct": feed.risk_pct, "lots_held": feed.lots_held,
        "halted": feed.halted, "halt_reason": feed.halt_reason, "halted_at": feed.halted_at,
        "config_hash": feed_config_hash(feed), "approved_config_hash": feed.approved_config_hash,
        "approved_experiment_id": feed.approved_experiment_id, "approved_at": feed.approved_at,
        "approved_by": feed.approved_by,
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


class FollowingIn(Schema):
    following: bool


@router.post("/feeds/{feed_id}/following", response=FeedOut)
def set_following(request, feed_id: int, payload: FollowingIn):
    """Choose which strategies you trade on MT5; only those send alerts."""
    require_owner(request)
    feed = _get_feed(feed_id)
    feed.following = payload.following
    feed.save(update_fields=["following"])
    audit("recommendations.following", request=request, target=f"feed:{feed.name}", following=payload.following)
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


def _get_feed(feed_id: int) -> Feed:
    feed = Feed.objects.filter(pk=feed_id).first()
    if feed is None:
        raise HttpError(404, "no such feed")
    return feed


class HaltIn(Schema):
    reason: str


@router.post("/feeds/{feed_id}/halt", response=FeedOut)
def halt(request, feed_id: int, payload: HaltIn):
    """Kill switch for one feed: no calls until resumed."""
    require_owner(request)
    feed = _get_feed(feed_id)
    reason = payload.reason.strip()
    if not reason:
        raise HttpError(400, "say why you're pausing it")
    if feed.halted:
        raise HttpError(409, "already paused")
    halt_feed(feed, f"paused by {request.user.username}: {reason}", by=request.user.username)
    audit("recommendations.halt", request=request, target=f"feed:{feed.name}", reason=reason)
    return _feed_out(feed, {})


class ResumeIn(Schema):
    # you've looked at why it paused and at your MT5 position
    acknowledged: bool
    note: str = ""


@router.post("/feeds/{feed_id}/resume", response=FeedOut)
def resume(request, feed_id: int, payload: ResumeIn):
    require_owner(request)
    feed = _get_feed(feed_id)
    if not feed.halted:
        raise HttpError(409, "not paused")
    if not payload.acknowledged:
        raise HttpError(400, "confirm you've checked the reason and your MT5 position first")
    reason = feed.halt_reason
    resume_feed(feed, by=request.user.username, note=payload.note)
    audit("recommendations.resume", request=request, target=f"feed:{feed.name}", after=reason, note=payload.note)
    return _feed_out(feed, {})


class ApproveIn(Schema):
    # the research experiment these settings were validated in, if any
    experiment_id: int | None = None
    note: str = ""


@router.post("/feeds/{feed_id}/approve", response=FeedOut)
def approve(request, feed_id: int, payload: ApproveIn):
    """Pins the feed's current settings: if they change later (feed params or
    config.yaml), the feed pauses until they're approved again."""
    require_owner(request)
    feed = _get_feed(feed_id)
    experiment = None
    if payload.experiment_id is not None:
        experiment = Experiment.objects.filter(pk=payload.experiment_id).first()
        if experiment is None:
            raise HttpError(404, "no such experiment")
        if experiment.strategy != feed.strategy:
            raise HttpError(400, f"that experiment tested {experiment.strategy}, not {feed.strategy}")
    feed.approved_config_hash = feed_config_hash(feed)
    feed.approved_experiment = experiment
    feed.approved_at = timezone.now()
    feed.approved_by = request.user.username
    feed.save(update_fields=["approved_config_hash", "approved_experiment", "approved_at", "approved_by"])
    audit("recommendations.approve", request=request, target=f"feed:{feed.name}",
          config_hash=feed.approved_config_hash, experiment=payload.experiment_id, note=payload.note)
    return _feed_out(feed, {})


class SwitchOut(Schema):
    halted: bool
    reason: str
    changed_at: datetime | None
    changed_by: str


class SwitchIn(Schema):
    halted: bool
    reason: str = ""


def _switch_out(switch: SignalSwitch) -> dict:
    return {"halted": switch.halted, "reason": switch.reason, "changed_at": switch.changed_at,
            "changed_by": switch.changed_by}


@router.get("/switch", response=SwitchOut)
def get_switch(request):
    return _switch_out(SignalSwitch.get())


@router.post("/switch", response=SwitchOut)
def set_switch(request, payload: SwitchIn):
    """Global kill switch: pause or resume every feed at once."""
    require_owner(request)
    if payload.halted and not payload.reason.strip():
        raise HttpError(400, "say why you're pausing all signals")
    switch = SignalSwitch.get()
    switch.halted = payload.halted
    switch.reason = payload.reason.strip()[:300] if payload.halted else ""
    switch.changed_at = timezone.now()
    switch.changed_by = request.user.username
    switch.save()
    audit("recommendations.switch", request=request, halted=payload.halted, reason=payload.reason)
    return _switch_out(switch)


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
    code_version: str
    config_hash: str
    data_from: datetime | None
    data_to: datetime | None
    data_rows: int | None
    data_digest: str


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
            "code_version": r.code_version, "config_hash": r.config_hash, "data_from": r.data_from,
            "data_to": r.data_to, "data_rows": r.data_rows, "data_digest": r.data_digest,
        }
        for r in rows[: min(limit, 1000)]
    ]

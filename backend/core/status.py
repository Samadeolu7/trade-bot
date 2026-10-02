"""Public status page data: is the system alive and are the signals
flowing? Health only, with no positions, weights, prices or money, so it
can be read without logging in. Cached briefly and rate limited per IP, so
it costs almost nothing however often it's polled."""

from datetime import datetime, timedelta

import ccxt
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from core.audit import client_ip

router = Router(tags=["status"])

WORKER_HEARTBEAT_KEY = "worker:heartbeat"
CACHE_KEY = "public:status"
CACHE_SECONDS = 15
RATE_LIMIT = 30  # requests per IP per minute


class EngineStatus(Schema):
    healthy: bool
    last_seen: datetime | None


class WorkerStatus(Schema):
    healthy: bool
    last_seen: datetime | None
    jobs_running: int
    jobs_queued: int


class FeedStatus(Schema):
    name: str
    timeframe: str
    # ok | paused | stale | error | warming_up | off
    state: str
    last_candle_at: datetime | None
    last_checked_at: datetime | None


class AlertStatus(Schema):
    last_alert_at: datetime | None
    last_daily_summary_at: datetime | None
    undelivered_last_24h: int


class StatusOut(Schema):
    # ok | degraded | down
    overall: str
    problems: list[str]
    engine: EngineStatus
    worker: WorkerStatus
    signals_paused: bool
    feeds: list[FeedStatus]
    alerts: AlertStatus
    checked_at: datetime


def worker_heartbeat() -> None:
    cache.set(WORKER_HEARTBEAT_KEY, {"at": timezone.now().isoformat()}, timeout=3600)


def _seen(key: str) -> datetime | None:
    beat = cache.get(key)
    return datetime.fromisoformat(beat["at"]) if beat else None


def _feed_state(feed, now) -> str:
    if not feed.enabled:
        return "off"
    if feed.halted:
        return "paused"
    reason = feed.status_reason or ""
    if reason.startswith("error"):
        return "error"
    if reason.startswith("warming up"):
        return "warming_up"
    if reason.startswith("paused"):
        return "paused"
    if feed.last_bar_at is None:
        return "warming_up"
    period = timedelta(seconds=ccxt.Exchange.parse_timeframe(feed.timeframe))
    # the newest evaluated candle closes one period after it opens; allow one more
    if now - (feed.last_bar_at + period) > 2 * period:
        return "stale"
    return "ok"


def build_status(now: datetime | None = None) -> dict:
    from alerts.models import AlertEvent, AlertRule
    from recommendations.models import Feed, SignalSwitch
    from research.models import ResearchJob
    from trading.engine.loop import HEARTBEAT_KEY

    now = now or timezone.now()
    problems = []
    stale = settings.ENGINE["heartbeat_stale_seconds"]

    engine_seen = _seen(HEARTBEAT_KEY)
    engine_ok = engine_seen is not None and (now - engine_seen).total_seconds() < stale
    if not engine_ok:
        problems.append("engine not running")

    running = ResearchJob.objects.filter(status=ResearchJob.Status.RUNNING).count()
    queued = ResearchJob.objects.filter(status=ResearchJob.Status.QUEUED).count()
    worker_seen = _seen(WORKER_HEARTBEAT_KEY)
    # a long research job blocks the worker's loop, so a busy worker counts as alive
    worker_ok = running > 0 or (worker_seen is not None and (now - worker_seen).total_seconds() < 120)
    if not worker_ok:
        problems.append("research worker not running")

    switch = SignalSwitch.get()
    if switch.halted:
        problems.append("all signals paused")
    feeds = []
    for feed in Feed.objects.order_by("name"):
        state = _feed_state(feed, now)
        if state in ("paused", "stale", "error"):
            problems.append(f"{feed.name}: {state.replace('_', ' ')}")
        feeds.append({"name": feed.name, "timeframe": feed.timeframe, "state": state,
                      "last_candle_at": feed.last_bar_at, "last_checked_at": feed.last_run_at})

    last_alert = AlertEvent.objects.order_by("-created_at").values_list("created_at", flat=True).first()
    last_summary = (AlertEvent.objects.filter(kind=AlertRule.Kind.DAILY_SUMMARY).order_by("-created_at")
                    .values_list("created_at", flat=True).first())
    undelivered = AlertEvent.objects.filter(created_at__gte=now - timedelta(hours=24), delivered=False).count()
    if undelivered:
        problems.append(f"{undelivered} alert(s) not delivered to Telegram in the last 24h")

    overall = "down" if not engine_ok else ("degraded" if problems else "ok")
    return {
        "overall": overall, "problems": problems,
        "engine": {"healthy": engine_ok, "last_seen": engine_seen},
        "worker": {"healthy": worker_ok, "last_seen": worker_seen, "jobs_running": running, "jobs_queued": queued},
        "signals_paused": switch.halted, "feeds": feeds,
        "alerts": {"last_alert_at": last_alert, "last_daily_summary_at": last_summary,
                   "undelivered_last_24h": undelivered},
        "checked_at": now,
    }


@router.get("/status", response=StatusOut, auth=None)
def public_status(request):
    """System health, readable without logging in."""
    ip = client_ip(request) or "unknown"
    bucket = f"status-rate:{ip}:{timezone.now():%Y%m%d%H%M}"
    count = cache.get_or_set(bucket, 0, timeout=90)
    if count >= RATE_LIMIT:
        raise HttpError(429, f"at most {RATE_LIMIT} status requests a minute")
    try:
        cache.incr(bucket)
    except ValueError:
        cache.set(bucket, 1, timeout=90)
    status = cache.get(CACHE_KEY)
    if status is None:
        status = build_status()
        cache.set(CACHE_KEY, status, CACHE_SECONDS)
    return status

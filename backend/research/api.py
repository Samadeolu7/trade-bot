from datetime import datetime


from ninja import Router, Schema
from ninja.errors import HttpError

from bot.research.lifecycle import STAGES
from core.audit import audit
from django.db.models import F
from django.utils import timezone

from research.jobs import JobError, RepeatJobError, check_not_repeat, validate_params
from research.keys import create_key, research_auth
from research.legacy_reports import legacy_reports
from research.models import (
    Experiment,
    ResearchApiKey,
    ResearchJob,
    ShadowRebalance,
    ShadowTrade,
    StrategyLifecycle,
)
from trading.permissions import require_owner, require_verified

router = Router(tags=["research"])


class ExperimentOut(Schema):
    id: int
    created_at: datetime
    kind: str
    strategy: str
    strategy_label: str
    symbol: str
    timeframe: str
    window_start: str
    window_end: str
    touched_holdout: bool
    config_hash: str
    result: dict
    decision: str
    decision_reason: str


class ExperimentPage(Schema):
    total: int
    by_kind: dict[str, int]
    items: list[ExperimentOut]


@router.get("/experiments", response=ExperimentPage, auth=research_auth)
def list_experiments(request, strategy: str | None = None, kind: str | None = None,
                     decision: str | None = None, limit: int = 100, offset: int = 0):
    rows = Experiment.objects.all()
    if strategy:
        rows = rows.filter(strategy=strategy)
    if kind:
        rows = rows.filter(kind=kind)
    if decision:
        rows = rows.filter(decision=decision)
    by_kind: dict[str, int] = {}
    for k in Experiment.objects.values_list("kind", flat=True):
        by_kind[k] = by_kind.get(k, 0) + 1
    return {"total": rows.count(), "by_kind": by_kind,
            "items": list(rows.order_by("-created_at")[offset: offset + min(limit, 500)])}


class DecisionIn(Schema):
    decision: str
    reason: str


@router.post("/experiments/{experiment_id}/decision", response=ExperimentOut)
def decide(request, experiment_id: int, payload: DecisionIn):
    """The only way an experiment gets a verdict: a person records it."""
    require_owner(request)
    experiment = Experiment.objects.filter(pk=experiment_id).first()
    if experiment is None:
        raise HttpError(404, "no such experiment")
    if payload.decision not in ("accepted", "rejected", "inconclusive"):
        raise HttpError(400, "decision must be accepted, rejected or inconclusive")
    if not payload.reason.strip():
        raise HttpError(400, "give a reason; the graveyard is only useful if it says why")
    experiment.decision = payload.decision
    experiment.decision_reason = payload.reason.strip()
    experiment.save(update_fields=["decision", "decision_reason"])
    audit("research.decision", request=request, target=f"experiment:{experiment.pk}",
          decision=payload.decision, reason=payload.reason)
    return experiment


class LifecycleOut(Schema):
    label: str
    stage: str
    note: str
    updated_at: datetime


class LifecyclePage(Schema):
    stages: list[str]
    items: list[LifecycleOut]


@router.get("/lifecycle", response=LifecyclePage)
def lifecycle(request):
    return {"stages": STAGES, "items": list(StrategyLifecycle.objects.all())}


class LifecycleIn(Schema):
    label: str
    stage: str
    note: str = ""


@router.post("/lifecycle", response=LifecycleOut)
def set_lifecycle(request, payload: LifecycleIn):
    require_owner(request)
    if payload.stage not in STAGES:
        raise HttpError(400, f"stage must be one of {', '.join(STAGES)}")
    item, _ = StrategyLifecycle.objects.update_or_create(
        label=payload.label.strip(),
        defaults={"stage": payload.stage, "note": payload.note, "updated_by": request.user},
    )
    audit("research.lifecycle", request=request, target=f"strategy:{item.label}", stage=item.stage, note=payload.note)
    return item


class JobOut(Schema):
    id: int
    kind: str
    params: dict
    status: str
    result: dict
    error: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobIn(Schema):
    strategy: str
    symbol: str = "BTC/USDT"
    timeframe: str = "4h"
    baseline: str = "donchian"
    # "section.key" -> list of values; every combination is run
    params: dict[str, list] = {}


@router.get("/jobs", response=list[JobOut], auth=research_auth)
def list_jobs(request, limit: int = 50):
    return list(ResearchJob.objects.order_by("-created_at")[: min(limit, 200)])


@router.get("/jobs/{job_id}", response=JobOut, auth=research_auth)
def get_job(request, job_id: int):
    job = ResearchJob.objects.filter(pk=job_id).first()
    if job is None:
        raise HttpError(404, "no such job")
    return job


@router.post("/jobs", response=JobOut, auth=research_auth)
def create_job(request, payload: JobIn):
    key = getattr(request, "research_key", None)
    if key is None:
        if request.user.role == "viewer":
            raise HttpError(403, "viewers can't start research jobs")
        require_verified(request)
    elif key.jobs_started >= key.max_jobs:
        raise HttpError(429, f"this key has started its limit of {key.max_jobs} jobs; create a new one")
    try:
        params = validate_params(ResearchJob.Kind.RESEARCH_REPORT, payload.dict())
        check_not_repeat(params)
    except RepeatJobError as exc:
        raise HttpError(409, str(exc)) from exc
    except JobError as exc:
        raise HttpError(400, str(exc)) from exc
    job = ResearchJob.objects.create(kind=ResearchJob.Kind.RESEARCH_REPORT, params=params, created_by=request.user)
    if key is not None:
        ResearchApiKey.objects.filter(pk=key.pk).update(jobs_started=F("jobs_started") + 1)
    audit("research.job", request=request, target=f"job:{job.pk}", params=params,
          via_key=f"{key.name} ({key.prefix})" if key else "")
    return job


class PortfolioJobIn(Schema):
    timeframe: str = "4h"
    # symbols the monthly universe is picked from; blank = the default pool
    pool: list[str] = []
    # universe sizes to compare (top N by trailing dollar volume)
    sizes: list[int] = [10]
    # single-value donchian_ensemble.* overrides
    params: dict = {}


@router.post("/portfolio-jobs", response=JobOut, auth=research_auth)
def create_portfolio_job(request, payload: PortfolioJobIn):
    """A rotational multi-coin donchian_ensemble backtest on the usual
    train/test windows (holdout excluded)."""
    from research.portfolio_jobs import validate_portfolio_params

    key = getattr(request, "research_key", None)
    if key is None:
        if request.user.role == "viewer":
            raise HttpError(403, "viewers can't start research jobs")
        require_verified(request)
    elif key.jobs_started >= key.max_jobs:
        raise HttpError(429, f"this key has started its limit of {key.max_jobs} jobs; create a new one")
    try:
        params = validate_portfolio_params(payload.dict())
    except JobError as exc:
        raise HttpError(400, str(exc)) from exc
    busy = ResearchJob.objects.filter(kind=ResearchJob.Kind.PORTFOLIO_REPORT, params=params,
                                      status__in=[ResearchJob.Status.QUEUED, ResearchJob.Status.RUNNING]).first()
    if busy:
        raise HttpError(409, f"An identical portfolio job (#{busy.pk}) is already {busy.status}.")
    done = ResearchJob.objects.filter(kind=ResearchJob.Kind.PORTFOLIO_REPORT, params=params,
                                      status=ResearchJob.Status.DONE).order_by("-created_at").first()
    if done and (done.result.get("header") or {}).get("code") == _code_version():
        raise HttpError(409, f"Already run with exactly these settings on the current code (job #{done.pk}).")
    job = ResearchJob.objects.create(kind=ResearchJob.Kind.PORTFOLIO_REPORT, params=params, created_by=request.user)
    if key is not None:
        ResearchApiKey.objects.filter(pk=key.pk).update(jobs_started=F("jobs_started") + 1)
    audit("research.job", request=request, target=f"job:{job.pk}", params=params,
          via_key=f"{key.name} ({key.prefix})" if key else "")
    return job


def _code_version() -> str:
    from research.jobs import code_version

    return code_version()


class ApiKeyOut(Schema):
    id: int
    name: str
    prefix: str
    status: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    max_jobs: int
    jobs_started: int


class ApiKeyIn(Schema):
    name: str
    hours: int = 24
    max_jobs: int = 30


class ApiKeyCreated(ApiKeyOut):
    key: str


@router.get("/keys", response=list[ApiKeyOut])
def list_keys(request):
    require_owner(request)
    return list(ResearchApiKey.objects.all()[:100])


@router.post("/keys", response=ApiKeyCreated)
def new_key(request, payload: ApiKeyIn):
    """Shows the key once; only its hash is kept."""
    require_owner(request)
    require_verified(request)
    try:
        row, key = create_key(request.user, payload.name, payload.hours, payload.max_jobs)
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    audit("research.key_created", request=request, target=f"research_key:{row.prefix}",
          name=row.name, expires_at=row.expires_at.isoformat(), max_jobs=row.max_jobs)
    return {**ApiKeyOut.from_orm(row).dict(), "key": key}


@router.post("/keys/{key_id}/revoke", response=ApiKeyOut)
def revoke_key(request, key_id: int):
    require_owner(request)
    row = ResearchApiKey.objects.filter(pk=key_id).first()
    if row is None:
        raise HttpError(404, "no such key")
    if row.revoked_at is None:
        row.revoked_at = timezone.now()
        row.save(update_fields=["revoked_at"])
        audit("research.key_revoked", request=request, target=f"research_key:{row.prefix}", name=row.name)
    return row


class ShadowTradeOut(Schema):
    strategy_label: str
    symbol: str
    timeframe: str
    direction: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    pnl_pct: float
    exit_reason: str


class ShadowRebalanceOut(Schema):
    strategy_label: str
    timeframe: str
    bar_time: datetime
    price: float
    from_weight: float
    to_weight: float
    equity: float


class ShadowHistory(Schema):
    trades: list[ShadowTradeOut]
    rebalances: list[ShadowRebalanceOut]


@router.get("/shadow-history", response=ShadowHistory)
def shadow_history(request, strategy_label: str | None = None):
    trades = ShadowTrade.objects.all()
    rebalances = ShadowRebalance.objects.all()
    if strategy_label:
        trades = trades.filter(strategy_label=strategy_label)
        rebalances = rebalances.filter(strategy_label=strategy_label)
    return {"trades": list(trades[:1000]), "rebalances": list(rebalances[:1000])}


class LegacyReportOut(Schema):
    id: int
    created_at: datetime
    kind: str
    header: dict
    runs: list[dict]


@router.get("/legacy-reports", response=list[LegacyReportOut], auth=research_auth)
def list_legacy_reports(request):
    """Reports run by the CLI (the Research Report workflow), rebuilt from
    their experiment rows."""
    return legacy_reports()


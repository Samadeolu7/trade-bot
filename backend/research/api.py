from datetime import datetime


from ninja import Router, Schema
from ninja.errors import HttpError

from bot.research.lifecycle import STAGES
from core.audit import audit
from research.jobs import JobError, validate_params
from research.legacy_reports import legacy_reports
from research.models import Experiment, ResearchJob, ShadowRebalance, ShadowTrade, StrategyLifecycle
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


@router.get("/experiments", response=ExperimentPage)
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


@router.get("/jobs", response=list[JobOut])
def list_jobs(request, limit: int = 50):
    return list(ResearchJob.objects.order_by("-created_at")[: min(limit, 200)])


@router.get("/jobs/{job_id}", response=JobOut)
def get_job(request, job_id: int):
    job = ResearchJob.objects.filter(pk=job_id).first()
    if job is None:
        raise HttpError(404, "no such job")
    return job


@router.post("/jobs", response=JobOut)
def create_job(request, payload: JobIn):
    if request.user.role == "viewer":
        raise HttpError(403, "viewers can't start research jobs")
    require_verified(request)
    try:
        params = validate_params(ResearchJob.Kind.RESEARCH_REPORT, payload.dict())
    except JobError as exc:
        raise HttpError(400, str(exc)) from exc
    job = ResearchJob.objects.create(kind=ResearchJob.Kind.RESEARCH_REPORT, params=params, created_by=request.user)
    audit("research.job", request=request, target=f"job:{job.pk}", params=params)
    return job


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


@router.get("/legacy-reports", response=list[LegacyReportOut])
def list_legacy_reports(request):
    """Reports run by the CLI (the Research Report workflow), rebuilt from
    their experiment rows."""
    return legacy_reports()


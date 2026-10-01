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
    Hypothesis,
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
    rows = Experiment.objects.defer("returns")
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


class HypothesisIn(Schema):
    title: str
    statement: str
    family: str
    pass_criteria: dict = {}
    trial_budget: int = 20


class HypothesisOut(Schema):
    id: int
    title: str
    statement: str
    family: str
    pass_criteria: dict
    trial_budget: int
    trials_used: int
    status: str
    conclusion: str
    created_by: str
    created_at: datetime
    concluded_by: str
    concluded_at: datetime | None


def _hypothesis_out(h: Hypothesis) -> dict:
    return {**{f: getattr(h, f) for f in (
        "id", "title", "statement", "family", "pass_criteria", "trial_budget", "status", "conclusion",
        "created_by", "created_at", "concluded_by", "concluded_at")}, "trials_used": h.trials_used()}


def _actor(request) -> str:
    key = getattr(request, "research_key", None)
    return f"{request.user.username} via key {key.prefix}" if key else request.user.username


@router.get("/hypotheses", response=list[HypothesisOut], auth=research_auth)
def list_hypotheses(request, status: str | None = None, family: str | None = None):
    rows = Hypothesis.objects.all()
    if status:
        rows = rows.filter(status=status)
    if family:
        rows = rows.filter(family=family)
    return [_hypothesis_out(h) for h in rows[:500]]


@router.post("/hypotheses", response=HypothesisOut, auth=research_auth)
def create_hypothesis(request, payload: HypothesisIn):
    """Pre-register an idea before running anything for it."""
    if getattr(request, "research_key", None) is None:
        if request.user.role == "viewer":
            raise HttpError(403, "viewers can't register hypotheses")
        require_verified(request)
    title, statement, family = payload.title.strip(), payload.statement.strip(), payload.family.strip().lower()
    if not title or not statement or not family:
        raise HttpError(400, "a hypothesis needs a title, a statement of what should work and why, and a family")
    if not 1 <= payload.trial_budget <= 100:
        raise HttpError(400, "trial budget must be from 1 to 100 variants")
    h = Hypothesis.objects.create(title=title[:120], statement=statement, family=family[:40],
                                  pass_criteria=payload.pass_criteria, trial_budget=payload.trial_budget,
                                  created_by=_actor(request))
    audit("research.hypothesis", request=request, target=f"hypothesis:{h.pk}", title=h.title, family=h.family)
    return _hypothesis_out(h)


class ConcludeIn(Schema):
    status: str  # passed | failed | abandoned
    conclusion: str


@router.post("/hypotheses/{hypothesis_id}/conclude", response=HypothesisOut)
def conclude_hypothesis(request, hypothesis_id: int, payload: ConcludeIn):
    """A person's verdict; research keys can't conclude."""
    require_owner(request)
    h = Hypothesis.objects.filter(pk=hypothesis_id).first()
    if h is None:
        raise HttpError(404, "no such hypothesis")
    if payload.status not in ("passed", "failed", "abandoned"):
        raise HttpError(400, "status must be passed, failed or abandoned")
    if not payload.conclusion.strip():
        raise HttpError(400, "write what the evidence showed")
    h.status, h.conclusion = payload.status, payload.conclusion.strip()
    h.concluded_by, h.concluded_at = request.user.username, timezone.now()
    h.save(update_fields=["status", "conclusion", "concluded_by", "concluded_at"])
    audit("research.conclude", request=request, target=f"hypothesis:{h.pk}", status=h.status)
    return _hypothesis_out(h)


class BudgetIn(Schema):
    trial_budget: int


@router.post("/hypotheses/{hypothesis_id}/budget", response=HypothesisOut)
def set_budget(request, hypothesis_id: int, payload: BudgetIn):
    require_owner(request)
    h = Hypothesis.objects.filter(pk=hypothesis_id).first()
    if h is None:
        raise HttpError(404, "no such hypothesis")
    if not 1 <= payload.trial_budget <= 500:
        raise HttpError(400, "trial budget must be from 1 to 500")
    old = h.trial_budget
    h.trial_budget = payload.trial_budget
    h.save(update_fields=["trial_budget"])
    audit("research.budget", request=request, target=f"hypothesis:{h.pk}", old=old, new=h.trial_budget)
    return _hypothesis_out(h)


def _hypothesis_for_job(request, hypothesis_id: int | None, variants: int) -> Hypothesis | None:
    """Jobs started with a research key must belong to an open hypothesis,
    and no hypothesis may run more variants than its budget."""
    if hypothesis_id is None:
        if getattr(request, "research_key", None) is not None:
            raise HttpError(400, "jobs started with a research key need a hypothesis_id: register the idea first "
                                 "(POST /api/research/hypotheses)")
        return None
    h = Hypothesis.objects.filter(pk=hypothesis_id).first()
    if h is None:
        raise HttpError(404, "no such hypothesis")
    if h.status != Hypothesis.Status.OPEN:
        raise HttpError(400, f"hypothesis #{h.pk} is {h.status}; register a new one for a new idea")
    pending = sum(_variant_count(j) for j in h.jobs.filter(
        status__in=[ResearchJob.Status.QUEUED, ResearchJob.Status.RUNNING]))
    used = h.trials_used() + pending
    if used + variants > h.trial_budget:
        raise HttpError(409, f"hypothesis #{h.pk} has used {used} of its {h.trial_budget} trials; this job adds "
                             f"{variants}. Each variant tried makes a lucky result more likely, so the budget is "
                             "fixed; the owner can raise it with a reason.")
    return h


def _variant_count(job_or_params) -> int:
    params = job_or_params.params if hasattr(job_or_params, "params") else job_or_params
    if "sizes" in params:
        return len(params["sizes"])
    count = 1
    for values in (params.get("params") or {}).values():
        count *= len(values) if isinstance(values, list) else 1
    return count


class JobOut(Schema):
    id: int
    kind: str
    hypothesis_id: int | None
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
    # the pre-registered idea this tests (required with a research key)
    hypothesis_id: int | None = None


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
    hypothesis = _hypothesis_for_job(request, payload.hypothesis_id, _variant_count(params))
    job = ResearchJob.objects.create(kind=ResearchJob.Kind.RESEARCH_REPORT, params=params, created_by=request.user,
                                     hypothesis=hypothesis)
    if key is not None:
        ResearchApiKey.objects.filter(pk=key.pk).update(jobs_started=F("jobs_started") + 1)
    audit("research.job", request=request, target=f"job:{job.pk}", params=params,
          via_key=f"{key.name} ({key.prefix})" if key else "")
    return job


class WalkForwardJobIn(Schema):
    strategy: str
    symbol: str = "BTC/USDT"
    timeframe: str = "4h"
    # "section.key" -> list of values; with several variants, each fold uses
    # the one with the best Sharpe before it
    params: dict[str, list] = {}
    # first out-of-sample window start; folds then step by test_months up to the holdout
    first_test: str = "2022-01-01"
    test_months: int = 6
    hypothesis_id: int | None = None


@router.post("/walk-forward-jobs", response=JobOut, auth=research_auth)
def create_walk_forward_job(request, payload: WalkForwardJobIn):
    """Anchored walk-forward validation with a deflated Sharpe ratio and a
    bootstrap range (holdout excluded)."""
    from research.walkforward_jobs import check_not_repeat_walk_forward, validate_walk_forward_params

    key = getattr(request, "research_key", None)
    if key is None:
        if request.user.role == "viewer":
            raise HttpError(403, "viewers can't start research jobs")
        require_verified(request)
    elif key.jobs_started >= key.max_jobs:
        raise HttpError(429, f"this key has started its limit of {key.max_jobs} jobs; create a new one")
    try:
        params = validate_walk_forward_params(payload.dict())
        check_not_repeat_walk_forward(params)
    except RepeatJobError as exc:
        raise HttpError(409, str(exc)) from exc
    except JobError as exc:
        raise HttpError(400, str(exc)) from exc
    hypothesis = _hypothesis_for_job(request, payload.hypothesis_id, _variant_count(params))
    job = ResearchJob.objects.create(kind=ResearchJob.Kind.WALK_FORWARD, params=params, created_by=request.user,
                                     hypothesis=hypothesis)
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
    hypothesis_id: int | None = None


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
    hypothesis = _hypothesis_for_job(request, payload.hypothesis_id, _variant_count(params))
    job = ResearchJob.objects.create(kind=ResearchJob.Kind.PORTFOLIO_REPORT, params=params, created_by=request.user,
                                     hypothesis=hypothesis)
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


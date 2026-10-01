"""Runs research jobs requested from the app, the same way `python main.py
research-report` does: fixed train and test windows, a baseline next to
each variant, every window logged as an Experiment. The reserved holdout
window is always excluded here, with no override. A holdout check stays a
deliberate CLI action logged to notes/holdout_validations.md."""

import functools
import hashlib
import itertools
import json
import logging
from pathlib import Path

import pandas as pd
from django.utils import timezone

from bot.data.exchange import create_exchange
from bot.research.backtest_runner import apply_holdout_guard, run_backtest_summary, with_override
from bot.strategy.registry import STRATEGY_CATALOG
from market.candles import backfill_candles, backfill_funding, candles_df, funding_df
from research.models import Experiment, ResearchJob
from trading.services.bots import TIMEFRAMES, trade_bot_config

logger = logging.getLogger(__name__)

TRAIN = ("2020-01-01", "2023-12-31")
TEST_START = "2024-01-01"
MAX_VARIANTS = 24
EQUITY_POINTS = 300


class JobError(Exception):
    pass


class RepeatJobError(JobError):
    """Every variant in the request has already been run with exactly
    these settings on the current code, so running it would only
    reproduce a result that already exists."""


@functools.lru_cache(maxsize=1)
def code_version() -> str:
    """Hash of the strategy and backtest code (every .py file under bot/).
    Part of a run's identity: after a fix to a strategy or the engine, the
    same settings can legitimately give a different result, so they're
    allowed to run again. Deploys that don't touch bot/ leave it
    unchanged."""
    import bot

    root = Path(bot.__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def run_key(strategy: str, symbol: str, timeframe: str, strategy_config: dict, backtest_config: dict,
            holdout_start: str | None) -> str:
    """Everything that decides a research report variant's numbers. Same
    key means the same computation on the same data windows."""
    identity = {
        "strategy": strategy, "symbol": symbol, "timeframe": timeframe,
        "config": _effective_config(strategy_config), "backtest": backtest_config,
        "train": TRAIN, "test_start": TEST_START, "holdout_start": holdout_start,
        "code": code_version(),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _effective_config(strategy_config: dict) -> dict:
    """Drops settings that can't affect the result, so variants that only
    differ in those count as the same run: with pyramiding off (max_adds
    0), the other pyramid options do nothing."""
    pyramid = strategy_config.get("pyramid") or {}
    if pyramid and not int(pyramid.get("max_adds", 0) or 0):
        return {**strategy_config, "pyramid": {"max_adds": 0}}
    return strategy_config


def variants(params: dict, base_config: dict) -> list[tuple[str, dict]]:
    """(label, resolved strategy config) for every combination in the grid."""
    grid = params["params"]
    keys = list(grid)
    out = []
    for combo in itertools.product(*[grid[k] for k in keys]) if keys else [()]:
        strategy_config = base_config
        for path, value in zip(keys, combo):
            section, key = path.split(".", 1)
            strategy_config = with_override(strategy_config, section, key, value)
        label = ", ".join(f"{k.split('.', 1)[1]}={v}" for k, v in zip(keys, combo)) or "defaults"
        out.append((f"{params['strategy']} {label}", strategy_config))
    return out


def earlier_runs(keys: list[str]) -> dict[str, list[Experiment]]:
    """For each run_key that has already been run to completion (both
    windows recorded), its experiment rows, newest first per window."""
    found: dict[str, dict[str, Experiment]] = {}
    for row in Experiment.objects.filter(kind="research_report", run_key__in=keys).order_by("-created_at"):
        # by start date, not an exact match on TRAIN[0]: the train window
        # starts wherever the data does if history begins after 2020
        window = "test" if row.window_start[:10] >= TEST_START else "train"
        found.setdefault(row.run_key, {}).setdefault(window, row)
    return {key: [rows["train"], rows["test"]] for key, rows in found.items() if len(rows) == 2}


def check_not_repeat(params: dict) -> None:
    """Refuses a research job that would only reproduce existing results:
    every non-baseline variant already run with the same settings on the
    same code, or an identical job already queued or running. A job where
    only some variants are repeats is allowed; the worker reuses the
    earlier results for those instead of recomputing them."""
    in_flight = ResearchJob.objects.filter(
        kind=ResearchJob.Kind.RESEARCH_REPORT,
        status__in=[ResearchJob.Status.QUEUED, ResearchJob.Status.RUNNING],
    )
    for job in in_flight:
        if _same_request(job.params, params):
            raise RepeatJobError(f"An identical research job (#{job.pk}) is already {job.status}. Wait for its results.")

    config = trade_bot_config()
    holdout_start = config.get("validation", {}).get("holdout_start")
    backtest_config = config.get("backtest", {})
    keyed = [
        (label, run_key(params["strategy"], params["symbol"], params["timeframe"], cfg, backtest_config, holdout_start))
        for label, cfg in variants(params, config.get("strategy", {}))
    ]
    earlier = earlier_runs([key for _, key in keyed])
    if keyed and all(key in earlier for _, key in keyed):
        where = []
        for label, key in keyed:
            row = earlier[key][1]
            ref = f"job #{row.job_id}" if row.job_id else f"experiment #{row.pk}"
            where.append(f"{label} ({ref}, {row.created_at:%Y-%m-%d %H:%M} UTC)")
        raise RepeatJobError(
            "Already run with exactly these settings on the current code: " + "; ".join(where)
            + ". Open that report instead, or change a setting."
        )


def _same_request(a: dict, b: dict) -> bool:
    def norm(p: dict) -> str:
        grid = {k: sorted(v, key=repr) for k, v in (p.get("params") or {}).items()}
        return json.dumps({**p, "params": grid}, sort_keys=True, default=str)

    return norm(a) == norm(b)


def validate_params(kind: str, params: dict) -> dict:
    strategy = params.get("strategy")
    if strategy not in STRATEGY_CATALOG:
        raise JobError(f"unknown strategy {strategy!r}")
    if params.get("timeframe", "4h") not in TIMEFRAMES:
        raise JobError(f"timeframe must be one of {', '.join(TIMEFRAMES)}")
    baseline = params.get("baseline", "donchian")
    if baseline != "none" and baseline not in STRATEGY_CATALOG:
        raise JobError(f"unknown baseline {baseline!r}")
    grid = params.get("params") or {}
    allowed = set(STRATEGY_CATALOG[strategy].config_sections)
    combos = 1
    for path, values in grid.items():
        if "." not in path or path.split(".", 1)[0] not in allowed:
            raise JobError(f"{path!r} isn't a parameter {strategy} reads")
        if not isinstance(values, list) or not values:
            raise JobError(f"{path!r} needs a list of values")
        combos *= len(values)
    if combos > MAX_VARIANTS:
        raise JobError(f"{combos} variants requested; the limit is {MAX_VARIANTS} per job")
    _check_pyramid(grid)
    return {
        "strategy": strategy, "symbol": params.get("symbol", "BTC/USDT"),
        "timeframe": params.get("timeframe", "4h"), "baseline": baseline, "params": grid,
    }


def _check_pyramid(grid: dict) -> None:
    """Catches pyramid settings that can't do what was meant: a step given
    as a percentage instead of a fraction, or pyramid options with adds
    switched off, which would only reproduce the plain strategy."""
    for value in grid.get("pyramid.add_step_pct", []):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value < 1:
            raise JobError(
                f"pyramid.add_step_pct is a fraction of price: 0.03 means 3%. Got {value!r}; use a value from 0 up to 1."
            )
    for value in grid.get("pyramid.max_adds", []):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise JobError(f"pyramid.max_adds is a whole number of extra units (0 = no adds). Got {value!r}.")
    other = sorted(path for path in grid if path.startswith("pyramid.") and path != "pyramid.max_adds")
    if other:
        default = int((trade_bot_config().get("strategy", {}).get("pyramid") or {}).get("max_adds", 0) or 0)
        max_adds = grid.get("pyramid.max_adds", [default])
        if not any(max_adds):
            raise JobError(
                f"{', '.join(other)} only matters when pyramid.max_adds is above 0, and every variant here has "
                "no adds, so the results would match the plain strategy. Add pyramid.max_adds (e.g. 3 or 5)."
            )


def _downsample(curve: pd.Series) -> list[list[float]]:
    if len(curve) == 0:
        return []
    step = max(1, len(curve) // EQUITY_POINTS)
    sampled = curve.iloc[::step]
    if sampled.index[-1] != curve.index[-1]:
        sampled = pd.concat([sampled, curve.iloc[-1:]])
    return [[int(ts.timestamp()), round(float(v), 2)] for ts, v in sampled.items()]


def _record(kind, strategy, symbol, timeframe, window_df, strategy_config, summary, key="", job=None) -> None:
    config_json = json.dumps(strategy_config, sort_keys=True, default=str)
    Experiment.objects.create(
        run_key=key, job=job, hypothesis_id=job.hypothesis_id if job else None, git_commit=code_version(),
        created_at=timezone.now(), kind=kind, strategy=strategy, strategy_label=strategy, symbol=symbol,
        timeframe=timeframe, window_start=str(window_df.index.min()), window_end=str(window_df.index.max()),
        touched_holdout=False, config=json.loads(config_json),
        config_hash=hashlib.sha256(config_json.encode()).hexdigest()[:12],
        data_version=f"{len(window_df)} bars, {window_df.index.min()}–{window_df.index.max()}",
        result=json.loads(json.dumps(summary, default=str)),
    )


def run_research_report(params: dict, fetch: bool = True, job: ResearchJob | None = None) -> dict:
    config = trade_bot_config()
    exchange_id = config["exchange"]["id"]
    symbol, timeframe = params["symbol"], params["timeframe"]
    if fetch:
        backfill_candles(create_exchange(exchange_id), exchange_id, symbol, timeframe,
                         config["backfill"]["start_date"])
    holdout_start = config.get("validation", {}).get("holdout_start")
    all_df, _ = apply_holdout_guard(candles_df(exchange_id, symbol, timeframe), holdout_start,
                                    allow_holdout=False, context_label="research job")
    windows = {
        "train": all_df[(all_df.index >= pd.Timestamp(TRAIN[0], tz="UTC")) & (all_df.index <= pd.Timestamp(TRAIN[1], tz="UTC"))],
        "test": all_df[all_df.index >= pd.Timestamp(TEST_START, tz="UTC")],
    }
    base_config = config.get("strategy", {})
    backtest_config = config.get("backtest", {})

    funding = None
    if "funding_filtered" in (params["strategy"], params["baseline"]):
        fc = config.get("funding", {})
        if fetch:
            backfill_funding(fc.get("exchange_id", "binanceusdm"), fc.get("symbol", "BTC/USDT:USDT"),
                             fc.get("start_date", config["backfill"]["start_date"]))
        funding = funding_df(fc.get("exchange_id", "binanceusdm"), fc.get("symbol", "BTC/USDT:USDT"))

    def run(name: str, strategy_config: dict) -> dict:
        key = run_key(name, symbol, timeframe, strategy_config, backtest_config, holdout_start)
        out = {"windows": {}, "equity": {}}
        for window, window_df in windows.items():
            if len(window_df) == 0:
                out["windows"][window] = None
                continue
            summary, curve = run_backtest_summary(
                window_df, name, strategy_config, backtest_config, timeframe,
                funding if name == "funding_filtered" else None,
                warmup_df=all_df[all_df.index <= window_df.index.max()],
            )
            _record("research_report", name, symbol, timeframe, window_df, strategy_config, summary, key, job)
            out["windows"][window] = json.loads(json.dumps(summary, default=str))
            out["equity"][window] = _downsample(curve)
        return out

    def reuse(rows: list[Experiment]) -> dict:
        """An identical variant's stored summaries, instead of recomputing
        them. Equity curves aren't stored per experiment, so none are shown."""
        train, test = rows
        ref = f"job #{test.job_id}" if test.job_id else f"experiment #{test.pk}"
        return {"windows": {"train": train.result, "test": test.result}, "equity": {},
                "earlier_result": f"{ref}, {test.created_at:%Y-%m-%d %H:%M} UTC"}

    keyed = [
        (label, cfg, run_key(params["strategy"], symbol, timeframe, cfg, backtest_config, holdout_start))
        for label, cfg in variants(params, base_config)
    ]
    earlier = earlier_runs([key for _, _, key in keyed])
    runs = []
    computed: dict[str, tuple[str, dict]] = {}  # run_key -> (label, result) within this job
    for label, strategy_config, key in keyed:
        if key in earlier:
            runs.append({"label": f"{label} (earlier result)", "baseline": False, **reuse(earlier[key])})
        elif key in computed:
            first_label, result = computed[key]
            runs.append({"label": f"{label} (same as {first_label})", "baseline": False, **result})
        else:
            result = run(params["strategy"], strategy_config)
            computed[key] = (label, result)
            runs.append({"label": label, "baseline": False, **result})
    if params["baseline"] != "none":
        runs.append({"label": f"baseline: {params['baseline']}", "baseline": True,
                     **run(params["baseline"], base_config)})

    def span(df):
        return f"{df.index.min():%Y-%m-%d}..{df.index.max():%Y-%m-%d} ({len(df)} bars)" if len(df) else "no data"

    return {
        "header": {
            "strategy": params["strategy"], "symbol": symbol, "timeframe": timeframe,
            "train": span(windows["train"]), "test": span(windows["test"]),
            "holdout": f"excluded from {holdout_start}" if holdout_start else "none configured",
            "costs": f"fee={backtest_config.get('fee', 0.001)} slippage={backtest_config.get('slippage', 0.0005)}",
        },
        "runs": runs,
    }


def run_job(job: ResearchJob, fetch: bool = True) -> ResearchJob:
    job.status = ResearchJob.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    try:
        if job.kind == ResearchJob.Kind.PORTFOLIO_REPORT:
            from research.portfolio_jobs import run_portfolio_report, validate_portfolio_params

            job.result = run_portfolio_report(validate_portfolio_params(job.params), fetch=fetch, job=job)
        elif job.kind == ResearchJob.Kind.WALK_FORWARD:
            from research.walkforward_jobs import run_walk_forward_report, validate_walk_forward_params

            job.result = run_walk_forward_report(validate_walk_forward_params(job.params), fetch=fetch, job=job)
        else:
            job.result = run_research_report(validate_params(job.kind, job.params), fetch=fetch, job=job)
        job.status = ResearchJob.Status.DONE
    except Exception as exc:
        logger.exception("research job %s failed", job.pk)
        job.error = str(exc)[:2000]
        job.status = ResearchJob.Status.FAILED
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "result", "error", "finished_at"])
    return job

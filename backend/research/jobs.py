"""Runs research jobs requested from the app, the same way `python main.py
research-report` does: fixed train and test windows, a baseline next to
each variant, every window logged as an Experiment. The reserved holdout
window is always excluded here, with no override. A holdout check stays a
deliberate CLI action logged to notes/holdout_validations.md."""

import hashlib
import itertools
import json
import logging

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
    return {
        "strategy": strategy, "symbol": params.get("symbol", "BTC/USDT"),
        "timeframe": params.get("timeframe", "4h"), "baseline": baseline, "params": grid,
    }


def _downsample(curve: pd.Series) -> list[list[float]]:
    if len(curve) == 0:
        return []
    step = max(1, len(curve) // EQUITY_POINTS)
    sampled = curve.iloc[::step]
    if sampled.index[-1] != curve.index[-1]:
        sampled = pd.concat([sampled, curve.iloc[-1:]])
    return [[int(ts.timestamp()), round(float(v), 2)] for ts, v in sampled.items()]


def _record(kind, strategy, symbol, timeframe, window_df, strategy_config, summary) -> None:
    config_json = json.dumps(strategy_config, sort_keys=True, default=str)
    Experiment.objects.create(
        created_at=timezone.now(), kind=kind, strategy=strategy, strategy_label=strategy, symbol=symbol,
        timeframe=timeframe, window_start=str(window_df.index.min()), window_end=str(window_df.index.max()),
        touched_holdout=False, config=json.loads(config_json),
        config_hash=hashlib.sha256(config_json.encode()).hexdigest()[:12],
        data_version=f"{len(window_df)} bars, {window_df.index.min()}–{window_df.index.max()}",
        result=json.loads(json.dumps(summary, default=str)),
    )


def run_research_report(params: dict, fetch: bool = True) -> dict:
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
            _record("research_report", name, symbol, timeframe, window_df, strategy_config, summary)
            out["windows"][window] = json.loads(json.dumps(summary, default=str))
            out["equity"][window] = _downsample(curve)
        return out

    grid = params["params"]
    keys = list(grid)
    runs = []
    for combo in itertools.product(*[grid[k] for k in keys]) if keys else [()]:
        strategy_config = base_config
        for path, value in zip(keys, combo):
            section, key = path.split(".", 1)
            strategy_config = with_override(strategy_config, section, key, value)
        label = ", ".join(f"{k.split('.', 1)[1]}={v}" for k, v in zip(keys, combo)) or "defaults"
        runs.append({"label": f"{params['strategy']} {label}", "baseline": False,
                     **run(params["strategy"], strategy_config)})
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
        job.result = run_research_report(validate_params(job.kind, job.params), fetch=fetch)
        job.status = ResearchJob.Status.DONE
    except Exception as exc:
        logger.exception("research job %s failed", job.pk)
        job.error = str(exc)[:2000]
        job.status = ResearchJob.Status.FAILED
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "result", "error", "finished_at"])
    return job

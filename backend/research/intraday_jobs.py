"""Intraday research jobs: hypothesis #7 (bot/research/intraday_report.py)
on BTC/USDT 15-minute candles fetched by the server. The holdout is always
excluded. Every variant is recorded as an experiment, so the trial counts
stay honest."""

import json
import logging

import pandas as pd
from django.utils import timezone

from bot.data.exchange import create_exchange
from bot.research import intraday_report as R
from bot.research.backtest_runner import apply_holdout_guard
from market.candles import backfill_candles, backfill_head, candles_df
from research.jobs import JobError, RepeatJobError, code_version, run_key
from research.models import Experiment, ResearchJob
from trading.services.bots import trade_bot_config

logger = logging.getLogger(__name__)

KIND = "intraday"
SYMBOL, TIMEFRAME, START = "BTC/USDT", "15m", "2020-01-01T00:00:00Z"  # ccxt needs the time part
HISTORY_FROM = "2020-02-01"  # stored history must start by then, or the download is incomplete


def validate_intraday_params(params: dict) -> dict:
    batch = str(params.get("batch") or "all").upper()
    if batch not in ("A", "B", "ALL"):
        raise JobError("batch must be A, B or all")
    return {"hypothesis": 7, "batch": "all" if batch == "ALL" else batch}


def variant_count(params: dict) -> int:
    return sum(R.VARIANTS.values()) if params["batch"] == "all" else R.VARIANTS[params["batch"]]


def check_not_repeat_intraday(params: dict) -> None:
    busy = ResearchJob.objects.filter(kind=ResearchJob.Kind.INTRADAY, params=params,
                                      status__in=[ResearchJob.Status.QUEUED, ResearchJob.Status.RUNNING]).first()
    if busy:
        raise RepeatJobError(f"An identical intraday job (#{busy.pk}) is already {busy.status}.")
    done = ResearchJob.objects.filter(kind=ResearchJob.Kind.INTRADAY, params=params,
                                      status=ResearchJob.Status.DONE).order_by("-created_at").first()
    if done and (done.result.get("header") or {}).get("code") == code_version():
        raise RepeatJobError(f"Already run with exactly these settings on the current code (job #{done.pk}).")


def _record(job, label: str, scored: dict, holdout: str, df) -> None:
    key = run_key(f"{KIND}7:{label}", SYMBOL, TIMEFRAME, {}, {"judge": R.JUDGE, "robust": R.ROBUST}, holdout)
    Experiment.objects.create(
        run_key=key, job=job, hypothesis_id=job.hypothesis_id if job else None, git_commit=code_version(),
        created_at=timezone.now(), kind=KIND, strategy=label.split(" ")[0][:40], strategy_label=label,
        symbol=SYMBOL, timeframe=TIMEFRAME, window_start=str(df.index.min()), window_end=str(df.index.max()),
        touched_holdout=False, config={"hypothesis": 7, "variant": label}, config_hash=key[:12],
        data_version=f"{len(df)} bars, {df.index.min()}–{df.index.max()}",
        result=json.loads(json.dumps(scored, default=str)),
    )


def run_intraday_report(params: dict, fetch: bool = True, job: ResearchJob | None = None) -> dict:
    config = trade_bot_config()
    exchange_id = config["exchange"]["id"]
    if fetch:
        exchange = create_exchange(exchange_id)
        # head first: stored candles may only cover recent months, and
        # backfill_candles only moves forward from the newest one
        backfill_head(exchange, exchange_id, SYMBOL, TIMEFRAME, START)
        backfill_candles(exchange, exchange_id, SYMBOL, TIMEFRAME, START)
    holdout = config.get("validation", {}).get("holdout_start")
    df, _ = apply_holdout_guard(candles_df(exchange_id, SYMBOL, TIMEFRAME), holdout, allow_holdout=False,
                                context_label="intraday job")
    if len(df) == 0:
        raise JobError(f"no {SYMBOL} {TIMEFRAME} candles stored")
    df = df[df.index >= START]
    if len(df) == 0 or df.index.min() > pd.Timestamp(HISTORY_FROM, tz="UTC"):
        first = f"{df.index.min():%Y-%m-%d}" if len(df) else "nothing"
        raise JobError(f"{SYMBOL} {TIMEFRAME} history before the holdout starts at {first}, not 2020: "
                       "the download is incomplete")
    days, folds = R.setup(df)

    def log(msg):
        logger.info("intraday job %s:%s", job.pk if job else "-", msg)

    batches = {}
    for name in (("A", "B") if params["batch"] == "all" else (params["batch"],)):
        rows = R.batch_a(df, days, log) if name == "A" else R.batch_b(df, config.get("strategy", {}), days, log)
        scored = R.score(rows, folds)
        for s in scored:
            _record(job, s["label"], s, holdout, df)
        batches[name] = {"title": R.TITLES[name], "rows": scored}
    return {
        "header": {
            "hypothesis": 7, "symbol": SYMBOL, "timeframe": TIMEFRAME,
            "data": f"{df.index.min():%Y-%m-%d}..{df.index.max():%Y-%m-%d} ({len(df)} bars)",
            "folds": f"{len(folds)} anchored 6-month folds from 2022-01-01",
            "holdout": f"excluded from {holdout}" if holdout else "none configured",
            "costs": f"judged at {R.JUDGE:.2%} round trip, must still profit at {R.ROBUST:.2%}; "
                     f"rollover round trip {R.DODGE:.2%}",
            "trials": R.TRIALS, "code": code_version(),
        },
        "batches": batches,
    }


def report_lines(result: dict) -> list[str]:
    """The Telegram summary of a finished intraday job."""
    lines = []
    for name, batch in result["batches"].items():
        rows = batch["rows"]
        passed = [r["label"] for r in rows if r["pass"]]
        lines.append(f"{batch['title']}: {len(passed)} of {len(rows)} pass"
                     + (f" ({', '.join(passed)})" if passed else "") + ".")
        for r in sorted(rows, key=lambda r: r["sharpe"], reverse=True)[:5]:
            lines.append(f"  {r['label']}: Sharpe {r['sharpe']} ({r['sharpe_robust']} at {R.ROBUST:.2%}), "
                         f"{r['return_pct']:+}%, DD {r['max_drawdown_pct']}%, {r['positive_folds']}/{r['folds']} folds+, "
                         f"DSR {r['deflated_sharpe']:.2f}")
    return lines

"""Walk-forward research jobs (bot/research/walk_forward.py): anchored
folds from `first_test` to the holdout start, the deflated Sharpe of the
stitched out-of-sample returns counting every variant ever tried for the
strategy, and a block-bootstrap range for Sharpe and drawdown. The holdout
is always excluded."""

import json
import math

import pandas as pd
from django.db.models import Q
from django.utils import timezone

from bot.backtest.metrics import periods_per_year
from bot.data.exchange import create_exchange
from bot.research.backtest_runner import apply_holdout_guard, run_strategy_backtest
from bot.research.stats import block_bootstrap, deflated_sharpe, per_bar_sharpe
from bot.research.walk_forward import daily_returns, make_folds, walk_forward
from market.candles import backfill_candles, backfill_funding, candles_df, funding_df
from research.jobs import JobError, RepeatJobError, _downsample, code_version, run_key, validate_params, variants
from research.models import Experiment, ResearchJob
from trading.services.bots import trade_bot_config

KIND = "walk_forward"
TEST_MONTHS = (3, 6, 12)
BOOTSTRAP_DAYS = 20  # block length: about a month of trading
DSR_PASS = 0.95


def validate_walk_forward_params(params: dict) -> dict:
    base = validate_params(ResearchJob.Kind.WALK_FORWARD, {**params, "baseline": "none"})
    base.pop("baseline")
    first_test = str(params.get("first_test") or "2022-01-01")
    try:
        first = pd.Timestamp(first_test, tz="UTC")
    except ValueError as exc:
        raise JobError(f"first_test {first_test!r} isn't a date") from exc
    if first < pd.Timestamp("2021-01-01", tz="UTC"):
        raise JobError("first_test must leave at least a year of training data (2021-01-01 or later)")
    holdout = trade_bot_config().get("validation", {}).get("holdout_start")
    if holdout and first >= pd.Timestamp(holdout, tz="UTC") - pd.DateOffset(months=6):
        raise JobError(f"first_test must be at least 6 months before the holdout ({holdout})")
    test_months = params.get("test_months", 6)
    if test_months not in TEST_MONTHS:
        raise JobError(f"test_months must be one of {', '.join(map(str, TEST_MONTHS))}")
    return {**base, "first_test": f"{first:%Y-%m-%d}", "test_months": test_months}


def _key(params: dict, strategy_config: dict, backtest_config: dict, holdout_start) -> str:
    name = f"{KIND}:{params['strategy']}:{params['first_test']}:{params['test_months']}"
    return run_key(name, params["symbol"], params["timeframe"], strategy_config, backtest_config, holdout_start)


def check_not_repeat_walk_forward(params: dict) -> None:
    busy = ResearchJob.objects.filter(kind=ResearchJob.Kind.WALK_FORWARD, params=params,
                                      status__in=[ResearchJob.Status.QUEUED, ResearchJob.Status.RUNNING]).first()
    if busy:
        raise RepeatJobError(f"An identical walk-forward job (#{busy.pk}) is already {busy.status}.")
    config = trade_bot_config()
    holdout = config.get("validation", {}).get("holdout_start")
    keys = [_key(params, cfg, config.get("backtest", {}), holdout) for _, cfg in variants(params, config.get("strategy", {}))]
    done = set(Experiment.objects.filter(kind=KIND, run_key__in=keys).values_list("run_key", flat=True))
    if keys and all(k in done for k in keys):
        job = Experiment.objects.filter(kind=KIND, run_key=keys[0]).order_by("-created_at").first().job_id
        raise RepeatJobError(f"Already run with exactly these settings on the current code (job #{job}).")


def _trials(strategy: str, own_keys: list[str], own_sharpes: list[float], ppy: float,
            family: str = "") -> tuple[int, float, str]:
    """(number of trials, variance of their per-bar Sharpe, where the
    variance came from). Trials = every distinct run of this strategy ever
    recorded, any market or timeframe, plus every run in the job's
    hypothesis family, plus this job's variants."""
    keys = set(own_keys)
    stored = []
    scope = Q(strategy=strategy) | (Q(hypothesis__family=family) if family else Q(pk__in=[]))
    for key, kind, start, result in Experiment.objects.filter(scope).exclude(run_key="").values_list(
            "run_key", "kind", "window_start", "result"):
        keys.add(key)
        if kind == "research_report" and start[:10] >= "2024-01-01" and result.get("sharpe_ratio") is not None:
            stored.append(float(result["sharpe_ratio"]) / math.sqrt(ppy))
    if len(own_sharpes) >= 2:
        return len(keys), float(pd.Series(own_sharpes).var()), "this job's variants"
    if len(stored) >= 2:
        return len(keys), float(pd.Series(stored).var()), f"{len(stored)} earlier test-window results"
    return len(keys), 0.0, "not enough trials to estimate; deflation off"


def run_walk_forward_report(params: dict, fetch: bool = True, job: ResearchJob | None = None) -> dict:
    config = trade_bot_config()
    exchange_id = config["exchange"]["id"]
    symbol, timeframe, strategy = params["symbol"], params["timeframe"], params["strategy"]
    if fetch:
        backfill_candles(create_exchange(exchange_id), exchange_id, symbol, timeframe, config["backfill"]["start_date"])
    holdout = config.get("validation", {}).get("holdout_start")
    df, _ = apply_holdout_guard(candles_df(exchange_id, symbol, timeframe), holdout, allow_holdout=False,
                                context_label="walk-forward job")
    if len(df) == 0:
        raise JobError(f"no {symbol} {timeframe} candles stored")
    backtest_config = config.get("backtest", {})
    funding = None
    if strategy == "funding_filtered":
        fc = config.get("funding", {})
        if fetch:
            backfill_funding(fc.get("exchange_id", "binanceusdm"), fc.get("symbol", "BTC/USDT:USDT"),
                             fc.get("start_date", config["backfill"]["start_date"]))
        funding = funding_df(fc.get("exchange_id", "binanceusdm"), fc.get("symbol", "BTC/USDT:USDT"))

    end = pd.Timestamp(holdout, tz="UTC") if holdout else df.index[-1] + pd.Timedelta(seconds=1)
    folds = make_folds(df.index[0], pd.Timestamp(params["first_test"], tz="UTC"), end, params["test_months"])
    ppy = periods_per_year(timeframe)

    curves, trades, keys = {}, {}, {}
    for label, cfg in variants(params, config.get("strategy", {})):
        result = run_strategy_backtest(df, strategy, cfg, backtest_config, funding, warmup_df=df)
        curves[label], trades[label] = result.equity_curve, result.trades
        keys[label] = (_key(params, cfg, backtest_config, holdout), cfg)
    wf = walk_forward(curves, trades, folds, timeframe)
    oos = wf["oos_returns"]

    # each variant on its own over the same out-of-sample span, for the
    # trial variance and so every variant is on record
    first_test = folds[0].test_start if folds else end
    own_sharpes = []
    for label, curve in curves.items():
        r = curve.pct_change().fillna(0.0)
        own = r[(r.index >= first_test) & (r.index < end)]
        own_sharpes.append(per_bar_sharpe(own))
        key, cfg = keys[label]
        daily = daily_returns(r)
        Experiment.objects.create(
            run_key=key, job=job, hypothesis_id=job.hypothesis_id if job else None, git_commit=code_version(),
            created_at=timezone.now(),
            kind=KIND, strategy=strategy, strategy_label=label, symbol=symbol, timeframe=timeframe,
            window_start=str(first_test), window_end=str(end), touched_holdout=False,
            config=json.loads(json.dumps(cfg, default=str)), config_hash=key[:12],
            data_version=f"{len(df)} bars, {df.index[0]}–{df.index[-1]}",
            result={"oos_if_fixed": {
                "total_return_pct": round(float(((1 + own).prod() - 1) * 100), 2),
                "sharpe_ratio": round(per_bar_sharpe(own) * math.sqrt(ppy), 2),
            }, "first_test": params["first_test"], "test_months": params["test_months"]},
            returns=[[int(ts.timestamp()), round(float(v), 6)] for ts, v in daily.items()],
        )

    family = job.hypothesis.family if job and job.hypothesis_id else ""
    n_trials, variance, source = _trials(strategy, [k for k, _ in keys.values()], own_sharpes, ppy, family)
    dsr = deflated_sharpe(oos, n_trials, variance)
    block = max(1, int(BOOTSTRAP_DAYS * ppy / 365))
    boot = block_bootstrap(oos, block, ppy)
    equity = (1 + oos).cumprod() * backtest_config.get("initial_capital", 10_000.0)
    return {
        "header": {
            "strategy": strategy, "symbol": symbol, "timeframe": timeframe,
            "folds": f"{len(folds)} anchored folds of {params['test_months']} months from {params['first_test']}",
            "holdout": f"excluded from {holdout}" if holdout else "none configured",
            "costs": f"fee={backtest_config.get('fee', 0.001)} slippage={backtest_config.get('slippage', 0.0005)}",
            "variants": len(curves), "code": code_version(),
        },
        "folds": wf["folds"],
        "oos": wf["oos"],
        "deflated_sharpe": {**dsr, "variance_source": source, "pass": dsr["probability"] >= DSR_PASS},
        "bootstrap": boot,
        "equity": _downsample(equity),
    }


def report_lines(result: dict) -> list[str]:
    """The Telegram summary of a finished walk-forward job."""
    oos, dsr, boot = result["oos"], result["deflated_sharpe"], result.get("bootstrap") or {}
    lines = [
        f"Out of sample {result['header']['folds']}: {oos['total_return_pct']:+}%, Sharpe {oos['sharpe_ratio']}, "
        f"max drawdown {oos['max_drawdown_pct']}%, {oos['positive_folds']}/{oos['folds']} folds positive.",
        f"Deflated Sharpe probability {dsr['probability']:.2f} over {dsr['n_trials']} trials "
        f"({'PASS' if dsr['pass'] else 'below 0.95'}).",
    ]
    if boot:
        lines.append(f"Bootstrap Sharpe {boot['sharpe_p05']}..{boot['sharpe_p95']} (5-95%), "
                     f"bad-case drawdown {boot['max_drawdown_p05'] * 100:.1f}%.")
    for row in result["folds"]:
        s = row["test_stats"]
        lines.append(f"Fold {row['fold']} {row['test']}: {s['total_return_pct']:+}% Sharpe {s['sharpe_ratio']}"
                     + (f" ({row['chosen']})" if result["header"]["variants"] > 1 else ""))
    return lines

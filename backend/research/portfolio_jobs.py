"""Portfolio research jobs: the rotational multi-coin backtest
(bot/backtest/portfolio.py) run on the same train/test windows as
single-coin research reports, with the holdout always excluded. Results use
the same shape as research reports (header + runs with train/test windows
and equity curves), so the research page shows them the same way."""

import json

import pandas as pd

from bot.backtest.metrics import summarize
from bot.backtest.portfolio import run_portfolio_backtest
from bot.data.exchange import create_exchange
from bot.research.backtest_runner import with_override
from bot.strategy.registry import build_strategy
from market.candles import backfill_candles, backfill_head, candles_df
from research.jobs import TEST_START, TRAIN, JobError, _downsample, _record, code_version, run_key
from trading.services.bots import trade_bot_config

STRATEGY = "donchian_ensemble"
HISTORY_START = "2018-01-01T00:00:00Z"  # a coin needs a year of history before it can be picked
BARS_PER_DAY = {"4h": 6, "1d": 1}
DEFAULT_POOL = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT", "LTC/USDT",
    "LINK/USDT", "DOT/USDT", "AVAX/USDT", "ATOM/USDT", "TRX/USDT", "BCH/USDT", "ETC/USDT", "FIL/USDT",
    "NEAR/USDT", "XLM/USDT", "UNI/USDT", "LUNC/USDT", "FTT/USDT", "EOS/USDT", "XTZ/USDT", "SHIB/USDT",
    "ALGO/USDT", "VET/USDT", "ICP/USDT",
]
MAX_POOL = 60


def validate_portfolio_params(params: dict) -> dict:
    timeframe = params.get("timeframe", "4h")
    if timeframe not in BARS_PER_DAY:
        raise JobError(f"portfolio timeframe must be one of {', '.join(BARS_PER_DAY)}")
    pool = params.get("pool") or DEFAULT_POOL
    if not isinstance(pool, list) or not all(isinstance(s, str) and "/" in s for s in pool):
        raise JobError("pool must be a list of symbols like BTC/USDT")
    if len(pool) > MAX_POOL:
        raise JobError(f"at most {MAX_POOL} coins in the pool")
    sizes = params.get("sizes") or [10]
    if not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(pool) for n in sizes):
        raise JobError(f"sizes must be whole numbers from 1 to the pool size ({len(pool)})")
    overrides = params.get("params") or {}
    for path, value in overrides.items():
        if not path.startswith(f"{STRATEGY}.") or isinstance(value, list):
            raise JobError(f"{path!r}: only single-value {STRATEGY}.* settings are allowed here")
    return {"kind": "portfolio", "strategy": STRATEGY, "timeframe": timeframe, "pool": sorted(set(pool)),
            "sizes": sorted(set(sizes)), "params": overrides}


def _portfolio_key(params: dict, size: int, strategy_config: dict, backtest_config: dict, holdout_start) -> str:
    return run_key(f"portfolio:{STRATEGY}:top{size}", ",".join(params["pool"]), params["timeframe"],
                   strategy_config, backtest_config, holdout_start)


def run_portfolio_report(params: dict, fetch: bool = True, job=None) -> dict:
    config = trade_bot_config()
    exchange_id = config["exchange"]["id"]
    timeframe = params["timeframe"]
    holdout_start = config.get("validation", {}).get("holdout_start")
    holdout_ts = pd.Timestamp(holdout_start, tz="UTC") if holdout_start else None
    backtest_config = config.get("backtest", {})

    strategy_config = with_override(config.get("strategy", {}), STRATEGY, "bars_per_day", BARS_PER_DAY[timeframe])
    for path, value in params["params"].items():
        strategy_config = with_override(strategy_config, STRATEGY, path.split(".", 1)[1], value)
    strategy = build_strategy(STRATEGY, strategy_config)

    frames, unavailable = {}, []
    exchange = create_exchange(exchange_id) if fetch else None
    for symbol in params["pool"]:
        if fetch:
            try:
                backfill_head(exchange, exchange_id, symbol, timeframe, HISTORY_START)
                backfill_candles(exchange, exchange_id, symbol, timeframe, HISTORY_START)
            except Exception as exc:  # delisted or unknown on the exchange
                unavailable.append(f"{symbol} ({str(exc)[:60]})")
                continue
        df = candles_df(exchange_id, symbol, timeframe)
        if holdout_ts is not None:
            df = df[df.index < holdout_ts]  # never the holdout, no override
        if len(df) < strategy.min_lookback:
            unavailable.append(f"{symbol} (not enough history)")
            continue
        frames[symbol] = df
    if not frames:
        raise JobError("no coin in the pool has usable history")

    weights = {sym: strategy.target_weights(df) for sym, df in frames.items()}
    index = pd.DatetimeIndex(sorted(set().union(*[df.index for df in frames.values()])))
    windows = {
        "train": index[(index >= pd.Timestamp(TRAIN[0], tz="UTC")) & (index <= pd.Timestamp(TRAIN[1], tz="UTC"))],
        "test": index[index >= pd.Timestamp(TEST_START, tz="UTC")],
    }
    initial = backtest_config.get("initial_capital", 10_000.0)
    threshold = strategy_config.get(STRATEGY, {}).get("rebalance_threshold", 0.0)

    runs = []
    for size in params["sizes"]:
        key = _portfolio_key(params, size, strategy_config, backtest_config, holdout_start)
        out = {"label": f"top-{size} rotational {STRATEGY}", "baseline": False, "windows": {}, "equity": {},
               "universe": {}}
        for name, window in windows.items():
            if len(window) == 0:
                out["windows"][name] = None
                continue
            res = run_portfolio_backtest(
                frames, weights, window, size,
                fee=backtest_config.get("fee", 0.001), slippage=backtest_config.get("slippage", 0.0005),
                initial_capital=initial, rebalance_threshold=threshold,
            )
            summary = summarize(res.trades, res.equity_curve, initial, timeframe)
            summary["avg_exposure_pct"] = round(float(res.exposure.mean() * 100), 1)
            summary = json.loads(json.dumps(summary, default=str))
            window_df = pd.DataFrame(index=window)
            _record("research_report", f"portfolio_{STRATEGY}_top{size}", "PORTFOLIO", timeframe, window_df,
                    {**strategy_config, "portfolio": {"size": size, "pool": params["pool"]}}, summary, key, job)
            out["windows"][name] = summary
            out["equity"][name] = _downsample(res.equity_curve)
            out["universe"][name] = res.universe
        runs.append(out)

    def span(ix):
        return f"{ix.min():%Y-%m-%d}..{ix.max():%Y-%m-%d} ({len(ix)} bars)" if len(ix) else "no data"

    return {
        "header": {
            "strategy": f"rotational {STRATEGY}", "symbol": f"{len(frames)} coins", "timeframe": timeframe,
            "train": span(windows["train"]), "test": span(windows["test"]),
            "holdout": f"excluded from {holdout_start}" if holdout_start else "none configured",
            "costs": f"fee={backtest_config.get('fee', 0.001)} slippage={backtest_config.get('slippage', 0.0005)}",
            "universe": "monthly, top by median 30-day dollar volume among coins with 1+ year of history",
            "unavailable": ", ".join(unavailable) or "none",
            "code": code_version(),
        },
        "runs": runs,
    }

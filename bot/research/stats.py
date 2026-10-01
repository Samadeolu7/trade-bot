"""Statistics for judging a backtest honestly: the deflated Sharpe ratio
(Bailey & Lopez de Prado, "The Deflated Sharpe Ratio", 2014), which asks
how likely the observed Sharpe is to be real given how many variants were
tried, and a circular block bootstrap for confidence intervals that keeps
the returns' autocorrelation. Sharpe ratios here are per bar unless named
annual."""

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


def per_bar_sharpe(returns: pd.Series) -> float:
    r = returns.dropna()
    if len(r) < 2 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std())


def expected_max_sharpe(n_trials: int, trial_variance: float) -> float:
    """The Sharpe (per bar) the best of `n_trials` unskilled variants would
    show by luck alone, given the variance of the trials' Sharpes."""
    if n_trials <= 1 or trial_variance <= 0:
        return 0.0
    return math.sqrt(trial_variance) * (
        (1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
        + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e))
    )


def probabilistic_sharpe(returns: pd.Series, benchmark_sharpe: float = 0.0) -> float:
    """Probability that the true per-bar Sharpe exceeds `benchmark_sharpe`,
    allowing for sample length, skew and fat tails."""
    r = returns.dropna()
    n = len(r)
    if n < 3 or r.std() == 0:
        return 0.0
    sr = per_bar_sharpe(r)
    skew = float(r.skew())
    kurt = float(r.kurt()) + 3  # pandas gives excess kurtosis
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if denom <= 0:
        return 0.0
    return _N.cdf((sr - benchmark_sharpe) * math.sqrt(n - 1) / math.sqrt(denom))


def deflated_sharpe(returns: pd.Series, n_trials: int, trial_variance: float) -> dict:
    """{probability, benchmark_sharpe}: the probabilistic Sharpe against
    the best Sharpe expected from `n_trials` tries by luck. 0.95 or more
    is the usual bar."""
    benchmark = expected_max_sharpe(n_trials, trial_variance)
    return {"probability": round(probabilistic_sharpe(returns, benchmark), 4),
            "benchmark_sharpe_per_bar": round(benchmark, 6), "n_trials": n_trials}


def max_drawdown(returns: np.ndarray) -> float:
    equity = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(equity)
    return float((equity / peak - 1).min()) if len(equity) else 0.0


def block_bootstrap(returns: pd.Series, block: int, periods_per_year: float, n: int = 2000,
                    seed: int = 7) -> dict:
    """Circular block bootstrap: resamples blocks of `block` consecutive
    bars, so streaks and volatility clusters survive. Returns 5th/50th/95th
    percentiles of annual Sharpe and of max drawdown (as a negative
    fraction; p05 is the bad tail)."""
    r = returns.dropna().to_numpy(dtype=float)
    if len(r) < 2 * block or block < 1:
        return {}
    rng = np.random.default_rng(seed)
    blocks = math.ceil(len(r) / block)
    sharpes, drawdowns = np.empty(n), np.empty(n)
    offsets = np.arange(block)
    for i in range(n):
        starts = rng.integers(0, len(r), blocks)
        sample = r[(starts[:, None] + offsets) % len(r)].ravel()[: len(r)]
        sd = sample.std(ddof=1)
        sharpes[i] = sample.mean() / sd * math.sqrt(periods_per_year) if sd > 0 else 0.0
        drawdowns[i] = max_drawdown(sample)
    pct = lambda a, q: round(float(np.percentile(a, q)), 4)  # noqa: E731
    return {
        "block_bars": block, "samples": n,
        "sharpe_p05": pct(sharpes, 5), "sharpe_p50": pct(sharpes, 50), "sharpe_p95": pct(sharpes, 95),
        "max_drawdown_p05": pct(drawdowns, 5), "max_drawdown_p50": pct(drawdowns, 50),
    }

import numpy as np
import pandas as pd

from bot.research.stats import block_bootstrap, deflated_sharpe, expected_max_sharpe, probabilistic_sharpe
from bot.research.walk_forward import daily_returns, make_folds, walk_forward

T = pd.Timestamp


def test_folds_step_forward_and_stop_at_the_holdout():
    folds = make_folds(T("2020-01-01", tz="UTC"), T("2022-01-01", tz="UTC"), T("2026-03-01", tz="UTC"), 6)
    assert len(folds) == 9
    assert folds[0].test_start == T("2022-01-01", tz="UTC") and folds[0].test_end == T("2022-07-01", tz="UTC")
    assert folds[-1].test_start == T("2026-01-01", tz="UTC")
    assert folds[-1].test_end == T("2026-03-01", tz="UTC")  # clipped: never into the holdout
    assert all(f.train_start == T("2020-01-01", tz="UTC") for f in folds)


def _curve(daily_returns_list, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(daily_returns_list), freq="D", tz="UTC")
    return pd.Series(10_000 * np.cumprod(1 + np.array(daily_returns_list)), index=idx)


def test_each_fold_uses_the_variant_that_was_best_before_it():
    n = 4 * 365
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.01, n)
    # A is great early then bad; B is the reverse
    a = np.where(np.arange(n) < 2 * 365, 0.002, -0.002) + noise
    b = np.where(np.arange(n) < 2 * 365, -0.002, 0.002) + noise
    curves = {"A": _curve(a), "B": _curve(b)}
    folds = make_folds(T("2020-01-01", tz="UTC"), T("2022-01-01", tz="UTC"), T("2023-12-31", tz="UTC"), 6)
    out = walk_forward(curves, {}, folds, "1d")
    # A looked best on all the data before 2022, so it's chosen first and loses out of sample
    assert out["folds"][0]["chosen"] == "A"
    assert out["folds"][0]["test_stats"]["total_return_pct"] < 0
    assert out["oos"]["folds"] == len(folds) and len(out["oos_returns"]) > 600


def test_single_variant_folds_match_its_own_returns():
    curve = _curve([0.001] * 1000)
    folds = make_folds(T("2020-01-01", tz="UTC"), T("2021-01-01", tz="UTC"), T("2022-09-01", tz="UTC"), 6)
    out = walk_forward({"only": curve}, {}, folds, "1d")
    assert all(row["chosen"] == "only" for row in out["folds"])
    assert out["oos"]["positive_folds"] == out["oos"]["folds"] and out["oos"]["max_drawdown_pct"] == 0.0


def test_deflated_sharpe_gets_stricter_with_more_trials():
    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0.0008, 0.01, 1500))
    assert probabilistic_sharpe(r) > 0.95
    assert expected_max_sharpe(1, 0.001) == 0.0
    one = deflated_sharpe(r, 1, 0.0004)["probability"]
    many = deflated_sharpe(r, 200, 0.0004)["probability"]
    assert one > many


def test_block_bootstrap_brackets_the_sample_sharpe():
    rng = np.random.default_rng(5)
    r = pd.Series(rng.normal(0.001, 0.01, 1000))
    out = block_bootstrap(r, block=20, periods_per_year=365, n=500)
    sample = r.mean() / r.std() * np.sqrt(365)
    assert out["sharpe_p05"] < sample < out["sharpe_p95"]
    assert out["max_drawdown_p05"] <= out["max_drawdown_p50"] < 0
    assert block_bootstrap(r.iloc[:30], block=20, periods_per_year=365) == {}  # too short


def test_daily_returns_compound_bars():
    idx = pd.date_range("2024-01-01", periods=12, freq="4h", tz="UTC")
    out = daily_returns(pd.Series(0.01, index=idx))
    assert len(out) == 2 and abs(out.iloc[0] - (1.01**6 - 1)) < 1e-12

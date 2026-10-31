"""Hypothesis #7 (notes/research_log.md): the intraday ideas and the
existing strategies when the owner is available 24/7, flat nightly vs held
overnight with swap. Shared by scripts/intraday_research.py (prints a
table) and the server's intraday research job (stores the rows)."""

import copy
import time
import types

import numpy as np
import pandas as pd

from bot.research import intraday as I
from bot.research.stats import deflated_sharpe, per_bar_sharpe
from bot.research.walk_forward import make_folds, walk_forward
from bot.strategy.registry import build_strategy

HOLDOUT = pd.Timestamp("2026-03-01", tz="UTC")
JUDGE, ROBUST = 0.0002, 0.0005  # round-trip cost: judged at, and must still profit at
DODGE = 3 * JUDGE  # assumed round trip at the rollover (spreads widen then)
TRIALS = 130  # every intraday variant pre-registered so far (#2-#7)
VARIANTS = {"A": 16, "B": 72}

SIGNAL = [("donchian", "donchian", {}), ("regime_adx", "regime_switched", {}),
          ("regime_natr", "regime_switched", {"regime.type": "natr"}), ("ema_cross", "ema_cross", {}),
          ("rsi_bb", "rsi_bb", {}), ("market_structure", "market_structure", {}),
          ("vol_expansion", "vol_expansion", {}), ("crt", "crt", {}), ("failed_breakout", "failed_breakout", {})]
EXPOSURE = [("ensemble", "donchian_ensemble"), ("ensemble_fb_filter", "donchian_ensemble_fb_filter"),
            ("ensemble_fb_exit", "donchian_ensemble_fb_exit")]


def setup(df: pd.DataFrame, first_test: str = "2022-01-01") -> tuple[pd.DatetimeIndex, list]:
    assert df.index.max() < HOLDOUT, "holdout data must never be used here"
    days = pd.date_range(df.index.min().normalize(), df.index.max().normalize(), freq="D")
    return days, make_folds(days[0], pd.Timestamp(first_test, tz="UTC"), HOLDOUT, 6)


def _row(label, trades_or_entries, gross, series) -> dict:
    return {"label": label, "n": len(trades_or_entries), "gross": gross,
            "entries": trades_or_entries if not trades_or_entries or isinstance(trades_or_entries[0], pd.Timestamp)
            else [t.entry_time for t in trades_or_entries], "series": series}


def batch_a(df: pd.DataFrame, days, log=print) -> list[dict]:
    grids = {
        "range_breakout": {f"range{r} stop-{s}": (I.orb, dict(range_minutes=r, stop_at=s))
                           for r in (30, 60) for s in ("opposite", "mid")},
        "vol_breakout": {f"k{k} {'both' if b else 'long'}": (I.vol_breakout, dict(k=k, both=b))
                         for k in (0.3, 0.5, 0.7) for b in (False, True)},
        "momentum": {f"from{h:02d}": (I.momentum, dict(formation_start_hour=h)) for h in (7, 0)},
        "fade": {f"k{k} {'both' if b else 'long'}": (I.fade, dict(k=k, both=b))
                 for k in (2.5, 3.0) for b in (False, True)},
    }
    rows = []
    for name, grid in grids.items():
        for label, (fn, kw) in grid.items():
            trades = fn(df, session="always", **kw)
            gross = float(np.mean([t.gross() for t in trades])) if trades else 0.0
            rows.append(_row(f"{name} {label} (24/7)", trades, gross,
                             {c: I.daily_returns(trades, days, c) for c in (JUDGE, ROBUST)}))
            log(f"  {rows[-1]['label']}: {len(trades)} trades")
    return rows


def batch_b(df15: pd.DataFrame, base_config: dict, days, log=print) -> list[dict]:
    def cfg(over):
        c = copy.deepcopy(base_config)
        for k, v in over.items():
            sec, key = k.split(".", 1)
            c.setdefault(sec, {})[key] = v
        return c

    df1h = df15.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                    "volume": "sum"}).dropna()
    rows = []
    for tf, df, bpd in (("1h", df1h, 24), ("15m", df15, 96)):
        for label, name, over in SIGNAL:
            strat = build_strategy(name, cfg(over))
            for session in ("always", "hold"):
                t0 = time.time()
                trades = I.session_signal_trades(df, strat, session=session)
                gross = float(np.mean([t.gross() for t in trades])) if trades else 0.0
                swap = session == "hold"
                for tname, dodge in ({"hold": None, "hold+dodge": DODGE} if swap else {"always": None}).items():
                    rows.append(_row(f"{label} {tf} {tname}", trades, gross,
                                     {c: I.daily_returns(trades, days, c, swap=swap, dodge_cost=dodge)
                                      for c in (JUDGE, ROBUST)}))
                log(f"  {label} {tf} {session}: {len(trades)} trades ({time.time() - t0:.0f}s)")
        for label, name in EXPOSURE:
            strat = build_strategy(name, cfg({"donchian_ensemble.bars_per_day": bpd}))
            for tname, session, dodge in (("always", "always", None), ("hold", "hold", None),
                                          ("hold+dodge", "hold", DODGE)):
                series, entries = {}, []
                for c in (JUDGE, ROBUST):
                    d, entries = I.session_exposure_returns(df, strat, c, session=session, dodge_cost=dodge)
                    series[c] = d.reindex(days, fill_value=0.0)
                rows.append(_row(f"{label} {tf} {tname}", entries, float("nan"), series))
            log(f"  {label} {tf}: done")
    return rows


def score(rows: list[dict], folds) -> list[dict]:
    """Walk-forward results and the pre-registered verdict for each row."""
    first = folds[0].test_start
    var = float(np.var([per_bar_sharpe(r["series"][JUDGE][r["series"][JUDGE].index >= first]) for r in rows], ddof=1))
    out = []
    for r in rows:
        res = {}
        for cost, series in r["series"].items():
            res[cost] = walk_forward({"x": (1 + series).cumprod()},
                                     {"x": [types.SimpleNamespace(entry_time=e) for e in r["entries"]]}, folds, "1d")
        o, rob = res[JUDGE]["oos"], res[ROBUST]["oos"]
        n_oos = sum(f["test_stats"].get("trades", 0) for f in res[JUDGE]["folds"])
        dsr = deflated_sharpe(res[JUDGE]["oos_returns"], TRIALS, var)["probability"]
        passed = (o["sharpe_ratio"] >= 0.8 and o["positive_folds"] >= 0.6 * o["folds"] and dsr >= 0.95
                  and n_oos >= 100 and rob["sharpe_ratio"] > 0)
        out.append({
            "label": r["label"], "trades": r["n"],
            "gross_pct": None if r["gross"] != r["gross"] else round(r["gross"] * 100, 4),
            "sharpe": o["sharpe_ratio"], "sharpe_robust": rob["sharpe_ratio"],
            "return_pct": o["total_return_pct"], "max_drawdown_pct": o["max_drawdown_pct"],
            "positive_folds": o["positive_folds"], "folds": o["folds"], "oos_trades": n_oos,
            "deflated_sharpe": round(dsr, 4), "pass": bool(passed),
        })
    return out


def table(title: str, scored: list[dict]) -> str:
    lines = [f"=== {title}",
             f"{'variant':38s} {'trades':>6s} {'gross':>8s} | Sharpe {JUDGE*100:.2f}% / {ROBUST*100:.2f}% | "
             "OOS ret   max DD  folds+ oosTrades  DSR   verdict"]
    for s in scored:
        gross = "n/a" if s["gross_pct"] is None else f"{s['gross_pct']:+.3f}%"
        lines.append(f"{s['label']:38s} {s['trades']:6d} {gross:>8s} | {s['sharpe']:6.2f} / {s['sharpe_robust']:6.2f}    | "
                     f"{s['return_pct']:+7.1f}% {s['max_drawdown_pct']:6.1f}%  {s['positive_folds']}/{s['folds']}  "
                     f"{s['oos_trades']:7d}  {s['deflated_sharpe']:.2f}  {'PASS' if s['pass'] else 'fail'}")
    return "\n".join(lines)


TITLES = {"A": "Batch A: #2-#5 with 24/7 availability (flat by 23:45 UTC)",
          "B": "Batch B: existing strategies, 24/7: flat nightly vs held overnight (swap) vs held + Friday dodge"}

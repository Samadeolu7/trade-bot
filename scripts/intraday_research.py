"""Runs the pre-registered intraday hypotheses (#7 by default; see
notes/research_log.md) on BTC/USDT 15-minute candles and prints the
walk-forward results.

    python scripts/intraday_research.py                 # hypothesis #7 (24/7 + overnight holding)
    python scripts/intraday_research.py --batch A       # only #2-#5 under the 24/7 session
    python scripts/intraday_research.py --batch B       # only existing strategies, 3 treatments

The 15m data (2020-01-01 to 2026-02-28, never the holdout) is downloaded
from Binance's public data API on first run and cached in data/btc15m.pkl.
Batch B takes 20-40 minutes."""

import argparse
import copy
import json
import sys
import time
import types
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.research import intraday as I  # noqa: E402
from bot.research.stats import deflated_sharpe, per_bar_sharpe  # noqa: E402
from bot.research.walk_forward import make_folds, walk_forward  # noqa: E402
from bot.strategy.registry import build_strategy  # noqa: E402

DATA = ROOT / "data" / "btc15m.pkl"
HOLDOUT = pd.Timestamp("2026-03-01", tz="UTC")
JUDGE, ROBUST = 0.0002, 0.0005  # round-trip cost: judged at, and must still profit at
DODGE = 3 * JUDGE  # assumed round trip at the rollover (spreads widen then)
TRIALS = 130  # every intraday variant pre-registered so far (#2-#7)


def load() -> pd.DataFrame:
    if DATA.exists():
        df = pd.read_pickle(DATA)
    else:
        print("downloading BTC/USDT 15m candles (one-off, a few minutes)...", flush=True)
        start = int(pd.Timestamp("2020-01-01", tz="UTC").timestamp() * 1000)
        end = int(HOLDOUT.timestamp() * 1000)
        rows, since = [], start
        while since < end:
            url = ("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=15m"
                   f"&startTime={since}&endTime={end - 1}&limit=1000")
            for attempt in range(5):
                try:
                    batch = json.load(urllib.request.urlopen(url, timeout=30))
                    break
                except Exception:
                    time.sleep(2 ** attempt)
            else:
                sys.exit(f"download failed at {pd.Timestamp(since, unit='ms')}")
            if not batch:
                break
            rows += [r[:6] for r in batch if r[0] < end]
            since = batch[-1][0] + 15 * 60 * 1000
        df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"]).astype(float)
        df.index = pd.to_datetime(df.pop("t").astype("int64"), unit="ms", utc=True)
        df = df[~df.index.duplicated()]
        DATA.parent.mkdir(exist_ok=True)
        df.to_pickle(DATA)
    assert df.index.max() < HOLDOUT, "holdout data must never be used here"
    return df


def evaluate(series_by_cost: dict, entries: list, folds) -> dict:
    out = {}
    for cost, series in series_by_cost.items():
        wf = walk_forward({"x": (1 + series).cumprod()},
                          {"x": [types.SimpleNamespace(entry_time=e) for e in entries]}, folds, "1d")
        out[cost] = wf
    return out


def report(rows: list, folds, title: str) -> None:
    first = folds[0].test_start
    var = float(np.var([per_bar_sharpe(r["series"][JUDGE][r["series"][JUDGE].index >= first]) for r in rows], ddof=1))
    print(f"\n=== {title}")
    print(f"{'variant':38s} {'trades':>6s} {'gross':>8s} | Sharpe {JUDGE*100:.2f}% / {ROBUST*100:.2f}% | "
          f"OOS ret   max DD  folds+ oosTrades  DSR   verdict")
    for r in rows:
        res = evaluate(r["series"], r["entries"], folds)
        o, rob = res[JUDGE]["oos"], res[ROBUST]["oos"]
        n_oos = sum(f["test_stats"].get("trades", 0) for f in res[JUDGE]["folds"])
        dsr = deflated_sharpe(res[JUDGE]["oos_returns"], TRIALS, var)["probability"]
        passed = (o["sharpe_ratio"] >= 0.8 and o["positive_folds"] >= 0.6 * o["folds"] and dsr >= 0.95
                  and n_oos >= 100 and rob["sharpe_ratio"] > 0)
        gross = f"{r['gross'] * 100:+.3f}%" if r["gross"] == r["gross"] else "n/a"
        print(f"{r['label']:38s} {r['n']:6d} {gross:>8s} | {o['sharpe_ratio']:6.2f} / {rob['sharpe_ratio']:6.2f}    | "
              f"{o['total_return_pct']:+7.1f}% {o['max_drawdown_pct']:6.1f}%  {o['positive_folds']}/{o['folds']}  "
              f"{n_oos:7d}  {dsr:.2f}  {'PASS' if passed else 'fail'}", flush=True)


def batch_a(df, days, folds) -> None:
    grids = {
        "range_breakout": {f"range{r} stop-{s}": (I.orb, dict(range_minutes=r, stop_at=s))
                           for r in (30, 60) for s in ("opposite", "mid")},
        "vol_breakout": {f"k{k} {'both' if b else 'long'}": (I.vol_breakout, dict(k=k, both=b))
                         for k in (0.3, 0.5, 0.7) for b in (False, True)},
        "momentum": {f"from{h:02d}": (I.momentum, dict(formation_start_hour=h)) for h in (7, 0)},
        "fade": {f"k{k} {'both' if b else 'long'}": (I.fade, dict(k=k, both=b)) for k in (2.5, 3.0) for b in (False, True)},
    }
    rows = []
    for name, grid in grids.items():
        for label, (fn, kw) in grid.items():
            trades = fn(df, session="always", **kw)
            g = np.mean([t.gross() for t in trades]) if trades else 0.0
            rows.append({"label": f"{name} {label} (24/7)", "n": len(trades), "gross": g,
                         "entries": [t.entry_time for t in trades],
                         "series": {c: I.daily_returns(trades, days, c) for c in (JUDGE, ROBUST)}})
            print(f"  {rows[-1]['label']}: {len(trades)} trades", flush=True)
    report(rows, folds, "Batch A: #2-#5 with 24/7 availability (flat by 23:45 UTC)")


def batch_b(df15, days, folds) -> None:
    base = yaml.safe_load(open(ROOT / "config" / "config.yaml", encoding="utf-8"))["strategy"]

    def cfg(over):
        c = copy.deepcopy(base)
        for k, v in over.items():
            sec, key = k.split(".", 1)
            c.setdefault(sec, {})[key] = v
        return c

    signal = [("donchian", "donchian", {}), ("regime_adx", "regime_switched", {}),
              ("regime_natr", "regime_switched", {"regime.type": "natr"}), ("ema_cross", "ema_cross", {}),
              ("rsi_bb", "rsi_bb", {}), ("market_structure", "market_structure", {}),
              ("vol_expansion", "vol_expansion", {}), ("crt", "crt", {}), ("failed_breakout", "failed_breakout", {})]
    exposure = [("ensemble", "donchian_ensemble"), ("ensemble_fb_filter", "donchian_ensemble_fb_filter"),
                ("ensemble_fb_exit", "donchian_ensemble_fb_exit")]
    df1h = df15.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                    "volume": "sum"}).dropna()
    rows = []
    for tf, df, bpd in (("1h", df1h, 24), ("15m", df15, 96)):
        for label, name, over in signal:
            strat = build_strategy(name, cfg(over))
            for session in ("always", "hold"):
                t0 = time.time()
                trades = I.session_signal_trades(df, strat, session=session)
                g = np.mean([t.gross() for t in trades]) if trades else 0.0
                entries = [t.entry_time for t in trades]
                swap = session == "hold"
                treatments = {session: None} if not swap else {"hold": None, "hold+dodge": DODGE}
                for tname, dodge in treatments.items():
                    rows.append({"label": f"{label} {tf} {tname}", "n": len(trades), "gross": g, "entries": entries,
                                 "series": {c: I.daily_returns(trades, days, c, swap=swap, dodge_cost=dodge)
                                            for c in (JUDGE, ROBUST)}})
                print(f"  {label} {tf} {session}: {len(trades)} trades ({time.time() - t0:.0f}s)", flush=True)
        for label, name in exposure:
            strat = build_strategy(name, cfg({"donchian_ensemble.bars_per_day": bpd}))
            for tname, session, dodge in (("always", "always", None), ("hold", "hold", None),
                                          ("hold+dodge", "hold", DODGE)):
                series, entries = {}, []
                for c in (JUDGE, ROBUST):
                    d, entries = I.session_exposure_returns(df, strat, c, session=session, dodge_cost=dodge)
                    series[c] = d.reindex(days, fill_value=0.0)
                rows.append({"label": f"{label} {tf} {tname}", "n": len(entries), "gross": float("nan"),
                             "entries": entries, "series": series})
            print(f"  {label} {tf}: done", flush=True)
    report(rows, folds, "Batch B: existing strategies, 24/7: flat nightly vs held overnight (swap) vs held + Friday dodge")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", choices=["A", "B", "all"], default="all")
    args = ap.parse_args()
    df = load()
    days = pd.date_range(df.index.min().normalize(), df.index.max().normalize(), freq="D")
    folds = make_folds(days[0], pd.Timestamp("2022-01-01", tz="UTC"), HOLDOUT, 6)
    print(f"{len(df)} bars, {df.index.min():%Y-%m-%d} to {df.index.max():%Y-%m-%d}; {len(folds)} folds from 2022-01-01",
          flush=True)
    if args.batch in ("A", "all"):
        batch_a(df, days, folds)
    if args.batch in ("B", "all"):
        batch_b(df, days, folds)


if __name__ == "__main__":
    main()

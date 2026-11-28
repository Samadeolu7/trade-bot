"""Hypothesis #9 (notes/research_log.md): does the US-open range breakout
(hypothesis #7's best result, now in a forward paper test) do worse on
scheduled US macro event days, so that skipping them helps?

The range is set 13:30-14:30 UTC and trades run to 23:45 UTC. CPI and the
jobs report come out at 12:30/13:30 UTC, just before or at the range; an
FOMC statement at 18:00/19:00 UTC lands mid-trade. Event dates are on a
published schedule (bot/data/macro_calendar.py), so skipping a day needs
no look-ahead and has no data lag.

Variants: the breakout as #7 ran it, and the same with each kind of event
day removed (and all kinds together when there's more than one). Removing
a day removes that day's one trade, so a skip variant is exactly the
baseline minus event-day trades. The direct evidence is therefore the
event-day trades themselves: their mean net return and a bootstrap range."""

import numpy as np
import pandas as pd

from bot.data import macro_calendar as M
from bot.research import intraday as I
from bot.research import intraday_report as R

ORB = dict(range_minutes=60, stop_at="mid", session="always")
FIRST_TEST = pd.Timestamp("2022-01-01", tz="UTC")
BOOTSTRAP = 5000


def variants(kinds) -> list[tuple[str, tuple]]:
    out = [("orb baseline", ())]
    out += [(f"orb skip {k} days", (k,)) for k in kinds]
    if len(kinds) > 1:
        out.append(("orb skip all event days", tuple(kinds)))
    return out


def _day_of(t: pd.Timestamp) -> pd.Timestamp:
    return t.normalize()


def event_day_stats(trades: list, days: set, cost: float, seed: int = 0) -> dict:
    """Out-of-sample trades entered on event days: count, mean net return
    per trade, its bootstrap 5-95% range, and the same for the other days."""
    def net(ts):
        return np.array([t.gross() - cost for t in ts])

    oos = [t for t in trades if t.entry_time >= FIRST_TEST]
    on = net([t for t in oos if _day_of(t.entry_time) in days])
    off = net([t for t in oos if _day_of(t.entry_time) not in days])
    rng = np.random.default_rng(seed)
    if len(on) >= 2:
        means = rng.choice(on, size=(BOOTSTRAP, len(on))).mean(axis=1)
        lo, hi = (float(x) for x in np.percentile(means, [5, 95]))
    else:
        lo = hi = None
    pct = lambda x: None if x is None else round(x * 100, 4)  # noqa: E731
    return {
        "event_day_trades": int(len(on)), "event_day_mean_pct": pct(float(on.mean())) if len(on) else None,
        "event_day_mean_p05_pct": pct(lo), "event_day_mean_p95_pct": pct(hi),
        "other_day_trades": int(len(off)), "other_day_mean_pct": pct(float(off.mean())) if len(off) else None,
    }


def run(df: pd.DataFrame, kinds, fred_dates=None, log=print) -> tuple[list[dict], dict]:
    """(scored rows, event-day evidence per kind) on 15m candles."""
    days, folds = R.setup(df)
    ev = M.events(kinds, fred_dates)
    by_kind = {k: M.event_days(ev[ev["kind"] == k]) for k in kinds}
    trades = I.orb(df, **ORB)
    log(f"  orb: {len(trades)} trades")
    rows = []
    for label, skip in variants(kinds):
        skipped = set().union(*(by_kind[k] for k in skip)) if skip else set()
        kept = [t for t in trades if _day_of(t.entry_time) not in skipped]
        gross = float(np.mean([t.gross() for t in kept])) if kept else 0.0
        rows.append(R._row(label, kept, gross, {c: I.daily_returns(kept, days, c) for c in (R.JUDGE, R.ROBUST)}))
        log(f"  {label}: {len(kept)} trades")
    evidence = {k: {**event_day_stats(trades, by_kind[k], R.JUDGE), "event_days": len(by_kind[k])} for k in kinds}
    if len(kinds) > 1:
        all_days = set().union(*by_kind.values())
        evidence["all"] = {**event_day_stats(trades, all_days, R.JUDGE), "event_days": len(all_days)}
    return R.score(rows, folds), evidence

"""When outside data (funding rates now, fundamentals later) would really
have reached the system, so a backtest only uses a value once the live
engine could have had it.

Every value has an `event_time`: the time it is about, such as a funding
print at 08:00 or a CPI release at 12:30. The live system can only use
it later:

    available_at = event_time
                 + source delay   (until the provider shows it, our fetch
                                   wait and any retries)
                 + outage delay   (on some values: the source or our fetcher
                                   was down when it came out)
                 + extra delay    (a deliberate "what if it were a day late"
                                   test)

and a bot only decides at a bar close, so a value counts for the first bar
whose close is at or after `available_at`. With the live engine's
semantics as of 2026-11 (FundingFilteredStrategy.generate_signal looks the
rate up at the bar's open time) a value counts for the first bar whose
*open* is at or after it; `use_at="bar_open"` reproduces that.

A LagModel is only used in backtests. Live, the lag is whatever it really
is; the live fetchers record it (backend `market.FetchAttempt` and
`FundingRate.fetched_at`) so the levels below can come from measurements
instead of guesses."""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

USE_AT = ("bar_close", "bar_open")


@dataclass(frozen=True)
class LagModel:
    name: str = "zero"
    source_delay_s: float = 0.0
    # fraction of values that land during an outage, each then `outage_s` later
    miss_rate: float = 0.0
    outage_s: float = 0.0
    extra_delay_s: float = 0.0
    use_at: str = "bar_close"
    seed: int = 0
    # where source_delay_s came from: "measured" (live fetch logs), "assumed"
    # (config defaults, before enough live samples exist) or "none"
    basis: str = "none"

    def __post_init__(self):
        if self.use_at not in USE_AT:
            raise ValueError(f"use_at must be one of {USE_AT}, got {self.use_at!r}")
        if not 0.0 <= self.miss_rate <= 1.0:
            raise ValueError(f"miss_rate is a fraction from 0 to 1, got {self.miss_rate!r}")
        for field in ("source_delay_s", "outage_s", "extra_delay_s"):
            if getattr(self, field) < 0:
                raise ValueError(f"{field} can't be negative")

    def delays(self, n: int) -> np.ndarray:
        """Seconds from event_time to available_at for n values in time
        order. Outages are drawn with a fixed seed, so the same model gives
        the same backtest every time."""
        base = np.full(n, self.source_delay_s + self.extra_delay_s, dtype=float)
        if self.miss_rate > 0 and self.outage_s > 0 and n:
            hit = np.random.default_rng(self.seed).random(n) < self.miss_rate
            base = base + hit * self.outage_s
        return base

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LagModel":
        return cls(**data)

    def describe(self) -> str:
        parts = [f"{_fmt_seconds(self.source_delay_s)} delay ({self.basis})"]
        if self.miss_rate and self.outage_s:
            parts.append(f"{self.miss_rate:.0%} of values {_fmt_seconds(self.outage_s)} late (outage)")
        if self.extra_delay_s:
            parts.append(f"+{_fmt_seconds(self.extra_delay_s)} delay test")
        parts.append(f"used from the next {self.use_at.replace('_', ' ')}")
        return ", ".join(parts)


def _fmt_seconds(s: float) -> str:
    if s >= 86400 and s % 3600 == 0:
        return f"{s / 86400:g}d"
    if s >= 3600:
        return f"{s / 3600:.3g}h"
    if s >= 60:
        return f"{s / 60:.3g}m"
    return f"{s:.3g}s"


def available_at(event_times: pd.DatetimeIndex, model: LagModel) -> pd.DatetimeIndex:
    """event_times must be sorted; the result keeps their order."""
    delays = pd.to_timedelta(model.delays(len(event_times)), unit="s")
    return pd.DatetimeIndex(event_times + delays)


def bar_length(index: pd.DatetimeIndex) -> pd.Timedelta:
    """The candle length of a bar series (its most common spacing)."""
    if len(index) < 2:
        raise ValueError("need at least two bars to tell their length")
    return pd.Series(index[1:] - index[:-1]).mode().iloc[0]


def asof_values(values: pd.Series, bars: pd.DatetimeIndex, model: LagModel,
                bar: pd.Timedelta | None = None) -> pd.Series:
    """For each bar (indexed by open time), the newest value the system
    would have had at its decision time under `model`. `values` is indexed
    by event_time. Bars before the first available value get NaN.

    A delay can reorder values (an outage-delayed print can arrive after a
    later one): then the newest *event* already available wins, as a live
    fetcher storing both would show."""
    if len(values) == 0:
        return pd.Series(np.nan, index=bars, dtype=float)
    values = values.sort_index()
    decision = bars + (bar if bar is not None else bar_length(bars)) if model.use_at == "bar_close" else bars
    avail = available_at(pd.DatetimeIndex(values.index), model)
    # newest event available by each decision time: walk events in arrival
    # order and keep a running max of event position
    order = np.argsort(avail.asi8, kind="stable")
    arrival = avail.asi8[order]
    newest_pos = np.maximum.accumulate(order)
    idx = np.searchsorted(arrival, decision.asi8, side="right") - 1
    out = np.full(len(bars), np.nan)
    ok = idx >= 0
    out[ok] = values.to_numpy(dtype=float)[newest_pos[idx[ok]]]
    return pd.Series(out, index=bars)


# --- lag levels for research sweeps --------------------------------------------

@dataclass(frozen=True)
class LatencyStats:
    """What the live fetch logs say. `samples` are seconds from a value's
    event_time to when we stored it; attempts/failures count fetch calls."""
    samples: int
    median_s: float | None
    p95_s: float | None
    max_s: float | None
    attempts: int = 0
    failures: int = 0
    longest_outage_s: float = 0.0

    @property
    def failure_rate(self) -> float:
        return self.failures / self.attempts if self.attempts else 0.0


def latency_stats(latencies_s, attempts: int = 0, failures: int = 0,
                  longest_outage_s: float = 0.0) -> LatencyStats:
    arr = np.asarray(list(latencies_s), dtype=float)
    if len(arr) == 0:
        return LatencyStats(0, None, None, None, attempts, failures, longest_outage_s)
    return LatencyStats(len(arr), float(np.median(arr)), float(np.percentile(arr, 95)), float(arr.max()),
                        attempts, failures, longest_outage_s)


LAG_LEVELS = ("zero", "typical", "p95", "double_p95", "p95_outages", "plus_1d")

DEFAULT_ASSUMPTIONS = {
    # used until enough live samples exist; deliberately on the slow side
    "typical_s": 120.0,
    "p95_s": 900.0,
    "min_samples": 30,
    "outage_rate": 0.05,
    "outage_s": 6 * 3600.0,
}


def lag_levels(stats: LatencyStats | None, assumptions: dict | None = None,
               use_at: str = "bar_close") -> dict[str, LagModel]:
    """The lag levels a research sweep runs at:

    zero         the theoretical best (data the instant it exists)
    typical      the median delay
    p95          the delay 95% of values beat; the pass bar is set here
    double_p95   twice that
    p95_outages  p95, plus some values arriving an outage late
    plus_1d      p95 plus a day: if the edge survives this, it isn't a
                 timing effect

    Delays come from `stats` once it has `min_samples` live samples,
    otherwise from the assumptions (config.yaml `data_lag`)."""
    a = {**DEFAULT_ASSUMPTIONS, **(assumptions or {})}
    measured = stats is not None and stats.samples >= a["min_samples"]
    if measured:
        typical, p95, basis = stats.median_s, stats.p95_s, "measured"
        outage_rate = max(stats.failure_rate, a["outage_rate"])
        outage_s = max(stats.longest_outage_s, a["outage_s"])
    else:
        typical, p95, basis = float(a["typical_s"]), float(a["p95_s"]), "assumed"
        outage_rate, outage_s = float(a["outage_rate"]), float(a["outage_s"])
    return {
        "zero": LagModel("zero", 0.0, use_at=use_at, basis="none"),
        "typical": LagModel("typical", typical, use_at=use_at, basis=basis),
        "p95": LagModel("p95", p95, use_at=use_at, basis=basis),
        "double_p95": LagModel("double_p95", 2 * p95, use_at=use_at, basis=basis),
        "p95_outages": LagModel("p95_outages", p95, miss_rate=outage_rate, outage_s=outage_s,
                                use_at=use_at, basis=basis),
        "plus_1d": LagModel("plus_1d", p95, extra_delay_s=86400.0, use_at=use_at, basis=basis),
    }

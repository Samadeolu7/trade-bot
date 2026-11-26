"""The lag the live system really has getting outside data, from what the
engine logged: each funding print's `fetched_at` against its
`funding_time`, and every fetch call in FetchAttempt. Research lag sweeps
(research/lag.py) use these numbers once there are enough of them."""

from datetime import timedelta

from bot.data.availability import LatencyStats, latency_stats
from market.candles import FUNDING_SOURCE
from market.models import FetchAttempt, FundingRate

# a print stored more than this long after its time came from a history
# backfill (first start, a new symbol), not from the live engine keeping up
LIVE_WINDOW = timedelta(days=1)


def funding_latency(exchange_id: str, symbol: str, since=None) -> LatencyStats:
    rows = FundingRate.objects.filter(exchange=exchange_id, symbol=symbol, fetched_at__isnull=False)
    if since is not None:
        rows = rows.filter(funding_time__gte=since)
    samples = []
    for event, fetched in rows.values_list("funding_time", "fetched_at"):
        lag = fetched - event
        if timedelta(0) <= lag <= LIVE_WINDOW:
            samples.append(lag.total_seconds())
    attempts = FetchAttempt.objects.filter(source=FUNDING_SOURCE)
    if since is not None:
        attempts = attempts.filter(started_at__gte=since)
    total = attempts.count()
    failures = attempts.filter(ok=False).count()
    return latency_stats(samples, attempts=total, failures=failures,
                         longest_outage_s=longest_outage(FUNDING_SOURCE, since))


def longest_outage(source: str, since=None) -> float:
    """Seconds from the first failed fetch of the longest failure streak to
    the next successful one (or the last failure, if none followed)."""
    attempts = FetchAttempt.objects.filter(source=source)
    if since is not None:
        attempts = attempts.filter(started_at__gte=since)
    longest, streak_start = 0.0, None
    last_failure = None
    for started, ok in attempts.order_by("started_at").values_list("started_at", "ok"):
        if not ok:
            streak_start = streak_start or started
            last_failure = started
        elif streak_start is not None:
            longest = max(longest, (started - streak_start).total_seconds())
            streak_start = None
    if streak_start is not None and last_failure is not None:
        longest = max(longest, (last_failure - streak_start).total_seconds())
    return longest


def stats_dict(stats: LatencyStats) -> dict:
    return {
        "samples": stats.samples, "median_s": stats.median_s, "p95_s": stats.p95_s, "max_s": stats.max_s,
        "fetch_attempts": stats.attempts, "fetch_failures": stats.failures,
        "failure_rate": round(stats.failure_rate, 4), "longest_outage_s": stats.longest_outage_s,
    }

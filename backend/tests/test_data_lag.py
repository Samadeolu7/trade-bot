import json
from datetime import datetime, timedelta, timezone

import pytest
from django.test import Client

from market.models import FetchAttempt, FundingRate
from research.jobs import JobError, run_job, validate_params
from research.keys import create_key
from research.models import Experiment, Hypothesis, ResearchJob
from tests.conftest import make_candles

EXCHANGE, PERP = "binanceusdm", "BTC/USDT:USDT"
T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def funding_history(start=datetime(2023, 1, 1, tzinfo=timezone.utc), days=1300):
    """8-hourly prints that swing between crowded and calm, so the filter
    vetoes some entries and lag changes which."""
    rows = []
    for i in range(days * 3):
        rate = 0.002 if (i // 9) % 2 else 0.0
        rows.append(FundingRate(exchange=EXCHANGE, symbol=PERP, funding_time=start + timedelta(hours=8 * i),
                                funding_rate=rate))
    FundingRate.objects.bulk_create(rows)


def live_prints(n, lag_s):
    FundingRate.objects.bulk_create([
        FundingRate(exchange=EXCHANGE, symbol=PERP, funding_time=T0 + timedelta(hours=8 * i), funding_rate=0.0001,
                    fetched_at=T0 + timedelta(hours=8 * i, seconds=lag_s(i)))
        for i in range(n)
    ])


# --- measurement --------------------------------------------------------------

def test_backfill_stamps_rows_and_logs_every_attempt(monkeypatch):
    from market import candles

    calls = {"n": 0}

    def fake_history(exchange, symbol, since=None, limit=1000):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("binance unreachable")
        return [{"timestamp": since, "fundingRate": 0.0001}] if calls["n"] == 1 else []

    monkeypatch.setattr(candles, "fetch_funding_rate_history", fake_history)
    monkeypatch.setattr(candles, "create_funding_exchange", lambda _id: None)
    assert candles.backfill_funding(EXCHANGE, PERP, "2026-11-01T00:00:00Z") == 1
    with pytest.raises(RuntimeError):
        candles.backfill_funding(EXCHANGE, PERP, "2026-11-01T00:00:00Z")
    row = FundingRate.objects.get()
    assert row.fetched_at is not None
    attempts = list(FetchAttempt.objects.order_by("started_at").values_list("ok", "new_rows", "error"))
    assert attempts[0][:2] == (True, 1)
    assert attempts[1][0] is False and "binance unreachable" in attempts[1][2]


def test_latency_counts_only_live_pickups_and_measures_outages():
    from market.latency import funding_latency

    live_prints(10, lambda i: 30 + i)  # 30..39s
    # a history backfill picked up a year late: not a live sample
    FundingRate.objects.create(exchange=EXCHANGE, symbol=PERP, funding_time=T0 - timedelta(days=365),
                               funding_rate=0.0, fetched_at=T0)
    for minute, ok in enumerate([True, False, False, False, True, False, True]):
        t = T0 + timedelta(minutes=minute)
        FetchAttempt.objects.create(source="funding", started_at=t, finished_at=t, ok=ok)
    stats = funding_latency(EXCHANGE, PERP)
    assert stats.samples == 10 and stats.median_s == 34.5 and stats.max_s == 39
    assert stats.attempts == 7 and stats.failures == 4
    assert stats.longest_outage_s == 180  # failing from minute 1 until the success at minute 4


def test_levels_switch_from_assumed_to_measured():
    from research.lag import levels_for

    levels, basis = levels_for("funding_filtered")
    assert basis["basis"] == "assumed" and levels["p95"].source_delay_s == 900
    live_prints(40, lambda i: 60 + 10 * i)  # 60..450s
    levels, basis = levels_for("funding_filtered")
    assert basis["basis"] == "measured" and basis["measured"]["samples"] == 40
    assert levels["typical"].source_delay_s == pytest.approx(255)


def test_engine_measures_funding_even_with_no_funding_bot(monkeypatch):
    from trading.engine import loop

    fetched = []
    monkeypatch.setattr(loop, "backfill_candles", lambda *a, **k: 0)
    monkeypatch.setattr(loop, "backfill_funding", lambda *a, **k: fetched.append(a) or 0)
    engine = loop.Engine()
    assert engine.measure_funding
    engine.poll_bars()
    assert len(fetched) == 1


# --- research jobs --------------------------------------------------------------

def _history():
    make_candles([100.0 + (i % 40) + i * 0.02 for i in range(1300)], start=datetime(2023, 1, 1, tzinfo=timezone.utc))
    funding_history()


def test_lag_levels_are_validated():
    with pytest.raises(JobError, match="reads no outside data"):
        validate_params("research_report", {"strategy": "donchian", "lag_levels": ["p95"]})
    with pytest.raises(JobError, match="unknown lag level"):
        validate_params("research_report", {"strategy": "funding_filtered", "lag_levels": ["p99"]})
    with pytest.raises(JobError, match="lag levels"):
        validate_params("research_report", {"strategy": "funding_filtered",
                                             "params": {"funding_filtered.high_threshold": [1, 2, 3, 4, 5]},
                                             "lag_levels": ["zero", "typical", "p95", "double_p95", "plus_1d"]})
    params = validate_params("research_report", {"strategy": "funding_filtered", "lag_levels": ["zero", "p95"]})
    assert params["lag_levels"] == ["zero", "p95"]
    assert "lag_levels" not in validate_params("research_report", {"strategy": "funding_filtered"})


def test_lag_sweep_runs_every_level_and_reports_the_decay(owner):
    _history()
    levels = ["zero", "p95", "plus_1d"]
    job = ResearchJob.objects.create(kind="research_report", created_by=owner, params={
        "strategy": "funding_filtered", "timeframe": "1d", "baseline": "donchian",
        "params": {"funding_filtered.high_threshold": [0.001]}, "lag_levels": levels,
    })
    run_job(job, fetch=False)
    job.refresh_from_db()
    assert job.status == "done", job.error
    labels = [r["label"] for r in job.result["runs"]]
    assert labels == [f"funding_filtered high_threshold=0.001 @ lag {x}" for x in levels] + ["baseline: donchian"]
    header = job.result["header"]["data_lag"]
    assert header["basis"] == "assumed" and set(header["levels"]) == set(levels)
    decay = job.result["lag_decay"]
    assert len(decay) == 1 and [row["level"] for row in decay[0]["levels"]] == levels
    # each level is its own experiment with its own run key
    keys = set(Experiment.objects.filter(job=job, strategy="funding_filtered").values_list("run_key", flat=True))
    assert len(keys) == 3
    # each run carries the lag it was simulated with (the effect on entries
    # is tested in tests/test_data_availability.py)
    runs = {r["label"].rsplit(" ", 1)[1]: r for r in job.result["runs"] if not r["baseline"]}
    assert runs["zero"]["lag"]["model"]["source_delay_s"] == 0
    assert runs["p95"]["lag"]["model"]["source_delay_s"] == 900
    assert runs["plus_1d"]["lag"]["model"]["extra_delay_s"] == 86400
    assert "lag" not in job.result["runs"][-1]  # the baseline reads no outside data


def test_walk_forward_takes_one_lag_level(owner):
    from research.walkforward_jobs import validate_walk_forward_params

    with pytest.raises(JobError, match="reads no outside data"):
        validate_walk_forward_params({"strategy": "donchian", "timeframe": "1d", "lag_level": "p95"})
    _history()
    job = ResearchJob.objects.create(kind="walk_forward", created_by=owner, params={
        "strategy": "funding_filtered", "timeframe": "1d", "first_test": "2024-01-01", "test_months": 6,
        "lag_level": "p95",
    })
    run_job(job, fetch=False)
    job.refresh_from_db()
    assert job.status == "done", job.error
    assert job.result["header"]["data_lag"]["level"] == "p95"


# --- API --------------------------------------------------------------------------

def test_api_counts_lag_levels_against_the_budget_and_shows_the_lag(owner):
    h = Hypothesis.objects.create(title="lag", statement="funding filter survives realistic lag",
                                  family="filter/funding", trial_budget=3)
    _, key = create_key(owner, "claude", hours=24)
    auth = {"HTTP_AUTHORIZATION": f"Bearer {key}"}
    client = Client()
    body = {"strategy": "funding_filtered", "timeframe": "1d", "hypothesis_id": h.pk,
            "lag_levels": ["zero", "typical", "p95", "plus_1d"]}
    response = client.post("/api/research/jobs", json.dumps(body), content_type="application/json", **auth)
    assert response.status_code == 409 and "adds 4" in response.json()["detail"]
    body["lag_levels"] = ["zero", "p95"]
    response = client.post("/api/research/jobs", json.dumps(body), content_type="application/json", **auth)
    assert response.status_code == 200, response.content
    assert response.json()["params"]["lag_levels"] == ["zero", "p95"]

    lag = client.get("/api/research/data-lag", **auth).json()
    assert lag["basis"] == "assumed" and [x["level"] for x in lag["levels"]][:3] == ["zero", "typical", "p95"]
    assert client.get("/api/research/data-lag?strategy=donchian", **auth).status_code == 400

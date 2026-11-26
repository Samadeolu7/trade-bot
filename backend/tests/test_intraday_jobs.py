import json
from datetime import datetime, timezone

import numpy as np
import pytest
from django.test import Client

from research.intraday_jobs import report_lines, validate_intraday_params
from research.jobs import JobError, run_job
from research.models import Experiment, Hypothesis, ResearchJob


def candles_15m():
    """Synthetic 15m random walk from 2021-06 to past the holdout (not market data)."""
    from market.candles import upsert_candles

    start = int(datetime(2021, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(2026, 3, 10, tzinfo=timezone.utc).timestamp() * 1000)
    n = (end - start) // 900_000
    rng = np.random.default_rng(1)
    close = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    rows = [[start + i * 900_000, float(o), float(max(o, c) * 1.001), float(min(o, c) * 0.999), float(c), 1.0]
            for i, (o, c) in enumerate(zip(np.r_[close[0], close[:-1]], close))]
    for i in range(0, len(rows), 5000):
        upsert_candles("binance", "BTC/USDT", "15m", rows[i:i + 5000])


def test_params():
    assert validate_intraday_params({}) == {"hypothesis": 7, "batch": "all"}
    assert validate_intraday_params({"batch": "a"})["batch"] == "A"
    with pytest.raises(JobError):
        validate_intraday_params({"batch": "C"})


def test_intraday_job_excludes_the_holdout_and_records_every_variant(owner, monkeypatch):
    monkeypatch.setattr("research.intraday_jobs.HISTORY_FROM", "2021-07-01")  # synthetic data starts 2021-06
    candles_15m()
    h = Hypothesis.objects.create(title="24/7", statement="s", family="intraday", trial_budget=100)
    client = Client()
    client.force_login(owner)
    r = client.post("/api/research/intraday-jobs", json.dumps({"batch": "A", "hypothesis_id": h.pk}),
                    content_type="application/json")
    assert r.status_code == 200, r.content
    job = run_job(ResearchJob.objects.get(pk=r.json()["id"]), fetch=False)
    assert job.status == "done", job.error
    rows = job.result["batches"]["A"]["rows"]
    assert len(rows) == 16 and all(set(row) >= {"sharpe", "pass", "deflated_sharpe"} for row in rows)
    assert job.result["header"]["data"].split("..")[1][:10] < "2026-03-01"
    exps = Experiment.objects.filter(kind="intraday", job=job)
    assert exps.count() == 16 and all(e.hypothesis_id == h.pk for e in exps)
    assert all(e.window_end[:10] < "2026-03-01" for e in exps)
    assert report_lines(job.result)[0].startswith("Batch A")
    again = client.post("/api/research/intraday-jobs", json.dumps({"batch": "A", "hypothesis_id": h.pk}),
                        content_type="application/json")
    assert again.status_code == 409


def test_server_fetch_start_date_parses():
    import ccxt

    from market.candles import backfill_candles
    from research.intraday_jobs import START

    assert ccxt.Exchange.parse8601(START) is not None
    with pytest.raises(ValueError, match="with a time"):
        backfill_candles(None, "binance", "BTC/USDT", "15m", "2020-01-01")


def test_job_refuses_incomplete_history(owner):
    candles_15m()  # synthetic data starts in 2021-06, so the 2020 history is missing
    job = ResearchJob.objects.create(kind="intraday", created_by=owner, params={"batch": "A"})
    job = run_job(job, fetch=False)
    assert job.status == "failed" and "download is incomplete" in job.error


def test_macro_params_need_fred_for_cpi_and_nfp(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert validate_intraday_params({"batch": "macro"}) == {"hypothesis": 9, "batch": "macro", "events": ["fomc"]}
    with pytest.raises(JobError, match="FRED_API_KEY"):
        validate_intraday_params({"batch": "macro", "events": ["cpi", "fomc"]})
    with pytest.raises(JobError, match="events"):
        validate_intraday_params({"batch": "macro", "events": ["gdp"]})
    monkeypatch.setenv("FRED_API_KEY", "x")
    assert validate_intraday_params({"batch": "macro", "events": ["nfp", "fomc", "cpi"]})["events"] == ["fomc", "cpi", "nfp"]


def test_macro_job_runs_and_counts_its_variants(owner, monkeypatch):
    from research.intraday_jobs import variant_count

    monkeypatch.setattr("research.intraday_jobs.HISTORY_FROM", "2021-07-01")
    candles_15m()
    h = Hypothesis.objects.create(title="macro", statement="s", family="intraday/macro", trial_budget=2)
    client = Client()
    client.force_login(owner)
    response = client.post("/api/research/intraday-jobs", json.dumps({"batch": "macro", "hypothesis_id": h.pk}),
                           content_type="application/json")
    assert response.status_code == 200, response.content
    job = ResearchJob.objects.get(pk=response.json()["id"])
    assert variant_count(job.params) == 2
    run_job(job, fetch=False)
    job.refresh_from_db()
    assert job.status == "done", job.error
    rows = job.result["batches"]["macro"]["rows"]
    assert [r["label"] for r in rows] == ["orb baseline", "orb skip fomc days"]
    assert job.result["event_days"]["fomc"]["event_day_trades"] > 0
    assert Experiment.objects.filter(job=job).count() == 2
    assert h.trials_used() == 2

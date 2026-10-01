import json
from datetime import datetime, timezone

import pytest
from django.test import Client

from research.jobs import JobError, run_job
from research.models import Experiment, ResearchJob
from research.walkforward_jobs import validate_walk_forward_params
from tests.conftest import make_candles
from trading.services.bots import trade_bot_config


def candles():
    # daily candles from 2021 through past the holdout (2026-03-01)
    make_candles([100.0 + (i % 40) + i * 0.05 for i in range(2000)], start=datetime(2021, 1, 1, tzinfo=timezone.utc))


def test_walk_forward_folds_end_at_the_holdout_and_every_variant_is_recorded(owner):
    candles()
    job = ResearchJob.objects.create(kind="walk_forward", created_by=owner, params={
        "strategy": "donchian", "timeframe": "1d", "first_test": "2023-01-01", "test_months": 6,
        "params": {"donchian.channel_period": [10, 20]},
    })
    run_job(job, fetch=False)
    job.refresh_from_db()
    assert job.status == "done", job.error
    result = job.result
    holdout = trade_bot_config()["validation"]["holdout_start"][:10]
    assert len(result["folds"]) == 7  # 2023H1 .. 2025H2, then 2026-01..02
    assert result["folds"][-1]["test"].endswith("2026-02-28")
    assert all(row["test"].split("..")[1] < holdout for row in result["folds"])
    assert result["header"]["variants"] == 2
    assert {row["chosen"] for row in result["folds"]} <= {"donchian channel_period=10", "donchian channel_period=20"}
    dsr = result["deflated_sharpe"]
    assert dsr["n_trials"] >= 2 and 0 <= dsr["probability"] <= 1 and dsr["variance_source"] == "this job's variants"
    rows = Experiment.objects.filter(kind="walk_forward", job=job)
    assert rows.count() == 2 and all(r.returns and r.run_key for r in rows)
    assert all(r.window_end[:10] <= holdout for r in rows)
    assert {r.strategy_label for r in rows} == {"donchian channel_period=10", "donchian channel_period=20"}

    from research.management.commands.run_worker import report_done

    report_done(job)  # Telegram summary builds without error


def test_walk_forward_params_and_repeats(owner):
    with pytest.raises(JobError, match="year of training"):
        validate_walk_forward_params({"strategy": "donchian", "first_test": "2020-06-01"})
    with pytest.raises(JobError, match="before the holdout"):
        validate_walk_forward_params({"strategy": "donchian", "first_test": "2026-01-01"})
    with pytest.raises(JobError, match="test_months"):
        validate_walk_forward_params({"strategy": "donchian", "test_months": 5})

    candles()
    client = Client()
    client.force_login(owner)
    body = json.dumps({"strategy": "donchian", "timeframe": "1d", "first_test": "2024-01-01"})
    r = client.post("/api/research/walk-forward-jobs", body, content_type="application/json")
    assert r.status_code == 200, r.content
    assert client.post("/api/research/walk-forward-jobs", body, content_type="application/json").status_code == 409
    run_job(ResearchJob.objects.get(pk=r.json()["id"]), fetch=False)
    again = client.post("/api/research/walk-forward-jobs", body, content_type="application/json")
    assert again.status_code == 409 and "Already run" in again.json()["detail"]

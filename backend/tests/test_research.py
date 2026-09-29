from datetime import datetime, timezone

import pytest

from research.jobs import JobError, run_job, validate_params
from research.models import Experiment, ResearchJob
from tests.conftest import make_candles
from trading.services.bots import trade_bot_config


def test_research_job_never_touches_the_holdout(owner):
    # daily candles from 2023 through well past holdout_start (2026-03-01)
    make_candles([100.0 + (i % 40) for i in range(1300)], start=datetime(2023, 1, 1, tzinfo=timezone.utc))
    job = ResearchJob.objects.create(
        kind="research_report", created_by=owner,
        params={"strategy": "donchian", "timeframe": "1d", "params": {"donchian.long_only": [False, True]}},
    )
    run_job(job, fetch=False)
    job.refresh_from_db()
    assert job.status == "done", job.error

    holdout = trade_bot_config()["validation"]["holdout_start"][:10]
    assert job.result["header"]["test"].split("..")[1][:10] < holdout
    assert len(job.result["runs"]) == 3  # two variants and the baseline
    assert Experiment.objects.filter(touched_holdout=True).count() == 0
    assert all(e.window_end[:10] < holdout for e in Experiment.objects.all())


def test_job_params_are_validated():
    with pytest.raises(JobError):
        validate_params("research_report", {"strategy": "nope"})
    with pytest.raises(JobError):
        validate_params("research_report", {"strategy": "donchian", "params": {"rsi_bb.bb_std": [1]}})
    with pytest.raises(JobError):
        validate_params("research_report", {"strategy": "donchian",
                                            "params": {"donchian.channel_period": list(range(30))}})

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


def test_pyramiding_is_a_research_option_but_not_a_bot_option(account, owner):
    from trading.services.bots import create_bot
    from trading.services.orders import OrderError

    params = validate_params("research_report", {"strategy": "donchian",
                                                 "params": {"pyramid.max_adds": [0, 2]}})
    assert params["params"] == {"pyramid.max_adds": [0, 2]}
    with pytest.raises(OrderError, match="research-only"):
        create_bot(account, name="p", strategy="donchian", allocation=100, user=owner,
                   params={"pyramid.max_adds": 2})


def test_cli_reports_are_rebuilt_from_their_experiment_rows():
    from datetime import timedelta

    from django.utils import timezone

    from research.legacy_reports import legacy_reports

    t0 = timezone.now() - timedelta(days=2)
    source = iter(range(1, 100))

    def row(strategy, config, window_start, seconds, kind="research_report"):
        Experiment.objects.create(
            created_at=t0 + timedelta(seconds=seconds), kind=kind, strategy=strategy, strategy_label=strategy,
            symbol="BTC/USDT", timeframe="4h", window_start=window_start, config=config,
            config_hash=str(hash(str(config)))[:12], result={"total_return_pct": seconds}, source_id=next(source),
        )

    a = {"donchian": {"channel_period": 20, "long_only": 0}}
    b = {"donchian": {"channel_period": 20, "long_only": 1}}
    row("donchian", a, "2020-01-01", 0)
    row("donchian", a, "2024-01-01", 5)
    row("donchian", b, "2020-01-01", 10)
    row("donchian", b, "2024-01-01", 15)
    row("donchian_ensemble", {"donchian_ensemble": {}}, "2020-01-01", 20)  # baseline
    row("donchian_ensemble", {"donchian_ensemble": {}}, "2026-03-01", 3600, kind="holdout_check")

    reports = legacy_reports()
    assert [r["kind"] for r in reports] == ["holdout_check", "research_report"]
    runs = reports[1]["runs"]
    assert [r["label"] for r in runs] == ["donchian long_only=0", "donchian long_only=1", "baseline: donchian_ensemble"]
    assert set(runs[0]["windows"]) == {"train", "test"}
    assert set(reports[0]["runs"][0]["windows"]) == {"holdout"}

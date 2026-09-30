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


def _donchian_history():
    make_candles([100.0 + (i % 40) for i in range(1300)], start=datetime(2023, 1, 1, tzinfo=timezone.utc))


def _run(owner, grid):
    job = ResearchJob.objects.create(
        kind="research_report", created_by=owner,
        params={"strategy": "donchian", "timeframe": "1d", "params": grid},
    )
    run_job(job, fetch=False)
    job.refresh_from_db()
    assert job.status == "done", job.error
    return job


def test_exact_repeat_of_a_finished_job_is_refused(owner):
    from research.jobs import RepeatJobError, check_not_repeat

    _donchian_history()
    job = _run(owner, {"donchian.long_only": [False, True]})
    # same settings, values in a different order: still a repeat
    params = validate_params("research_report", {"strategy": "donchian", "timeframe": "1d",
                                                 "params": {"donchian.long_only": [True, False]}})
    with pytest.raises(RepeatJobError, match=f"job #{job.pk}"):
        check_not_repeat(params)
    # a different market or setting is new research
    check_not_repeat({**params, "symbol": "ETH/USDT"})
    check_not_repeat({**params, "params": {"donchian.long_only": [True], "donchian.channel_period": [30]}})


def test_a_repeat_is_allowed_after_the_strategy_code_changes(owner, monkeypatch):
    import research.jobs as jobs

    _donchian_history()
    _run(owner, {"donchian.long_only": [True]})
    params = validate_params("research_report", {"strategy": "donchian", "timeframe": "1d",
                                                 "params": {"donchian.long_only": [True]}})
    monkeypatch.setattr(jobs, "code_version", lambda: "changed-code")
    jobs.check_not_repeat(params)  # no error


def test_partial_repeat_reuses_the_earlier_result_instead_of_recomputing(owner):
    from research.jobs import check_not_repeat

    _donchian_history()
    first = _run(owner, {"donchian.long_only": [True]})
    rows_before = Experiment.objects.filter(strategy="donchian").exclude(job=first).count()
    params = {"donchian.long_only": [True], "donchian.channel_period": [20, 30]}  # 20 is the config default
    check_not_repeat(validate_params("research_report", {"strategy": "donchian", "timeframe": "1d", "params": params}))
    second = _run(owner, params)

    labels = [r["label"] for r in second.result["runs"]]
    reused = [r for r in second.result["runs"] if r["label"].endswith("(earlier result)")]
    assert len(reused) == 1 and "channel_period=20" in reused[0]["label"], labels
    assert f"job #{first.pk}" in reused[0]["earlier_result"]
    assert reused[0]["equity"] == {}
    first_variant = next(r for r in first.result["runs"] if not r["baseline"])
    assert reused[0]["windows"]["test"] == first_variant["windows"]["test"]
    # only the new variant and the baseline were computed and recorded
    assert Experiment.objects.filter(job=second).count() == 4
    assert rows_before == 0


def test_identical_job_already_queued_is_refused(owner):
    from research.jobs import RepeatJobError, check_not_repeat

    params = validate_params("research_report", {"strategy": "donchian", "params": {"pyramid.max_adds": [5]}})
    ResearchJob.objects.create(kind="research_report", created_by=owner, params=params)
    with pytest.raises(RepeatJobError, match="already queued"):
        check_not_repeat(params)


def test_api_answers_a_repeat_with_409(owner):
    import json

    from django.test import Client

    _donchian_history()
    job = _run(owner, {"donchian.long_only": [True]})
    client = Client()
    client.force_login(owner)
    response = client.post("/api/research/jobs", json.dumps(
        {"strategy": "donchian", "timeframe": "1d", "params": {"donchian.long_only": [True]}}
    ), content_type="application/json")
    assert response.status_code == 409
    assert f"job #{job.pk}" in response.json()["detail"]


def test_pyramid_step_must_be_a_fraction():
    with pytest.raises(JobError, match="0.03 means 3%"):
        validate_params("research_report", {"strategy": "donchian",
                                            "params": {"pyramid.max_adds": [5], "pyramid.add_step_pct": [20]}})
    with pytest.raises(JobError, match="0.03 means 3%"):
        validate_params("research_report", {"strategy": "donchian",
                                            "params": {"pyramid.max_adds": [5], "pyramid.add_step_pct": [-0.1]}})
    ok = validate_params("research_report", {"strategy": "donchian",
                                             "params": {"pyramid.max_adds": [5], "pyramid.add_step_pct": [0, 0.03]}})
    assert ok["params"]["pyramid.add_step_pct"] == [0, 0.03]


def test_pyramid_options_without_adds_are_refused():
    # max_adds left at its config default of 0: add_step_pct can't change anything
    with pytest.raises(JobError, match="only matters when pyramid.max_adds is above 0"):
        validate_params("research_report", {"strategy": "donchian", "params": {"pyramid.add_step_pct": [0.03]}})
    with pytest.raises(JobError, match="only matters when pyramid.max_adds is above 0"):
        validate_params("research_report", {"strategy": "donchian",
                                            "params": {"pyramid.max_adds": [0], "pyramid.add_step_pct": [0.03]}})
    # comparing no adds against some adds is a real comparison
    validate_params("research_report", {"strategy": "donchian",
                                        "params": {"pyramid.max_adds": [0, 5], "pyramid.add_step_pct": [0.03]}})


def test_pyramid_max_adds_must_be_a_whole_number():
    with pytest.raises(JobError, match="whole number"):
        validate_params("research_report", {"strategy": "donchian", "params": {"pyramid.max_adds": [2.5]}})


def test_variants_differing_only_in_inert_pyramid_options_are_computed_once(owner):
    _donchian_history()
    job = _run(owner, {"pyramid.max_adds": [0, 1], "pyramid.add_step_pct": [0, 0.02]})
    labels = [r["label"] for r in job.result["runs"]]
    same = [r for r in job.result["runs"] if "(same as" in r["label"]]
    # max_adds=0 with step 0.02 is the same computation as max_adds=0 with step 0
    assert len(same) == 1 and "max_adds=0" in same[0]["label"], labels
    first = next(r for r in job.result["runs"] if r["label"] == same[0]["label"].split(" (same as ")[1].rstrip(")"))
    assert same[0]["windows"] == first["windows"]
    # three distinct variants plus the baseline, two windows each
    assert Experiment.objects.filter(job=job).count() == 8


def _coin(symbol, seed, volume, start=datetime(2018, 1, 1, tzinfo=timezone.utc), days=3000):
    import numpy as np

    from market.candles import upsert_candles

    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.03, days)))
    rows = []
    for i, c in enumerate(closes):
        t = int((start.timestamp() + 86400 * i) * 1000)
        rows.append([t, c, c, c, c, volume])
    upsert_candles("binance", symbol, "1d", rows)


def test_portfolio_job_runs_both_windows_and_never_touches_the_holdout(owner):
    _coin("AAA/USDT", 1, 1e8)  # dollar volume = price x units; wide gaps so price drift can't reorder them
    _coin("BBB/USDT", 2, 1e5)
    _coin("CCC/USDT", 3, 1.0)
    job = ResearchJob.objects.create(
        kind="portfolio_report", created_by=owner,
        params={"timeframe": "1d", "pool": ["AAA/USDT", "BBB/USDT", "CCC/USDT"], "sizes": [2]},
    )
    run_job(job, fetch=False)
    job.refresh_from_db()
    assert job.status == "done", job.error
    run = job.result["runs"][0]
    assert run["label"] == "top-2 rotational donchian_ensemble"
    assert run["windows"]["train"] and run["windows"]["test"]
    # the monthly universe is the two highest-volume coins
    assert run["universe"]["test"]["2024-01"] == ["AAA/USDT", "BBB/USDT"]
    holdout = trade_bot_config()["validation"]["holdout_start"][:10]
    assert job.result["header"]["test"].split("..")[1][:10] < holdout
    assert Experiment.objects.filter(job=job, symbol="PORTFOLIO").count() == 2


def test_portfolio_api_validates_and_refuses_repeats(owner):
    import json

    from django.test import Client

    client = Client()
    client.force_login(owner)

    def post(body):
        return client.post("/api/research/portfolio-jobs", json.dumps(body), content_type="application/json")

    assert post({"sizes": [0]}).status_code == 400
    assert post({"params": {"donchian.channel_period": 30}}).status_code == 400
    first = post({"sizes": [10]})
    assert first.status_code == 200, first.content
    again = post({"sizes": [10]})
    assert again.status_code == 409 and "already queued" in again.json()["detail"]

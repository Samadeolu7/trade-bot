import numpy as np
import pandas as pd
import pytest

from bot.data import macro_calendar as M
from bot.research import macro_report as MR


def test_fomc_calendar_is_scheduled_statement_days_in_new_york_time():
    ev = M.events(["fomc"])
    assert len(ev) == 63 and (ev["kind"] == "fomc").all()
    times = ev.index
    assert pd.Timestamp("2020-03-03 19:00", tz="UTC") not in times  # unscheduled cut, left out
    assert pd.Timestamp("2020-03-15", tz="UTC") not in times.normalize()
    assert pd.Timestamp("2024-05-01 18:00", tz="UTC") in times  # Apr/May meeting, summer time
    assert pd.Timestamp("2024-12-18 19:00", tz="UTC") in times  # winter time
    assert times.is_monotonic_increasing


def test_fred_kinds_need_their_dates():
    with pytest.raises(ValueError, match="FRED_API_KEY"):
        M.events(["cpi"])
    ev = M.events(["cpi", "nfp"], {"cpi": ["2024-07-11"], "nfp": ["2024-07-05"]})
    assert ev.index.tolist() == [pd.Timestamp("2024-07-05 12:30", tz="UTC"), pd.Timestamp("2024-07-11 12:30", tz="UTC")]
    with pytest.raises(ValueError, match="unknown"):
        M.events(["gdp"])


def test_fred_fetch_parses_release_dates(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"release_dates": [{"release_id": 10, "date": "2024-01-11"}, {"release_id": 10, "date": "2024-02-13"}]}

    seen = {}
    monkeypatch.setattr(M.requests, "get", lambda url, params, timeout: seen.update(params) or Resp())
    assert M.fetch_fred_release_dates("cpi", "k") == ["2024-01-11", "2024-02-13"]
    assert seen["release_id"] == 10 and seen["api_key"] == "k"


def test_variants():
    assert [v[0] for v in MR.variants(["fomc"])] == ["orb baseline", "orb skip fomc days"]
    assert len(MR.variants(["fomc", "cpi", "nfp"])) == 5


def test_skip_variant_is_the_baseline_minus_event_day_trades():
    rng = np.random.default_rng(3)
    idx = pd.date_range("2021-06-01", "2026-02-27 23:45", freq="15min", tz="UTC")
    close = 30000 * np.exp(np.cumsum(rng.normal(0, 0.002, len(idx))))
    df = pd.DataFrame({"open": close, "high": close * 1.001, "low": close * 0.999, "close": close, "volume": 1.0},
                      index=idx)
    rows, evidence = MR.run(df, ["fomc"], log=lambda m: None)
    base, skip = rows
    fomc = evidence["fomc"]
    days = M.event_days(M.events(["fomc"]))
    in_data = {d for d in days if idx[0] <= d <= idx[-1] and d.weekday() < 5}
    assert fomc["event_days"] == len(days)
    assert base["trades"] - skip["trades"] <= len(in_data)
    assert base["trades"] > skip["trades"]
    assert fomc["event_day_trades"] > 0 and fomc["event_day_mean_p05_pct"] <= fomc["event_day_mean_pct"] <= fomc["event_day_mean_p95_pct"]

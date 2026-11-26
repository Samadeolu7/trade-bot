import numpy as np
import pandas as pd
import pytest

from bot.data.availability import (
    LagModel, asof_values, available_at, bar_length, lag_levels, latency_stats,
)
from bot.strategy.funding_filter import FundingFilteredStrategy
from tests.test_funding_filter_strategy import AlwaysLongStrategy


def hourly(n=6, start="2024-01-01"):
    return pd.date_range(start, periods=n, freq="h", tz="UTC")


def test_zero_lag_value_counts_from_the_bar_whose_close_it_lands_by():
    bars = hourly()  # opens 00:00 .. 05:00, closes 01:00 .. 06:00
    values = pd.Series([1.0, 2.0], index=pd.DatetimeIndex(
        ["2024-01-01 00:30", "2024-01-01 02:00"], tz="UTC"))
    out = asof_values(values, bars, LagModel())
    # 00:30 lands inside bar 0 (closes 01:00); 02:00 at bar 1's close exactly
    assert out.tolist() == [1.0, 2.0, 2.0, 2.0, 2.0, 2.0]


def test_bar_open_reproduces_the_live_lookup():
    bars = hourly()
    values = pd.Series([1.0, 2.0], index=pd.DatetimeIndex(
        ["2024-01-01 00:30", "2024-01-01 02:00"], tz="UTC"))
    out = asof_values(values, bars, LagModel(use_at="bar_open"))
    assert np.isnan(out.iloc[0])
    assert out.iloc[1:].tolist() == [1.0, 2.0, 2.0, 2.0, 2.0]


def test_delay_pushes_a_value_to_later_bars():
    bars = hourly()
    values = pd.Series([1.0, 2.0], index=pd.DatetimeIndex(
        ["2024-01-01 00:30", "2024-01-01 02:00"], tz="UTC"))
    out = asof_values(values, bars, LagModel(source_delay_s=45 * 60))  # 01:15 and 02:45
    assert np.isnan(out.iloc[0])
    # bar 1 closes 02:00 (has the first), bar 2 closes 03:00 (has the second)
    assert out.iloc[1:].tolist() == [1.0, 2.0, 2.0, 2.0, 2.0]


def test_an_outage_delayed_value_never_replaces_a_newer_one():
    bars = hourly(8)
    values = pd.Series([1.0, 2.0], index=pd.DatetimeIndex(
        ["2024-01-01 00:00", "2024-01-01 01:00"], tz="UTC"))
    # every value hit by a 5h outage: still the newest arrival wins by event
    # time, and nothing is used before it arrives
    model = LagModel(miss_rate=1.0, outage_s=5 * 3600)
    out = asof_values(values, bars, model)
    assert out.isna().sum() == 4  # first arrives 05:00 = close of bar 4
    assert out.iloc[4] == 1.0 and out.iloc[5:].tolist() == [2.0, 2.0, 2.0]


def test_outages_are_reproducible_and_roughly_the_requested_share():
    model = LagModel(miss_rate=0.2, outage_s=100, seed=7)
    a, b = model.delays(5000), model.delays(5000)
    assert (a == b).all()
    assert 0.17 < (a > 0).mean() < 0.23


def test_available_at_adds_extra_delay():
    times = pd.DatetimeIndex(["2024-01-01"], tz="UTC")
    out = available_at(times, LagModel(source_delay_s=60, extra_delay_s=86400))
    assert out[0] == pd.Timestamp("2024-01-02 00:01", tz="UTC")


def test_bad_models_are_refused():
    with pytest.raises(ValueError):
        LagModel(use_at="midnight")
    with pytest.raises(ValueError):
        LagModel(miss_rate=5)
    with pytest.raises(ValueError):
        LagModel(source_delay_s=-1)


def test_bar_length_is_the_usual_spacing_despite_gaps():
    idx = hourly(10).delete([3, 4])
    assert bar_length(idx) == pd.Timedelta(hours=1)


def test_levels_use_assumptions_until_enough_samples_then_measurements():
    few = latency_stats([10.0] * 5)
    levels = lag_levels(few, {"typical_s": 100, "p95_s": 500, "min_samples": 30})
    assert levels["p95"].basis == "assumed" and levels["p95"].source_delay_s == 500
    assert levels["double_p95"].source_delay_s == 1000
    assert levels["plus_1d"].extra_delay_s == 86400

    many = latency_stats(list(range(1, 101)), attempts=1000, failures=100, longest_outage_s=3600)
    levels = lag_levels(many, {"min_samples": 30, "outage_rate": 0.05, "outage_s": 600})
    assert levels["typical"].basis == "measured"
    assert levels["typical"].source_delay_s == pytest.approx(50.5)
    assert levels["p95"].source_delay_s == pytest.approx(95.05)
    # measured failure rate (10%) and outage (1h) beat the floors
    assert levels["p95_outages"].miss_rate == pytest.approx(0.1)
    assert levels["p95_outages"].outage_s == 3600
    assert levels["zero"].source_delay_s == 0


def test_round_trip_and_description():
    m = LagModel("p95", 900, miss_rate=0.05, outage_s=21600, basis="assumed")
    assert LagModel.from_dict(m.to_dict()) == m
    assert "15m delay (assumed)" in m.describe() and "6h late" in m.describe()


def _funding_filter(lag):
    bars = pd.date_range("2024-01-01", periods=6, freq="h", tz="UTC")
    close = pd.Series(100.0, index=bars)
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0})
    # crowded funding printed at 02:00
    funding = pd.DataFrame({"funding_rate": [0.0, 0.01]}, index=pd.DatetimeIndex(
        ["2023-12-31 16:00", "2024-01-01 02:00"], tz="UTC", name="funding_time"))
    strategy = FundingFilteredStrategy(AlwaysLongStrategy(), funding, 0.0005, -0.0005, lag=lag)
    return [d if isinstance(d, str) else None for d in strategy.entry_signals(df)["direction"]]


def test_funding_filter_without_a_lag_model_is_unchanged():
    # looked up at bar open: the 02:00 print vetoes from the 02:00 bar
    assert _funding_filter(None) == ["long", "long", None, None, None, None]


def test_funding_filter_with_lag_vetoes_later():
    # zero lag at bar close: the 02:00 print is known at bar 1's close
    assert _funding_filter(LagModel()) == ["long", None, None, None, None, None]
    # 2h15m late: arrives 04:15, first used at bar 4's close (05:00)
    assert _funding_filter(LagModel(source_delay_s=8100)) == ["long", "long", "long", "long", None, None]

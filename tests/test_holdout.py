import pandas as pd
import pytest

from main import apply_holdout_guard, log_holdout_validation


def make_df(dates):
    close = pd.Series([100.0] * len(dates), index=pd.DatetimeIndex(dates, tz="UTC"))
    return pd.DataFrame({"close": close})


def test_no_holdout_configured_is_a_noop():
    df = make_df(pd.date_range("2020-01-01", periods=5, freq="D", tz="UTC"))
    result, touched = apply_holdout_guard(df, None, allow_holdout=False, context_label="test")
    assert len(result) == 5
    assert touched is False


def test_data_entirely_before_holdout_is_untouched():
    df = make_df(pd.date_range("2020-01-01", periods=5, freq="D", tz="UTC"))
    result, touched = apply_holdout_guard(
        df, "2026-01-01T00:00:00Z", allow_holdout=False, context_label="test"
    )
    assert len(result) == 5
    assert touched is False


def test_holdout_excluded_by_default():
    df = make_df(pd.date_range("2025-12-28", periods=10, freq="D", tz="UTC"))  # crosses into 2026-01-01
    result, touched = apply_holdout_guard(
        df, "2026-01-01T00:00:00Z", allow_holdout=False, context_label="test"
    )
    assert touched is False
    assert result.index.max() < pd.Timestamp("2026-01-01T00:00:00Z")
    assert len(result) == 4  # Dec 28, 29, 30, 31 only


def test_holdout_included_when_allowed():
    df = make_df(pd.date_range("2025-12-28", periods=10, freq="D", tz="UTC"))
    result, touched = apply_holdout_guard(
        df, "2026-01-01T00:00:00Z", allow_holdout=True, context_label="test"
    )
    assert touched is True
    assert len(result) == 10  # nothing excluded


def test_empty_df_is_a_noop():
    df = make_df([])
    result, touched = apply_holdout_guard(
        df, "2026-01-01T00:00:00Z", allow_holdout=False, context_label="test"
    )
    assert len(result) == 0
    assert touched is False


def test_log_holdout_validation_appends_entry(tmp_path, monkeypatch):
    import main

    log_path = tmp_path / "holdout_validations.md"
    monkeypatch.setattr(main, "HOLDOUT_LOG_PATH", log_path)

    log_holdout_validation(
        "donchian", "BTC/USDT", "1d", "2026-01-01T00:00:00Z", None, {"profit_factor": 1.5}
    )

    assert log_path.exists()
    content = log_path.read_text()
    assert "donchian on BTC/USDT 1d" in content
    assert "profit_factor" in content

    # a second call appends rather than overwrites
    log_holdout_validation("ema_cross", "ETH/USDT", "1d", None, None, {"profit_factor": 0.8})
    content = log_path.read_text()
    assert "donchian on BTC/USDT 1d" in content
    assert "ema_cross on ETH/USDT 1d" in content

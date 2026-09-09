from bot.storage.db import (
    connect,
    count_experiments,
    get_experiment,
    list_experiments,
    record_experiment,
    set_experiment_decision,
)


def make_conn():
    return connect(":memory:")


def record_one(conn, kind="backtest", strategy="donchian", strategy_label=None, rank_value=None):
    return record_experiment(
        conn,
        kind=kind,
        strategy=strategy,
        strategy_label=strategy_label or strategy,
        symbol="BTC/USDT",
        timeframe="1d",
        window_start="2023-01-01",
        window_end="2024-01-01",
        touched_holdout=False,
        config_json='{"channel_period": 20}',
        config_hash="abc123",
        data_version="500 bars",
        code_commit="deadbeef",
        result_json='{"profit_factor": 1.4, "total_return_pct": 8.2}',
        rank_metric="profit_factor" if kind == "sweep" else None,
        rank_value=rank_value,
    )


def test_record_and_get_experiment_round_trip():
    conn = make_conn()
    experiment_id = record_one(conn)

    row = get_experiment(conn, experiment_id)
    assert row is not None
    assert row["kind"] == "backtest"
    assert row["strategy"] == "donchian"
    assert row["strategy_label"] == "donchian"
    assert row["symbol"] == "BTC/USDT"
    assert row["config_hash"] == "abc123"
    assert row["code_commit"] == "deadbeef"
    assert row["decision"] is None
    assert row["decision_reason"] is None
    assert row["decided_at"] is None


def test_get_experiment_returns_none_for_unknown_id():
    conn = make_conn()
    assert get_experiment(conn, 999) is None


def test_list_experiments_orders_newest_first():
    conn = make_conn()
    first_id = record_one(conn)
    second_id = record_one(conn)

    rows = list_experiments(conn)
    assert [row["id"] for row in rows] == [second_id, first_id]


def test_list_experiments_filters_by_strategy_and_kind():
    conn = make_conn()
    record_one(conn, strategy="donchian", kind="backtest")
    record_one(conn, strategy="vol_expansion", kind="sweep", rank_value=1.1)
    record_one(conn, strategy="donchian", kind="sweep", rank_value=1.9)

    donchian_only = list_experiments(conn, strategy="donchian")
    assert len(donchian_only) == 2
    assert all(row["strategy"] == "donchian" for row in donchian_only)

    sweep_only = list_experiments(conn, kind="sweep")
    assert len(sweep_only) == 2
    assert all(row["kind"] == "sweep" for row in sweep_only)

    both = list_experiments(conn, strategy="donchian", kind="sweep")
    assert len(both) == 1


def test_list_experiments_respects_limit():
    conn = make_conn()
    for _ in range(5):
        record_one(conn)
    assert len(list_experiments(conn, limit=2)) == 2


def test_count_experiments_by_kind():
    conn = make_conn()
    record_one(conn, kind="backtest")
    record_one(conn, kind="backtest")
    record_one(conn, kind="sweep", rank_value=1.0)

    counts = count_experiments(conn)
    assert counts["backtest"] == 2
    assert counts["sweep"] == 1
    assert counts["total"] == 3


def test_count_experiments_empty():
    conn = make_conn()
    counts = count_experiments(conn)
    assert counts == {"total": 0}


def test_set_experiment_decision_persists_and_is_never_automatic():
    conn = make_conn()
    experiment_id = record_one(conn)

    row = get_experiment(conn, experiment_id)
    assert row["decision"] is None  # never set by record_experiment itself

    found = set_experiment_decision(conn, experiment_id, "rejected", "PF collapsed out-of-sample")
    assert found is True

    row = get_experiment(conn, experiment_id)
    assert row["decision"] == "rejected"
    assert row["decision_reason"] == "PF collapsed out-of-sample"
    assert row["decided_at"] is not None


def test_set_experiment_decision_returns_false_for_unknown_id():
    conn = make_conn()
    assert set_experiment_decision(conn, 999, "rejected", "n/a") is False


def test_rejected_experiments_are_not_deleted():
    conn = make_conn()
    experiment_id = record_one(conn)
    set_experiment_decision(conn, experiment_id, "rejected", "weak evidence")

    # still there, still counted — the graveyard is just rows with a decision
    assert get_experiment(conn, experiment_id) is not None
    assert count_experiments(conn)["total"] == 1

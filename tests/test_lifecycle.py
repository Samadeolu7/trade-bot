import pytest

from bot.research.lifecycle import (
    STAGES,
    get_lifecycle_stage,
    is_automation_ready,
    list_lifecycle_stages,
    set_lifecycle_stage,
)
from bot.storage.db import connect


def make_conn():
    return connect(":memory:")


def test_default_stage_is_research_when_unset():
    conn = make_conn()
    assert get_lifecycle_stage(conn, "donchian_adx_control") == "research"


def test_set_and_get_lifecycle_stage_round_trip():
    conn = make_conn()
    set_lifecycle_stage(conn, "donchian_adx_control", "shadowing")
    assert get_lifecycle_stage(conn, "donchian_adx_control") == "shadowing"


def test_set_lifecycle_stage_rejects_invalid_stage():
    conn = make_conn()
    with pytest.raises(ValueError):
        set_lifecycle_stage(conn, "donchian_adx_control", "definitely_not_a_real_stage")


def test_set_lifecycle_stage_overwrites_previous_value():
    conn = make_conn()
    set_lifecycle_stage(conn, "vol_expansion", "research")
    set_lifecycle_stage(conn, "vol_expansion", "shadowing")
    assert get_lifecycle_stage(conn, "vol_expansion") == "shadowing"


def test_list_lifecycle_stages_returns_only_what_was_set():
    conn = make_conn()
    assert list_lifecycle_stages(conn) == {}

    set_lifecycle_stage(conn, "donchian_adx_control", "shadowing")
    set_lifecycle_stage(conn, "rsi_bb", "retired")

    stages = list_lifecycle_stages(conn)
    assert stages == {"donchian_adx_control": "shadowing", "rsi_bb": "retired"}


def test_list_lifecycle_stages_does_not_leak_other_bot_state_keys():
    conn = make_conn()
    from bot.storage.db import set_state

    set_state(conn, "some_strategy_label:last_heartbeat_at", "12345")
    set_lifecycle_stage(conn, "donchian_adx_control", "shadowing")

    assert list_lifecycle_stages(conn) == {"donchian_adx_control": "shadowing"}


def test_is_automation_ready_false_by_default():
    conn = make_conn()
    ready, reasons = is_automation_ready(conn, "donchian_adx_control")
    assert ready is False
    assert "research" in reasons[0]


def test_is_automation_ready_false_at_every_stage_except_the_last():
    conn = make_conn()
    for stage in STAGES:
        set_lifecycle_stage(conn, "x", stage)
        ready, reasons = is_automation_ready(conn, "x")
        if stage == "automation_ready":
            assert ready is True
            assert reasons == []
        else:
            assert ready is False
            assert stage in reasons[0]

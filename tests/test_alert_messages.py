from bot.alerting.messages import (
    format_daily_summary,
    format_error_alert,
    format_exit_message,
    format_heartbeat,
    format_recommendation_entry,
    format_recommendation_exit,
    format_recommendation_stop_update,
    format_signal_message,
)
from bot.strategy.base import Signal


def make_signal(**overrides):
    defaults = dict(
        symbol="",
        timeframe="",
        direction="long",
        entry_price=67234.5,
        stop_loss=61003.2,
        take_profit=None,
        reason="close broke above 20-bar high channel",
        timestamp="2026-09-05T00:00:00Z",
    )
    defaults.update(overrides)
    return Signal(**defaults)


def test_signal_message_is_structured_key_value():
    msg = format_signal_message(make_signal(), "BTC/USDT", "1d", "donchian")
    lines = msg.splitlines()
    assert lines[0] == "SIGNAL_FIRED"
    assert "strategy=donchian" in lines
    assert "symbol=BTC/USDT" in lines
    assert "timeframe=1d" in lines
    assert "direction=long" in lines
    assert "entry=67234.50" in lines
    assert "stop=61003.20" in lines
    assert "reason=close broke above 20-bar high channel" in lines


def test_signal_message_blank_target_when_none():
    msg = format_signal_message(make_signal(take_profit=None), "BTC/USDT", "1d", "donchian")
    assert "target=" in msg.splitlines()


def test_signal_message_formats_target_when_present():
    msg = format_signal_message(make_signal(take_profit=70000.0), "BTC/USDT", "1d", "donchian")
    assert "target=70000.00" in msg.splitlines()


def test_exit_message_structure():
    msg = format_exit_message(
        "BTC/USDT", "1d", "donchian", "long", 67234.5, 71890.0, 6.92, "stop", "2026-09-10T00:00:00Z"
    )
    lines = msg.splitlines()
    assert lines[0] == "POSITION_CLOSED"
    assert "strategy=donchian" in lines
    assert "pnl_pct=6.92" in lines
    assert "reason=stop" in lines


def test_daily_summary_flat():
    msg = format_daily_summary("BTC/USDT", "1d", "donchian", None, 0, 5, 3.21)
    lines = msg.splitlines()
    assert lines[0] == "DAILY_SUMMARY"
    assert "strategy=donchian" in lines
    assert "position=flat" in lines
    assert "trades_today=0" in lines
    assert "trades_all_time=5" in lines
    assert "cumulative_pnl_pct=3.21" in lines


def test_daily_summary_open_position():
    open_position = {
        "direction": "long",
        "entry_time": "2026-09-01T00:00:00Z",
        "entry_price": 65000.0,
        "stop": 60000.0,
    }
    msg = format_daily_summary("BTC/USDT", "1d", "donchian", open_position, 1, 6, 4.0)
    position_line = next(line for line in msg.splitlines() if line.startswith("position="))
    assert "long since 2026-09-01T00:00:00Z" in position_line
    assert "entry=65000.00" in position_line
    assert "stop=60000.00" in position_line


def test_heartbeat_structure():
    msg = format_heartbeat("BTC/USDT", "1d", "donchian")
    assert msg.splitlines()[0] == "HEARTBEAT"
    assert "strategy=donchian" in msg.splitlines()
    assert "status=alive" in msg.splitlines()


def test_error_alert_structure():
    msg = format_error_alert("BTC/USDT", "1d", "donchian", "connection timed out")
    assert msg.splitlines()[0] == "ERROR"
    assert "strategy=donchian" in msg.splitlines()
    assert "detail=connection timed out" in msg.splitlines()


def test_recommendation_entry_is_explicitly_advisory_and_carries_context():
    signal = make_signal(context={"regime": "trending", "atr_pct": 1.23})
    msg = format_recommendation_entry(signal, "BTC/USDT", "1d", "reco_donchian_adx_control")
    lines = msg.splitlines()
    assert lines[0] == "RECOMMENDATION_ENTRY"
    assert "strategy=reco_donchian_adx_control" in lines
    assert "direction=long" in lines
    assert "entry=67234.50" in lines
    assert "ctx_regime=trending" in lines
    assert "ctx_atr_pct=1.23" in lines
    assert "note=for your review — no order placed" in lines


def test_recommendation_entry_includes_fear_greed_when_given():
    signal = make_signal()
    msg = format_recommendation_entry(
        signal, "BTC/USDT", "1d", "reco_donchian_adx_control",
        fear_greed={"value": 66, "classification": "Greed"},
    )
    assert "fear_greed=66 (Greed)" in msg.splitlines()


def test_recommendation_entry_omits_fear_greed_when_not_given():
    msg = format_recommendation_entry(make_signal(), "BTC/USDT", "1d", "reco_donchian_adx_control")
    assert not any(line.startswith("fear_greed=") for line in msg.splitlines())


def test_recommendation_exit_structure():
    msg = format_recommendation_exit(
        "BTC/USDT", "1d", "reco_donchian_adx_control", "long", 67234.5, 71890.0, 6.92, "stop",
        "2026-09-10T00:00:00Z",
    )
    lines = msg.splitlines()
    assert lines[0] == "RECOMMENDATION_EXIT"
    assert "strategy=reco_donchian_adx_control" in lines
    assert "pnl_pct=6.92" in lines
    assert "note=for your review — no order placed" in lines


def test_recommendation_stop_update_structure():
    msg = format_recommendation_stop_update(
        "BTC/USDT", "1d", "reco_multi_timeframe", "long", 60000.0, 61000.0,
    )
    lines = msg.splitlines()
    assert lines[0] == "RECOMMENDATION_STOP_UPDATE"
    assert "strategy=reco_multi_timeframe" in lines
    assert "old_stop=60000.00" in lines
    assert "new_stop=61000.00" in lines
    assert "note=update your MT5 stop-loss order to match" in lines

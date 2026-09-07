from bot.alerting.messages import (
    format_daily_summary,
    format_error_alert,
    format_exit_message,
    format_heartbeat,
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

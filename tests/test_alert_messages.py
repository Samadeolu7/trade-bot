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


def test_daily_summary_renders_epoch_ms_entry_time_as_date():
    open_position = {"direction": "long", "entry_time": 1790035200000, "entry_price": 86669.12, "stop": 62535.24}
    msg = format_daily_summary("BTC/USDT", "1d", "donchian", open_position, 0, 1, -9.17)
    assert "position=long since 2026-09-22, entry=86669.12, stop=62535.24" in msg.splitlines()


def test_daily_summary_reports_unrealized_pnl_and_stop_outcome():
    open_position = {"direction": "long", "entry_time": 1790035200000, "entry_price": 80000.0, "stop": 60000.0}
    msg = format_daily_summary("BTC/USDT", "1d", "donchian", open_position, 0, 1, -9.17, current_price=84000.0)
    lines = msg.splitlines()
    assert "unrealized_pnl_pct=5.00" in lines
    assert "stop_distance_pct=28.57" in lines
    summary = next(line for line in lines if line.startswith("summary="))
    assert "Holding LONG since 2026-09-22 at 80,000.00." in summary
    assert "+5.00% open" in summary
    assert "closes at a loss of 25.00%" in summary
    assert "Closed trades so far: 1, total -9.17%." in summary


def test_daily_summary_short_stop_above_entry_is_a_loss():
    open_position = {"direction": "short", "entry_time": 1790035200000, "entry_price": 80000.0, "stop": 84000.0}
    msg = format_daily_summary("BTC/USDT", "1d", "donchian", open_position, 0, 0, 0.0, current_price=76000.0)
    lines = msg.splitlines()
    assert "unrealized_pnl_pct=5.00" in lines
    summary = next(line for line in lines if line.startswith("summary="))
    assert "closes at a loss of 5.00%" in summary
    assert "No closed trades yet." in summary


def test_daily_summary_trailed_stop_above_entry_is_a_locked_profit():
    open_position = {"direction": "long", "entry_time": 1789430400000, "entry_price": 78241.30, "stop": 83163.68}
    msg = format_daily_summary("BTC/USDT", "1d", "multi_timeframe", open_position, 0, 0, 0.0, current_price=84454.56)
    summary = next(line for line in msg.splitlines() if line.startswith("summary="))
    assert "closes at a profit of 6.29%" in summary


def test_daily_summary_flat_summary_sentence():
    msg = format_daily_summary("BTC/USDT", "1d", "donchian", None, 0, 5, 3.21)
    assert "summary=No open position. Closed trades so far: 5, total +3.21%." in msg.splitlines()


def test_recommendation_stop_update_has_plain_english_action():
    msg = format_recommendation_stop_update(
        "BTC/USDT", "1d", "reco_donchian_adx_control", "long", 62300.0, 62535.24,
    )
    assert (
        "action=Move your MT5 stop-loss on the BTC/USDT long from 62,300.00 to 62,535.24 (up 235.24)"
        in msg.splitlines()
    )


def test_research_report_is_one_paste_friendly_message():
    from bot.alerting.messages import format_research_report

    summary = {"trades": 12, "total_return_pct": 3.456, "max_drawdown_pct": -2.1, "profit_factor": 1.4,
               "win_rate_pct": 58.33, "sharpe_ratio": 0.9, "buy_hold_pct": 80.49}
    messages = format_research_report(
        {"strategy": "crt", "timeframe": "1d"},
        [("crt (defaults)", summary, None), ("baseline: donchian", summary, summary)],
    )
    assert len(messages) == 1
    lines = messages[0].splitlines()
    assert lines[0] == "RESEARCH_REPORT"
    assert "strategy=crt" in lines
    assert "[crt (defaults)]" in lines
    assert " train: n=12 ret=+3.46% dd=-2.10% pf=1.40 win=58.3% sharpe=0.90 bh=+80.49%" in lines
    assert " test : no data in window" in lines
    assert lines[-1] == "note=paste this whole message back to Claude"


def test_research_report_splits_long_batches_under_telegram_limit():
    from bot.alerting.messages import TELEGRAM_MAX_CHARS, format_research_report

    summary = {"trades": 1}
    runs = [(f"crt variant {i} " + "x" * 80, summary, summary) for i in range(60)]
    messages = format_research_report({"strategy": "crt"}, runs)
    assert len(messages) > 1
    assert all(len(m) <= 4096 for m in messages)
    assert messages[0].endswith(f"(part 1/{len(messages)})")
    assert sum(m.count("[crt variant") for m in messages) == 60
    assert TELEGRAM_MAX_CHARS < 4096


def test_exposure_rebalance_actions_read_as_mt5_instructions():
    from bot.alerting.messages import format_exposure_rebalance

    open_msg = format_exposure_rebalance("BTC/USDT", "4h", "ens", 0.0, 0.38, 84000.0, 1790035200000, 10000.0)
    assert "action=Open a BTC/USDT long worth 38% of your trading capital" in open_msg.splitlines()
    assert "time=2026-09-22" in open_msg.splitlines()
    add = format_exposure_rebalance("BTC/USDT", "4h", "ens", 0.30, 0.42, 84000.0, 1790035200000, 10000.0)
    assert "action=Add to the BTC/USDT long: 30% → 42% of capital (buy 12% of capital)" in add.splitlines()
    cut = format_exposure_rebalance("BTC/USDT", "4h", "ens", 0.42, 0.25, 84000.0, 1790035200000, 10000.0)
    assert "action=Reduce the BTC/USDT long: 42% → 25% of capital (sell 17% of capital)" in cut.splitlines()
    close = format_exposure_rebalance("BTC/USDT", "4h", "ens", 0.25, 0.0, 84000.0, 1790035200000, 10000.0)
    assert "action=Close the BTC/USDT position (was 25% of capital)" in close.splitlines()
    assert close.splitlines()[-1] == "note=paper trading — no order placed"


def test_exposure_summary_line():
    from bot.alerting.messages import format_exposure_summary

    msg = format_exposure_summary("BTC/USDT", "4h", "ens", 0.4, 10500.0, 10000.0, 1790035200000, 3)
    assert (
        "summary=Holding 40% of capital in BTC/USDT. Paper account 10,500.00 (+5.00% since 2026-09-22), "
        "3 rebalances so far." in msg.splitlines()
    )


def test_exposure_rebalance_advisory_variant():
    from bot.alerting.messages import format_exposure_rebalance

    msg = format_exposure_rebalance(
        "BTC/USDT", "4h", "reco_ens", 0.0, 0.4, 84000.0, 1790035200000, 10000.0, advisory=True
    )
    lines = msg.splitlines()
    assert lines[0] == "RECOMMENDATION_REBALANCE"
    assert lines[-1] == "note=for your review — no order placed; resize your MT5 position to match"


def test_research_report_supports_other_window_sets_and_titles():
    from bot.alerting.messages import format_research_report

    summary = {"trades": 3, "total_return_pct": 5.0}
    msg = format_research_report(
        {"check": "HOLDOUT"}, [("ens", [("holdout", summary)])], title="HOLDOUT_CHECK"
    )[0]
    lines = msg.splitlines()
    assert lines[0] == "HOLDOUT_CHECK"
    assert "[ens]" in lines
    assert any(line.startswith(" holdout: n=3 ret=+5.00%") for line in lines)
    assert not any("train" in line for line in lines)

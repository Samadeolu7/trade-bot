from main import parse_param_arg


def test_parse_param_arg_splits_section_key_and_values():
    section, key, values = parse_param_arg("rsi_bb.rsi_oversold=20,25,30")
    assert section == "rsi_bb"
    assert key == "rsi_oversold"
    assert values == [20, 25, 30]


def test_parse_param_arg_coerces_floats():
    section, key, values = parse_param_arg("rsi_bb.stop_band_mult=0.5,0.75,1.0")
    assert section == "rsi_bb"
    assert key == "stop_band_mult"
    assert values == [0.5, 0.75, 1.0]


def test_parse_param_arg_keeps_non_numeric_strings():
    section, key, values = parse_param_arg("donchian.exit_method=channel,atr")
    assert section == "donchian"
    assert key == "exit_method"
    assert values == ["channel", "atr"]


def test_parse_param_arg_single_value():
    section, key, values = parse_param_arg("donchian.exit_channel_period=90")
    assert (section, key, values) == ("donchian", "exit_channel_period", [90])

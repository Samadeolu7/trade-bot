import pandas as pd

from bot.research.intraday import daily_returns, exit_scan, Bars, momentum, orb, vol_breakout, window


def frame(day="2024-01-03", closes=None, start_hour=0):
    idx = pd.date_range(pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=start_hour), periods=len(closes), freq="15min")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c + 1, "low": c - 1, "close": c, "volume": 1.0})


def test_window_weekday_and_weekend():
    wed, sat = pd.Timestamp("2024-01-03", tz="UTC"), pd.Timestamp("2024-01-06", tz="UTC")
    assert window(wed) == (wed + pd.Timedelta(hours=7), wed + pd.Timedelta(hours=20, minutes=45))
    assert window(sat)[0] == sat and window(sat)[1] == sat + pd.Timedelta(hours=23, minutes=30)


def test_stop_fills_at_stop_or_gap_open():
    df = frame(closes=[100, 100, 95, 90])
    b = Bars(df)
    assert exit_scan(b, 0, 1, 97, 3)[2] == "stop"
    df.loc[df.index[2], "open"] = 94  # gapped below the 97 stop
    j, px, why = exit_scan(Bars(df), 0, 1, 97, 3)
    assert (j, px, why) == (2, 94, "stop")


def test_orb_trades_the_breakout_and_closes_by_21_utc():
    closes = [100.0] * 96
    for i in range(56, 60):  # 14:00-15:00: break above the 13:30-14:00 range
        closes[i] = 105.0
    trades = orb(frame(closes=closes), range_minutes=30)
    assert len(trades) == 1 and trades[0].direction == 1
    assert trades[0].entry_time == pd.Timestamp("2024-01-03 14:00", tz="UTC")
    assert trades[0].exit_time <= pd.Timestamp("2024-01-03 20:45", tz="UTC")


def test_vol_breakout_needs_a_prior_day_and_enters_on_k_range():
    prior = [110.0 if i % 2 == 0 else 100.0 for i in range(96)]  # 24h range 12, last close 100
    today = [100.0] * 28 + [100.0] * 4 + [108.0] + [108.0] * 63  # 07:00 open 100, +0.5*12 = 106 crossed
    df = frame(day="2024-01-02", closes=prior + today, start_hour=7)
    trades = vol_breakout(df, k=0.5)
    assert len(trades) == 1 and trades[0].direction == 1 and trades[0].entry == 108.0


def test_momentum_follows_the_day_and_costs_reduce_returns():
    closes = [100.0 + i * 0.1 for i in range(96)]
    trades = momentum(frame(closes=closes), formation_start_hour=7)
    assert len(trades) == 1 and trades[0].direction == 1
    days = pd.DatetimeIndex([pd.Timestamp("2024-01-03", tz="UTC")])
    assert daily_returns(trades, days, 0.0005).iloc[0] < daily_returns(trades, days, 0.0).iloc[0]

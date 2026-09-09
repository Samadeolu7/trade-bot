#!/usr/bin/env bash
# Re-runs the historical backtests/sweeps that are narrated in
# btc-usd-bot-spec.md / notes/PILOT_LOG.md but predate the experiment log
# (2026-09-09) — they were never recorded anywhere queryable, just written
# up in prose, so their exact original CLI invocations aren't recoverable
# (this session's terminal history is gone). This script is a best-effort
# reconstruction from the written record. Every run below goes through the
# real `backtest`/`sweep` commands, which now automatically log to the
# `experiments` table — running this backfills the missing history into
# something queryable instead of losing it for good.
#
# Each block prints the ORIGINALLY OBSERVED result right above the command
# that's supposed to reproduce it, so you can eyeball whether the rerun
# roughly matches. Small differences are expected/fine (Binance's own
# history can shift slightly; a swept grid's exact points here are
# approximated around the deployed defaults where the original exact grid
# wasn't recorded, only its size and standout result).
#
# Needs real Binance network access for anything not already backfilled
# locally (ETH/USDT, SOL/USDT especially) — run this from a machine with
# real internet access, not a sandboxed/offline environment.
#
# Usage: bash scripts/run_experiment_batch.sh   (from the project root)

set -e

TRAIN_START="2020-01-01"
TRAIN_END="2023-12-31"
TEST_START="2024-01-01"
# No --end on the "test" runs below — config.yaml's validation.holdout_start
# (2026-03-01) already excludes anything from there onward automatically,
# with no --allow-holdout needed, so this stays a routine check rather than
# a deliberate holdout look.

echo "=== 1/9: donchian — train window (2020–2023) ==="
echo "    originally observed: PF 3.94, 64.3% win rate — looked strong in-sample"
python main.py backtest --strategy donchian --symbol BTC/USDT --timeframe 1d \
  --start "$TRAIN_START" --end "$TRAIN_END"

echo ""
echo "=== 2/9: donchian — test window (2024+) ==="
echo "    originally observed: PF 0.91, 50% win rate — essentially breakeven out-of-sample"
python main.py backtest --strategy donchian --symbol BTC/USDT --timeframe 1d \
  --start "$TEST_START"

echo ""
echo "=== 3/9: donchian exit-channel-width sweep (train window) ==="
echo "    originally observed: exit_channel_period=55 was the standout at PF 3.94 — this is the sweep that produced it"
python main.py sweep --strategy donchian --symbol BTC/USDT --timeframe 1d \
  --start "$TRAIN_START" --end "$TRAIN_END" \
  --param donchian.exit_channel_period=30,55,70,90 --rank-by profit_factor

echo ""
echo "=== 4/9: regime filter comparison — ADX vs NATR (2020–2023) ==="
echo "    originally observed: ADX 12 trades PF 4.93 / NATR 10 trades PF 4.90 — nearly identical"
python main.py backtest --strategy regime_switched --regime-type adx --symbol BTC/USDT --timeframe 1d \
  --start "$TRAIN_START" --end "$TRAIN_END"
python main.py backtest --strategy regime_switched --regime-type natr --symbol BTC/USDT --timeframe 1d \
  --start "$TRAIN_START" --end "$TRAIN_END"

echo ""
echo "=== 5/9: rsi_bb parameter sweep (train window) ==="
echo "    originally observed: only 2-5 trades regardless of parameters, +\$138 to -\$449 swings — not enough trades to trust"
python main.py sweep --strategy rsi_bb --symbol BTC/USDT --timeframe 1d \
  --start "$TRAIN_START" --end "$TRAIN_END" \
  --param rsi_bb.rsi_oversold=25,30,35 --param rsi_bb.rsi_overbought=65,70,75 \
  --param rsi_bb.stop_band_mult=0.5,0.75,1.0 --rank-by profit_factor

echo ""
echo "=== 6/9: market_structure swing/retest sweep (2020–2023) ==="
echo "    originally observed: best-of-9 PF 1.22, 67 trades, +9.37% return — clustered, unremarkable, weaker than donchian"
python main.py sweep --strategy market_structure --symbol BTC/USDT --timeframe 1d \
  --start "$TRAIN_START" --end "$TRAIN_END" \
  --param market_structure.swing_window=3,5,7 --param market_structure.retest_window=5,10,15 \
  --rank-by profit_factor

echo ""
echo "=== 7/9: cross-asset validation — ETH/USDT, same fixed donchian rule set ==="
echo "    originally observed: train PF 4.40 (+14.46%) / test PF 2.21 (+2.59%) — genuinely encouraging out-of-sample"
python main.py backfill --symbol ETH/USDT --timeframe 1d --start "$TRAIN_START"
python main.py backtest --strategy donchian --symbol ETH/USDT --timeframe 1d --start "$TRAIN_START" --end "$TRAIN_END"
python main.py backtest --strategy donchian --symbol ETH/USDT --timeframe 1d --start "$TEST_START"

echo ""
echo "=== 8/9: cross-asset validation — SOL/USDT, same fixed donchian rule set ==="
echo "    originally observed: train PF 17.68 (+84.18%) / test PF 0.30 (-4.32%) — fails out-of-sample"
python main.py backfill --symbol SOL/USDT --timeframe 1d --start "$TRAIN_START"
python main.py backtest --strategy donchian --symbol SOL/USDT --timeframe 1d --start "$TRAIN_START" --end "$TRAIN_END"
python main.py backtest --strategy donchian --symbol SOL/USDT --timeframe 1d --start "$TEST_START"

echo ""
echo "=== 9/9: vol_expansion and funding_filtered — NEW, no backtest verdict logged anywhere yet ==="
echo "    both were deployed straight to shadow-testing without ever getting a backtest number — first look:"
python main.py backtest --strategy vol_expansion --symbol BTC/USDT --timeframe 1d --start "$TRAIN_START" --end "$TRAIN_END"
python main.py backtest --strategy funding_filtered --symbol BTC/USDT --timeframe 1d --start "$TRAIN_START" --end "$TRAIN_END"

echo ""
echo "Done — every run above is now a queryable row: python main.py experiments"

# Automated research log

Research started through the app's research API (a short-lived research
key), so the rules for judging results are written here before the jobs
run, not after.

## 2026-09-30 — "find a great strategy" search (pre-registered)

**Starting point (best so far, BTC 4h, test window 2024-01-01..2026-02-28):**
donchian_ensemble 4h with rebalance_threshold 0.1: test Sharpe 0.90, return
+30.8%, max DD -12.1%. Plain donchian 4h: test Sharpe 0.61.

**Rules:**
1. Candidates are picked by **train-window** results only (2020–2023).
2. A candidate counts only if its **test** Sharpe also beats the current best
   for its family (ensemble 0.90; donchian 0.61) with drawdown no worse than
   -25%.
3. It must then hold on **ETH/USDT** with identical settings: positive test
   return and Sharpe.
4. For parameter grids, prefer a **plateau** (neighbouring settings also
   good) over the single best cell. An isolated best cell is treated as noise.
5. The reserved holdout (2026-03-01 onward) is not touched. It's already
   spent for the ensemble. Any new winner's next evidence is live paper
   trading.
6. Every job and its outcome is recorded below, including failures.

**Batch 1 (BTC/USDT 4h):**
- A. Ensemble exposure level: vol_target 0.25 / 0.4 / 0.6, threshold 0.1.
  The question is whether more exposure fixes its under-participation in
  uptrends without wrecking drawdown.
- B. Donchian robustness surface: channel_period 10/20/40 × exit_channel_period
  30/55/100.
- C. Donchian ATR trailing exit: atr_mult 2/3/4.
- D. Regime filters on 4h: adx / sma200 / natr.

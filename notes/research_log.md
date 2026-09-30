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

**Batch 1 results (jobs 6–9, BTC 4h), train Sharpe → test Sharpe:**
- A. Ensemble vol_target 0.25 / 0.4 / 0.6: 1.45→0.90, 1.38→0.91, 1.23→0.81.
  Test DD -12.1 / -18.9 / -22.5%. A risk dial, not an edge; by train, 0.25 stays.
- B. Donchian grid: the best train cells (exit 100: 1.27–1.29) have *negative*
  test Sharpe (-0.27). Only exit 55 holds across entry periods on test
  (0.90 / 0.61 / 0.67). Train and test rankings are roughly inverted, a
  regime shift from 2020–21's long trends to choppier 2024–26. No variant
  qualifies under rule 1. The deployed 20/55 sits on the only stable ridge.
- C. ATR exit: mult 2 → -1.43/-1.90, mult 3 → 0.54/0.76, mult 4 → 1.29/0.05.
  Chosen by train it's 4, which fails. Rejected.
- D. Regime filters: adx 0.94/0.44, sma200 0.90/0.54, natr 1.54/0.36. All
  trail plain donchian on test. Rejected.
- Also re-read (user's job 5, pyramid): train Sharpe falls with adds
  (1.57→1.40), test rises (0.52→0.81). By train, no adds. Rejected.

**Batch 2: does the untuned ensemble generalise across coins?** Same config
(4h, bars_per_day 6, threshold 0.1, vol_target 0.25) on ETH, SOL, BNB, XRP,
ADA, DOGE. This is the paper's own route to Sharpe >1.5: breadth, not
tuning. Success means positive test Sharpe on most coins. Then a
multi-coin portfolio is the candidate.

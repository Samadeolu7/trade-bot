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

**Batch 2 results (jobs 10–15), untuned ensemble per coin, train → test Sharpe:**
BTC 1.45→0.90, ETH 1.38→0.75, SOL 1.83→0.39, BNB 1.20→0.88, XRP 0.50→1.36,
ADA 1.45→0.68, DOGE 0.85→0.96. **Positive in all 14 coin-windows**, including
ADA (+17.8% while buy & hold was -52.8%) and SOL/ETH (buy & hold negative).
It beats plain donchian's test Sharpe on 5 of 7 coins (not ETH, DOGE).

**Equal-weight 7-coin portfolio** (approximation: mean of each coin's
normalized equity curve from the job results, ~300 points per window, no
cross-coin rebalancing, fees included per coin; the method reproduces BTC's
own Sharpe, 0.92 vs 0.90 reported):

| | Train Sharpe / DD | Test return / DD / Sharpe |
|---|---|---|
| BTC alone, vol_target 0.25 | ~1.29 / -16.9% | +30.8% / -11.1% / ~0.92 |
| 7 coins, vol_target 0.25 | ~1.51 / -8.5% | +28.8% / -6.8% / ~1.14 |
| 7 coins, vol_target 0.40 (jobs 16–21) | ~1.58 / -14.3% | +42.0% / -11.5% / ~1.04 |

Average pairwise correlation of the coins' strategy returns: 0.25 train,
0.41 test. That's where the improvement comes from. The portfolio beats BTC
alone in both windows without anything being picked on test. It follows the
paper's own design, and it's the best candidate found. At vol_target 0.4 it
matches BTC-alone's drawdown with more return. The vol target is a risk
choice, not a finding.

**Caveats:**
- **Survivorship bias.** The seven coins were chosen today as large,
  surviving coins. Coins that collapsed in the window (LUNA, FTT, ...) aren't
  in the test, which flatters it. The paper uses a point-in-time top-20.
- The portfolio numbers are an approximation from downsampled curves, not a
  full multi-asset backtest.
- The holdout is already spent for the ensemble on BTC/ETH. The other five
  coins' holdout windows are untouched, so they could serve as a clean check.
- Whether all seven are tradable on the user's venue (MT5/Exness CFDs,
  Quidax spot) isn't checked.

Jobs used on this key: 21 of 30.

## Batch 3 (pre-registered): reduce survivorship bias

Key limit raised to 100 jobs. Same untuned ensemble config on a broader
set: LTC, LINK, DOT, AVAX, ATOM, TRX, BCH, ETC, FIL, NEAR, XLM, UNI, plus two
collapsed coins, LUNC (ex-LUNA) and FTT, if Binance still serves their
history. **Rule:** the portfolio is judged on *every* coin with data, with no
dropping of losers after the fact. If the broad equal-weight portfolio still
beats BTC alone in both windows, the multi-coin result isn't just survivor
picking. The holdout check comes only after this, on a fixed design.

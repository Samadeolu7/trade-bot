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

**Batch 3 results (jobs 22–35), train → test Sharpe:** LTC 0.00→-0.31,
LINK 0.95→0.20, DOT 1.13→0.05, AVAX 1.81→-0.31, ATOM 0.63→-0.30, TRX
0.43→1.38, BCH 0.11→0.10, ETC 0.87→0.16, FIL 1.02→0.00, NEAR 0.69→-0.46,
XLM 0.44→1.59, UNI 0.04→-0.10, LUNC 0.52→-0.07 (data only from 2022-09,
after the collapse), FTT 1.56→-0.39. FTT's train window *includes* its
Nov-2022 collapse, and the ensemble made +91% there, so it exited in time.
Worst test loss on any coin: -9.7% (LTC), versus buy & hold losses of up
to -90%.

Equal-weight portfolios (daily-aligned; coins join when their data starts):

| | Train Sharpe | Test return / DD / Sharpe |
|---|---|---|
| BTC only | ~1.31 | +30.8% / -11.1% / ~0.92 |
| original 7 (survivor-picked) | ~1.66 | +29.8% / -6.8% / ~1.18 |
| the 14 added coins | ~1.66 | +6.9% / -8.4% / ~0.40 |
| all 21 | ~1.83 | +14.1% / -7.3% / ~0.74 |

**Survivorship did flatter the 7-coin result.** On the broad set, test
Sharpe falls below BTC alone. 2024–26 was an altcoin bear, and the ensemble
limited the damage but couldn't profit. Next: point-in-time universes (top
coins by market cap at each window's start, the paper's method), which
removes the survivor pick. Adding EOS and XTZ (top-10 in Jan 2020).

**Point-in-time universes and strategy blends (jobs 36–37 add EOS, XTZ).**

**Correction to my own analysis:** the earlier portfolio figures put each
job's downsampled equity curve on a *daily* grid. Different jobs' curves
have points on different days (e.g. donchian starts 9 days into a window,
after its warmup), so on a daily grid their moves never coincide. That
faked near-zero correlation and inflated every blend's Sharpe. All figures
below use weekly closes, where every series has real values.

| Weekly, aligned | Train Sharpe | Test return / DD / Sharpe |
|---|---|---|
| **BTC ensemble alone** | 1.21 | +32.5% / -9.9% / 0.92 |
| original 7 (survivor-picked) | 1.64 | +30.1% / -6.8% / 1.04 |
| all 23 coins | 1.41 | +15.7% / -7.3% / 0.70 |
| top-10 by market cap at window start (no survivorship) | 0.98 | +27.3% / -6.5% / 0.99 |
| BTC+ETH ensemble 50/50 | 1.34 | +24.3% / -9.3% / 0.84 |
| ensemble + donchian on BTC+ETH, inverse-vol weights from train | 1.27 | +14.3% / -5.5% / 0.93 |

Ensemble vs donchian return correlation on BTC: 0.61 train, 0.51 test.

**Conclusion of this search (37 of 100 key jobs used):** once survivorship
and the alignment artifact are removed, nothing beats the BTC ensemble
alone on risk-adjusted return in *both* windows by a meaningful margin.
- The point-in-time top-10 is worse on train (0.98 vs 1.21) and only
  marginally better on test.
- BTC+ETH is better on train and worse on test.
- Blends mainly lower risk and return together.

The BTC 4h ensemble, already paper-traded, in the recommend feed and
holdout-checked (thin pass), stays the best strategy found. Because the
multi-coin design was dropped, the planned altcoin holdout check isn't
run: it would validate a design that isn't being adopted.

**The one faithful version still untested** is the paper's actual portfolio:
a universe re-selected monthly by trailing volume (top-20 coins with
≥$2M median 30-day volume, point-in-time), ensemble per coin, rotated.
It needs a multi-asset backtester (new code), since the research API
tests one symbol at a time.

## Multi-coin rotational ensemble (the paper's portfolio), pre-registered 2026-09-30

Written before the backtester exists, so the design can't drift toward
the results.

**Design (fixed):**
- **Candidate pool:** every coin tested so far (BTC, ETH, SOL, BNB, XRP, ADA,
  DOGE, LTC, LINK, DOT, AVAX, ATOM, TRX, BCH, ETC, FIL, NEAR, XLM, UNI, LUNC,
  FTT, EOS, XTZ) plus SHIB, ALGO, VET, ICP. Some of these later collapsed or
  faded; that's deliberate. Coins whose history Binance no longer serves are
  skipped and listed. The pool is still "coins listed on Binance today", so
  some survivorship remains.
- **Point-in-time universe:** at the first 4h bar of each calendar month,
  rank the coins that have at least 365 days of history (the ensemble's
  longest lookback, and the paper's "listed ≥1 year") by median daily dollar
  volume over the prior 30 days. Keep the top N. This uses only data
  available at that moment.
- **Sizing:** capital is split into N equal slots. Each held coin's target is
  slot × its donchian_ensemble weight (4h, bars_per_day 6, vol_target 0.25,
  the paper's lookbacks). A coin that drops out of the universe goes to 0.
  Total exposure is ≤ 100%, no leverage.
- **Rebalancing:** each 4h close, per coin. Skip changes smaller than 10% of a
  slot, but always execute going flat. Fee 0.1% + slippage 0.05% on traded
  notional.
- **Windows:** the same train (2020–2023) and test (2024-01-01..holdout)
  windows, with warmup history before each.

**Primary: N = 10.** N = 5 and N = 20 are reported as sensitivity only, not
as a choice.

**Pass criteria:** the N = 10 portfolio's Sharpe beats the BTC ensemble alone
(train 1.45, test 0.90, same summarize() metric) in **both** windows, with
test max drawdown no worse than -20%. A pass makes it a candidate for a
paper run. The holdout stays untouched until then.

### Result (job 38, appended after the run; criteria above unchanged)

All 27 coins had usable history. The monthly universes look right: FTT
and LUNC entered the top-20 while they were liquid, and the Jan-2020 top-10
included EOS, VET and ETC.

| Rotational ensemble (4h) | Train return / DD / Sharpe | Test return / DD / Sharpe | Avg exposure (train / test) |
|---|---|---|---|
| top-5 (sensitivity) | +74.4% / -13.7% / 1.39 | +18.3% / -8.7% / 0.79 | 11% / 14% |
| **top-10 (primary)** | +68.2% / -14.7% / **1.46** | +17.2% / -8.0% / **0.83** | 9% / 12% |
| top-20 (sensitivity) | +47.9% / -11.1% / 1.35 | +13.8% / -7.7% / 0.77 | 7% / 9% |
| BTC ensemble alone (bar) | +128.5% / -17.4% / 1.45 | +30.8% / -12.1% / 0.90 | |

**Primary: FAIL.** Train Sharpe 1.46 vs 1.45 is a tie. Test Sharpe 0.83 is
below BTC-alone's 0.90. Drawdown is fine (-8.0%), but that's not enough on
its own. The sensitivity sizes fail the same way (test 0.77–0.79), so the
result isn't a quirk of N = 10. The low average exposure (~10%) comes from
N slots × per-coin vol targeting. It lowers return and drawdown together
and doesn't change the Sharpe comparison.

**Conclusion of the whole search:** four different ways of adding coins all
fail to beat the BTC 4h ensemble alone on risk-adjusted return in both
windows once survivorship is removed:
- survivor-picked 7 (flattered),
- all 23 equal-weight,
- point-in-time top-10 by market cap,
- the paper's monthly volume-rotated portfolio.

Neither do strategy blends. The multi-coin versions reliably *lower
drawdown*, but at a lower Sharpe in 2024–26, when altcoins were in a bear
market relative to BTC. The BTC 4h ensemble stays the recommended strategy.

## 2026-10-01 — Hypothesis #1: ensemble walk-forward stability (pre-registered)

Registered in the app as hypothesis #1 (family trend/breakout, budget 6
variants) before any result was seen.

- Statement: the 9-lookback Donchian ensemble (fixed parameters from
  Zarattini et al. 2025, bars_per_day 6) has an edge that is stable across
  time, not concentrated in one period.
- Test: anchored walk-forward, 6-month folds from 2022-01-01 to the holdout
  start (2026-03-01, excluded), current backtest costs.
- Runs: donchian_ensemble BTC/USDT 4h (the candidate), donchian_ensemble
  ETH/USDT 4h (cross-check), donchian BTC/USDT 4h (reference).
- Pass (BTC ensemble): joined out-of-sample Sharpe >= 0.8, deflated Sharpe
  probability >= 0.95 counting every donchian_ensemble and trend/breakout
  variant ever recorded, and at least half the folds positive.
- Caveat stated up front: the ensemble was chosen after seeing 2024-2026
  test results, so folds from 2024 on are not fully out of sample for the
  choice of strategy; the 2022-2023 folds and the holdout are the cleaner
  evidence.

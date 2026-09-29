# Holdout validations

One deliberate look per candidate at the reserved window
(`validation.holdout_start`, 2026-03-01 onward). Each entry is written
**before** the check runs: the exact configuration and the pass criteria.
The result is appended afterwards and not edited. A strategy that fails
doesn't get a second look at this window after tweaking. That's the whole
point of reserving it.

## 2026-09-29 — donchian_ensemble (pre-registered before running)

**Why this window is clean for this strategy:** the design comes from
Zarattini et al. (SSRN, April 2025), so its authors couldn't have seen
2026-03 onward. Every research report run here excluded the window, and
no parameter was tuned on this project's data. The paper's parameters are
used verbatim, plus the 10-point rebalance threshold, which was picked for
manual-trading practicality before any result was seen.

**Configuration (exactly what's deployed as `donchian_ensemble_4h` and
`reco_donchian_ensemble_4h`):** `donchian_ensemble`, 4h candles,
`bars_per_day=6`, `rebalance_threshold=0.1`, config defaults otherwise
(lookbacks 5/10/20/30/60/90/150/250/360 days, 25% vol target, 90-day vol
window, max weight 1.0). Fee 0.1% + slippage 0.05% per side. Warmup history
before the window, as in every research report.

**Primary check:** BTC/USDT. **Secondary:** ETH/USDT (same configuration).
Baseline shown for context: `donchian` 4h, also deployed. Its holdout was
already informally touched by the 2023+ exit-width exploration (see
config.yaml), so its line here is context, not a clean validation.

**Pass criteria (primary, BTC 4h), all three:**
1. Total return over the window is positive after costs.
2. Sharpe ratio is positive.
3. Max drawdown is no worse than -20%, in line with train/test (-12% to -20%).

**Reading the secondary (ETH):** confirmation if it also passes. If ETH
fails while BTC passes, the result is "passes, weaker evidence", not a fail.

**What a pass means:** the strategy keeps its paper and recommend runs
and becomes eligible for small real allocations. A pass on ~7 months of
data does not prove an edge. **What a fail means:** it's removed from the
recommend feed. The paper run continues for observation, with no retuning
against this window.

# Strategy watchlist

New strategy ideas from outside sources (YouTube, blogs, papers, other
traders) go here before any code is written, so "keeping up to date" doesn't
mean chasing every viral claim. For each one, write down the claim, the
source, and which parts are mechanical vs. left to interpretation (the
interpretive parts are where most viral strategies hide their edge, or
their lack of one).

Pipeline for an idea that looks worth testing:
1. Pin every discretionary rule to an explicit, sweepable parameter and
   implement it as a `Strategy` (lifecycle stage: `research`).
2. Run the **Research Report** workflow (GitHub → Actions → Research Report →
   Run workflow). The result lands in Telegram as a `RESEARCH_REPORT`;
   paste it back to Claude.
3. Judge it on the **test** line against the baseline, not the train line. A
   good train result with a bad test result is the overfitting pattern this
   project has already hit once (donchian exit widths, spec Section 8).
4. Log the verdict below and in the spec (Section 7), including negative
   results: a tested "doesn't work" is still worth keeping.

| Idea | Source | Status | Verdict |
|---|---|---|---|
| Candle Range Theory (CRT): sweep of prior candle's high/low, close back inside, target the opposite end | Trading Geek (YouTube), "I Tested The Trading Geek Strategy…" | tested 2026-09-29 (1d + 4h) | **Rejected.** On 4h all 6 variants lost heavily in both windows (PF 0.38–0.70, 365–1414 trades, -67% to -99%), with or without trend filters. On 1d: Plain CRT lost in both windows (PF 0.69–0.89, 140–258 trades). With a 50-EMA trend filter it lost on train (PF 0.82–0.84) but made money on test (PF 1.12–1.31, 46–65 trades). Inconsistent, so not deployed. The trend filter, not the sweep, looks like the useful part. Killzone timing is the only untested piece, and on these results not worth building. |
| Donchian **ensemble** (9 lookbacks averaged into one signal) + volatility-targeted sizing (25% annual vol) | Zarattini, Pagani & Barbon, "Catching Crypto Trends" (SSRN 5209907, 2025): BTC since 2015, net Sharpe 1.56, max DD 19% | shortlisted, top pick | — |
| Weekend / "Monday Asia open" seasonality: BTC returns cluster Sunday evening NY time into Monday, and in certain hours (22–23 UTC strong, 03–04 weak) | QuantPedia; Concretum "Seasonality in Bitcoin Intraday Trend Trading" | shortlisted | — |
| Intraday time-series momentum: early-session return predicts late-session return | Univ. of Reading, "Bitcoin intraday time-series momentum" (hourly Gemini data, 2015–2022) | watch only: many small trades, likely eaten by 0.1% fees | — |
| Volatility-managed sizing as an overlay on existing strategies (smaller positions when realized vol is high) | Several academic papers; Grayscale "The Trend is Your Friend" | shortlisted, bundled with the ensemble | — |
| Donchian (existing 20/55 settings) on **4h** instead of 1d | side finding in the CRT 4h research report | lead: profitable in both windows on BTC (train PF 1.68 / 96 trades, test PF 1.48 / 46) **and ETH** (train PF 1.60 / 92, test PF 1.55 / 52, +11.24% while ETH buy&hold was -13.60%), no 4h tuning | shadow run `donchian_4h` started 2026-09-29 |
| Every existing strategy on 4h (regime_switched, funding_filtered, vol_expansion, multi_timeframe, ema_cross, market_structure) | 4h sweep after donchian's 4h result | tested 2026-09-29 | Only the donchian variants were profitable. Plain donchian had the best test result, and the ADX gate and funding veto added nothing. The other four lost in both windows (PF 0.37–0.70). No new shadow runs. |

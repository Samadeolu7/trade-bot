# BTC/USD Custom Trading Bot — Build Specification

**Handoff note for Claude Code:** This is a build spec for a self-hosted BTC/USD signal bot, meant to run unattended on the author's own VPS (which already runs Docker + Traefik). Build in the phases listed in Section 13 — each phase should be independently runnable and testable before moving to the next. Do not skip straight to live trading; alert-only and paper-trading phases come first.

---

## 1. Scope & Goals

- Monitor BTC/USD price action and fire custom signals that aren't available in mainstream free tools (e.g. TradingView's free tier).
- Deliver alerts via Telegram in real time.
- Optionally (later phase, opt-in) support semi-automated or fully automated execution.
- Run entirely on the author's own VPS — $0 recurring cost beyond the VPS itself.
- Config-driven: strategy parameters should be editable without touching code.

## 2. Constraints

- No paid data feeds, no paid backtesting/SaaS tools.
- Must run unattended and recover from crashes (Docker restart policy or systemd).
- Secrets (API keys, bot tokens) via environment variables / `.env`, never hardcoded or committed.
- Note: most crypto exchanges quote **BTC/USDT**, not literal BTC/USD. USDT is functionally equivalent to USD for this purpose.
- **Data vs. custody split (Nigeria-specific):** Binance is used **strictly as a read-only public data source** — historical/live OHLCV and funding-rate data, no API key, no funds ever touch it. All actual order placement and fund custody happens on **Quidax**, a Nigerian SEC-provisionally-licensed exchange with direct NGN bank rails and a bot-friendly REST API. This sidesteps Binance's shifting Nigeria-specific restrictions (no NGN on-ramp since 2024, account-freeze risk, ongoing regulatory disputes) while still getting Binance's much deeper historical data for backtesting. Fee-wise the two are close to equivalent: Quidax charges 0.1% maker/taker on order-book trades, matching Binance's standard spot fee — so this split isn't a cost trade-off, just a risk one.

## 3. Recommended Stack

| Layer | Tool | Why |
|---|---|---|
| Market data | `ccxt` (Binance, read-only) | Free, deep historical OHLCV + funding-rate data, no API key needed for public endpoints |
| Execution | Direct REST client against Quidax's API (not in `ccxt`) | Quidax isn't in `ccxt`'s exchange list, so this is a small custom wrapper — a `requests`-based client around Quidax's documented endpoints (order placement, balances, order book) |
| Data handling | `pandas` | Standard, and the user already knows Python |
| Indicators | `pandas-ta` | Free, wide indicator coverage, no C build dependencies like TA-Lib |
| Backtesting | `vectorbt` (or `backtrader` if preferred) | Free, vectorized, fast enough for iterating on strategy ideas |
| Alerting | Telegram Bot API (`python-telegram-bot` or raw `requests`) | Free, instant, reliable, easy to script |
| Storage | SQLite (upgrade to Postgres only if needed) | Zero-ops, plenty for candle/signal/trade logging at this scale |
| Scheduling / runtime | Long-running Python process in Docker, `unless-stopped` restart policy | Matches the VPS's existing Docker setup |
| Secrets | `.env` + `python-dotenv` | Simple, standard |

## 4. System Architecture

```
[Binance public API, read-only] --> [Data Layer: fetch + store OHLCV] --> [Strategy Engine: indicators + rules]
   (OHLCV + funding rate, no key)                                                |
                                                                                   v
                                                                        [Signal Event: direction, entry, stop, target]
                                                                                   |
                                                       +-----------------------+-----------------------+
                                                       v                                               v
                                             [Telegram Alert]                                [Execution Layer (optional)]
                                                                                                        |
                                                                                                        v
                                                                                          [Quidax order API — custody + execution]

All events logged to SQLite for later review and strategy comparison.
```

## 5. Data Layer

- Use `ccxt`'s **Binance** adapter in **public/read-only mode only** — no API key or secret required for OHLCV candles or funding-rate data, so no Binance account credentials are ever stored or used by the bot.
- Poll or stream OHLCV candles (WebSocket preferred for lower latency, REST polling is fine and simpler to start with — e.g. every 1–5 min depending on the strategy's timeframe).
- Store candles in a local table: `candles(exchange, symbol, timeframe, open_time, open, high, low, close, volume)`.
- Backfill enough historical data (ideally 2–3 years) to backtest across different market regimes, not just recent bull conditions — Binance's history is far deeper than Quidax's, which is why it's used for this layer.
- Respect exchange rate limits — `ccxt` exposes these; don't poll faster than needed.
- **Separately**, backfill Quidax's own BTC/USDT order book depth and recent trade history (via its REST API) so live signal execution is checked against Quidax's actual tradable liquidity, not just Binance's — the two can diverge, especially in size.

## 6. Strategy Engine

- Build a pluggable strategy interface (e.g. a base class with a `generate_signal(df) -> Signal | None` method) so multiple strategies can run side by side and be swapped via config, not code changes.
- Each strategy reads its parameters from a config file (YAML/JSON) — timeframe, indicator periods, thresholds, position sizing rule.
- A `Signal` event should carry: symbol, timeframe, direction (long/short/flat), entry price, stop-loss, take-profit, confidence/reason string, timestamp.
- Add a **market regime filter** (e.g. ADX, realized volatility, or price vs. SMA(200)) that determines which sub-strategy is "live" at any time — this is the part that's genuinely different from a single fixed TradingView alert, since the bot adapts to trending vs. ranging conditions instead of firing one static rule all the time. An independent reproducible backtest (CoinQuant, BTCUSDT daily, 2021–2026, fees included) found an SMA(200) trend filter drove the best risk-adjusted result of the strategies it tested (profit factor 2.02) — worth backtesting as the primary trend/range switch, with ADX as a secondary confirmation.
- **NATR regime filter (implemented) — findings:** normalized ATR (ATR/price) vs. its own trailing history, a different axis from ADX/SMA200 (volatility *expansion* vs. trend *strength*/*extension*), on the premise (7c) that BTC moves in volatility clusters. On BTC/USDT 2020-2023 (donchian trend, flat ranging): NATR gave 10 trades, +9.33% return, -4.77% max DD, 70% win rate, PF 4.90; ADX on the identical window gave 12 trades, +7.52%, -2.74% max DD, 50% win rate, PF 4.93 — nearly identical profit factor despite different mechanics, suggesting donchian's edge here is fairly robust to *which* filter gates it (both largely agree on the same big directional moves) rather than NATR finding something ADX misses. Neither result is trustworthy on its own at 10-12 trades — same sample-size caveat that already burned the `rsi_bb` evaluation — so treat this as "no evidence NATR is better, no evidence it's worse," not a decision either way. Deployed default stays `adx`.

## 7. Candidate Strategies for BTC/USD

None of these are guaranteed profitable — they're starting points to implement, backtest, and tune. Crypto markets shift between regimes often, and a strategy that worked in one period can fail in another. Treat every number below as a first guess to be validated on real data, not a final setting.

**a) Trend-following: EMA cross + ATR trailing stop**
Long when a fast EMA (e.g. 20) crosses above a slow EMA (e.g. 50) on a higher timeframe (4h or 1D), exit via an ATR(14)-based trailing stop (e.g. 2× ATR) rather than a fixed target. Works well in sustained directional moves; tends to whipsaw and lose in choppy, range-bound conditions — pair with the regime filter above so it's only "live" when trend strength (e.g. ADX > ~20–25) confirms a trend.

**b) Mean-reversion: RSI + Bollinger Bands in range regimes**
In detected range-bound conditions (low ADX), fade extremes — e.g. RSI(14) < 30 near the lower Bollinger Band as a long signal, RSI > 70 near the upper band as a short. Works in sideways chop; loses badly if it fires during a real trend, which is why gating it behind a regime filter matters.

**c) Volatility breakout: Bollinger Band squeeze + volume confirmation**
BTC tends to move in volatility clusters — long compression (tight Bollinger Bands / low ATR) often precedes a sharp expansion. Enter in the breakout direction only when volume confirms (e.g. volume > its recent average), with a stop just inside the pre-breakout range. False breakouts are the main failure mode; volume and/or a minimum breakout size filter help reduce these.

**d) Crypto-specific: funding rate mean-reversion**
Unlike traditional markets, perpetual futures funding rates are public and often overextend at sentiment extremes — very high positive funding tends to correlate with over-leveraged longs (short-term pullback risk), and very negative funding with over-leveraged shorts. This is the kind of signal mainstream TradingView alerts don't offer out of the box and can be pulled read-only via `ccxt`'s Binance funding-rate endpoint (Quidax is spot-only, no perpetuals, so this data has to come from Binance). Used as a filter/confirmation input only — the resulting trade still executes on Quidax's spot market, not on Binance.

**e) Trend-following: 20-bar Donchian breakout**
Long when price closes above the highest high of the prior 20 daily bars, exit when it closes below the lowest low of the prior 20 bars. In the same independent reproducible backtest referenced above, this was the single best performer by raw return (+118.4% over 2021–2026 on BTCUSDT, 24 trades, 50% win rate, profit factor 1.45) — but also the largest drawdown of the trend strategies tested (48.6%, concentrated in the 2022 bear market), so it needs the same regime filter and risk sizing as (a). Cheap to implement — it's just a rolling max/min comparison — and worth including alongside (a) as a second trend-following candidate to backtest side by side.

**f) Market-structure breakout + retest (implemented) — findings: weak, not pursued further**
Find the most recent confirmed swing high/low (a fractal — a bar whose high/low is the extreme of several bars on each side of it), wait for a close beyond it (breakout), wait for price to come back and retest that level, then enter on confirmed rejection/continuation. Deliberately a different entry hypothesis from (a)/(e) — those ask "what do the indicators say"; this asks "what is price actually doing." Uses only the most recent confirmed swing point per direction rather than tracking every unresolved one.

On BTC/USDT 2020-2023: best-of-9 in a `swing_window`/`retest_window` sweep was PF 1.22 (67 trades, +9.37% return, -14.5% max DD), with every combo clustered in a narrow, unremarkable PF 1.0-1.22 band — no wild parameter sensitivity (a mild positive, per Section 8's overfitting caveat), but also nothing good anywhere in the grid. Clearly weaker than donchian's PF 3.94 on the identical window. Recommendation: don't deploy or tune further as currently designed — same treatment as `rsi_bb`, a tested and logged negative result rather than an ongoing candidate. The core idea may still have merit with a different rule set (e.g. tracking multiple unresolved swing levels rather than only the most recent, or requiring volume/momentum confirmation on the rejection bar), but that would be a distinct redesign, not a parameter tune of this version.

**Explicitly not included: naive grid trading.** It's the most heavily marketed "profitable" crypto bot strategy, but the claims behind it are almost entirely exchange marketing rather than reproducible backtests, and its mechanics are structurally built for sideways markets — a strong sustained trend drives price outside the grid and leaves it holding unrealized losing positions, the same failure mode that made the Bollinger mean-reversion strategy above the worst performer in the real test (65.7% win rate, only +10.4% return, 52.7% drawdown). If it's added later, it needs a hard "pause during confirmed trend" kill-switch tied to the same regime filter as everything else, not a standalone always-on strategy.

**Evidence note for Claude Code:** strategies (a) and (e) above were validated against an independent, reproducible backtest (CoinQuant, BTCUSDT daily candles, Jan 2021–Aug 2026, Binance 0.1% taker fees included, spans the 2021 bull run, 2022 crash, and 2023–26 cycle). None of the five strategy families tested there beat plain buy-and-hold Bitcoin on raw return over that window — their edge was a much smaller drawdown and far less time exposed to the market, not higher absolute profit. Treat that as the realistic bar: the goal of this bot is a better risk profile than holding, not a guarantee of beating it.
- Risk a small, fixed percentage of capital per trade (commonly 0.5–1%), sized off the stop-loss distance, not a fixed coin amount.
- Cap max concurrent open positions.
- Add a daily/weekly loss circuit-breaker that pauses the bot after a max drawdown threshold is hit, rather than letting it keep trading through a bad stretch.
- Avoid leverage until the strategy has a solid live track record on paper; if used later, keep it conservative.

## 8. Backtesting Requirements

- Include realistic taker fees — use Quidax's actual 0.1% order-book fee, since that's where trades will really execute — plus a slippage assumption. Backtests without fees/slippage are misleading.
- Test across multiple distinct BTC regimes (e.g. the 2020–21 bull run, the 2022 bear market, and a choppy/sideways stretch), not just the most recent months — a strategy that only works in one regime isn't done yet.
- Use walk-forward or out-of-sample validation, not just an in-sample fit — the biggest risk here is curve-fitting parameters to past data that won't hold up going forward.
- Track more than total return: Sharpe ratio, max drawdown, win rate, and profit factor all matter for judging whether a strategy is actually robust.
- **Single-sample-path caveat:** BTC has only one historical price path (~6–7 years of meaningful data). A public study "confirming" a strategy family (Section 6/7's evidence notes) and this project's own backtests both draw on largely the same underlying history — that overlap means they are not independent confirmations of each other, and repeated re-slicing of the same BTC window is not a substitute for genuinely new data. Cross-asset validation (Phase 3.5) is the practical way to get independent evidence without waiting years for fresh candles.
- **Pilot backtest findings (logged here so this isn't re-litigated from scratch):** an initial sweep found `rsi_bb` produces only 2–5 trades across a 4-year window regardless of parameters, with results swinging from +$138 to -$449 on small threshold changes — not enough trades to distinguish a real edge from noise, and it did not beat plain `donchian` run alone on the identical window. Recommendation: don't deploy `rsi_bb` as designed on daily BTC data; treat "stay in cash while ranging, no active mean-reversion" as the more defensible default (adopted — see the strategy engine's default config). Separately, `donchian` with a widened exit channel (channel_period=20, exit_channel_period=55) looked strong in-sample on 2020–23 (PF 3.94, 64.3% win rate) but came back at essentially breakeven out-of-sample on 2024+ (PF 0.91, 50% win rate) — no confirmed edge yet on BTC alone.
- **Phase 3.5 cross-asset results (same fixed rule set, no per-asset tuning, same train/test split):**

  | Asset | Train return / PF | Train buy-hold | Test return / PF | Test max DD | Test buy-hold |
  |---|---|---|---|---|---|
  | BTC/USDT | +7.91% / 3.94 | +487.2% | -0.40% / 0.91 | -3.32% | +80.49% |
  | ETH/USDT | +14.46% / 4.40 | +1644.95% | **+2.59% / 2.21** | -2.62% | +4.65% |
  | SOL/USDT | +84.18% / 17.68 | +2983.83% | -4.32% / 0.30 | -7.03% | -6.27% |

  **Verdict: mixed, not purely BTC-specific.** ETH's out-of-sample result is genuinely encouraging — comfortably profitable (PF 2.21), tiny drawdown, and it meaningfully beat a nearly-flat buy-and-hold in that window. BTC is roughly breakeven. SOL clearly fails (PF 0.30) even though it also lost less than its own buy-and-hold there. Two of three assets clear the Phase 4 acceptance bar (Section 13) out-of-sample and one doesn't — this reads as a weak, asset-dependent mechanism rather than either a robust cross-asset edge or a pure 2020-23-BTC artifact. Consistent with the spec's own framing: worth shadow-testing at alert-only risk, not worth trusting with capital yet.
- **Holdout enforcement (methodology fix):** the "out-of-sample" test above wasn't actually clean — the donchian exit-channel-width exploration (30/55/70/90) was run against 2023-01-01 onward with no end date, overlapping almost entirely with the same window later used as the 2024+ holdout. That's exactly the parameter-sensitivity/overfitting risk this section already warns about, just self-inflicted. Going forward, `config.yaml`'s `validation.holdout_start` marks a reserved window: `sweep` can never touch it (no override), `backtest` excludes it unless `--allow-holdout` is explicitly passed, and every deliberate use is logged to `notes/holdout_validations.md`. This doesn't retroactively clean the data already looked at — it makes the mistake structurally harder to repeat for any new strategy work from here on.

## 9. Alerting Layer

- Create a bot via Telegram's BotFather, grab the bot token and the target chat ID.
- Alert types: signal fired (with entry/stop/target/reason), daily summary, and a heartbeat/error alert so the user knows if the bot process dies — a silent bot is worse than no bot.
- Keep the message format consistent and parseable (structured, not free text) in case it's parsed programmatically later.

## 10. Execution Layer (Optional, later phase) — Quidax

- Build a thin custom REST client against Quidax's documented API (`https://www.quidax.com/developers/api_v2`) for: account/balance lookup, order-book snapshot, placing/cancelling orders. This is the one piece that can't reuse `ccxt`, since Quidax isn't in its exchange list.
- Generate the API key from Quidax's Developer Settings, scoped to trading only if Quidax's key permissions allow it — **no withdrawal permission on the bot's key**, ever. Withdrawals stay a manual, human action.
- Start in "alert + manual confirm" mode (bot proposes a trade, user confirms via Telegram reply) before enabling full auto-execution.
- Prefer limit orders over market orders on Quidax's order book to control slippage (note: order-book trades get the 0.1% fee; Quidax's "instant swap" convenience feature charges 1% and should be avoided by the bot).
- Keep the same daily-loss circuit breaker from Section 7 active at the execution layer too, as a second safety net.
- Log every order request/response verbatim (minus the API key) — useful for debugging Quidax-specific quirks since its API is far less documented/battle-tested than Binance's.

## 11. Storage & Logging

- SQLite tables: `candles`, `signals`, `trades`, `equity_curve`.
- Structured logs (not just print statements) with rotation so the VPS disk doesn't fill up over months of unattended running.

## 12. Deployment on the VPS

- Package as a Docker service alongside the existing Docker/Traefik setup — no need to expose it via Traefik unless a status dashboard is added later.
- Restart policy: `unless-stopped`.
- All secrets via environment variables injected at deploy time, not baked into the image.
- Optional: a lightweight health-check/status endpoint for monitoring.

## 13. Suggested Build Order

1. **Phase 1 — Data**: Binance read-only connectivity via `ccxt`, OHLCV storage, historical backfill. No exchange account credentials needed yet.
2. **Phase 2 — Strategy + Backtest**: pluggable strategy interface, at least strategies (a) and (b) above, backtesting harness with Quidax's real fee (0.1%) and a slippage assumption, across multiple regimes.
3. **Phase 3 — Alerting**: Telegram integration, paper-mode signal alerts only (no execution).
3.5. **Phase 3.5 — Cross-asset validation**: before committing to a shadow run, re-run the exact fixed rule set that came out of Phase 2 (no re-tuning) on ETH/USDT and 1–2 other liquid majors, same train/test windows. BTC only has one historical path, so repeated backtests against it are not independent evidence of anything — this is the fastest way to get genuinely independent test data without waiting years for fresh BTC candles. A directionally similar result across assets is real evidence of a mechanism; a result that only works on BTC's specific 2020–23 window is evidence of an artifact.
4. **Phase 4 — Shadow run**: let it run live on the VPS in paper/alert-only mode for a few weeks, compare real-time behavior against backtest expectations before trusting it. **Acceptance bar**: grade this against "holds up near breakeven while meaningfully cutting drawdown and time-in-market versus holding," not "beats buy-and-hold" — everything tested so far points to an edge somewhere between roughly zero and modest, and grading against outperformance sets up a comparison the strategy was never shown to support.
   - **Concurrent shadow runs (2026-09-07)**: rather than testing one candidate at a time and switching linearly once each finishes gathering data, the shadow-run infrastructure supports multiple strategies running concurrently against the same candle data, each with fully independent paper-trading state (`signals`/`paper_position`/`paper_trades` all keyed by `strategy_label`, not shared capital). `python main.py shadow --strategy <name> --strategy-label <label>` runs any registered strategy standalone; `docker-compose.yml` runs the deployed control (`donchian_adx_control`) alongside `multi_timeframe` and `donchian_natr_regime` as separate services sharing one SQLite file (WAL mode) and one Telegram chat (every alert is tagged `strategy=<label>`). New hypotheses can be added as another compose service without touching the others.
5. **Phase 5 — Execution (optional)**: build the Quidax REST client, open a Quidax account + API key (trading scope only), manual-confirm mode first, full auto only after Phase 4 has proven out and the circuit-breaker/risk controls are tested.

## 14. Disclaimer

This spec is a technical build plan, not financial advice. No strategy here is guaranteed to be profitable — crypto markets are volatile and regimes change. Backtest thoroughly, paper-trade before risking capital, and only trade with money you can afford to lose. As of the most recent pilot testing (Section 8), none of the configurations tried so far have produced a result that clearly survives out-of-sample validation — that's expected at this stage, not a reason to abandon the approach, but it means Phase 5 (real execution) should stay gated behind Phase 3.5 and a genuine Phase 4 shadow run, not opened on the strength of an in-sample backtest alone.

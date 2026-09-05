# BTC/USD Signal Bot

Build spec: [btc-usd-bot-spec.md](btc-usd-bot-spec.md). This repo is being built phase-by-phase per Section 13.

## Phase 1 — Data Layer

Binance read-only OHLCV data via `ccxt`, stored in SQLite. No exchange account credentials needed.

### Setup

```
pip install -r requirements.txt
```

### Backfill historical candles

```
python main.py backfill --symbol BTC/USDT --timeframe 1h --start 2020-01-01T00:00:00Z
```

Safe to re-run — resumes from the latest stored candle instead of re-fetching from scratch.
Pass `--no-resume` to force fetching from `--start` regardless of what's already stored (e.g. if
`poll` has already seeded a few recent rows for this symbol/timeframe and you now want full history).

### Poll for live candles

```
python main.py poll --symbol BTC/USDT --timeframe 1h
```

Runs forever, fetching the latest candle every `poll.interval_seconds` (see `config/config.yaml`). Ctrl+C to stop.

### Config

`config/config.yaml` — exchange/symbol, backfill start date, poll interval, SQLite path, logging.

### Tests

```
python -m pytest tests/ -v
```

## Phase 2 — Strategy + Backtest

A pluggable `Strategy` interface (`bot/strategy/base.py`) plus:

- `ema_cross` — trend-following EMA cross with an ATR trailing stop (spec 7a).
- `rsi_bb` — RSI/Bollinger mean-reversion, fading extremes back to the midline (spec 7b).
- `donchian` — trend-following breakout of the prior N-bar high/low channel (spec 7e). Entry and
  exit use separate channel periods (`channel_period`/`exit_channel_period`) — a single shared
  period means a routine pullback within a trend can breach the same-width exit channel, closing
  the position on normal chop rather than a real reversal. `exit_method: atr` is an alternative to
  the wider-channel exit: an ATR-based trailing stop (the same mechanism `ema_cross` uses), sized
  off actual volatility rather than a second lookback window. Neither is a settled choice — both
  the exit channel width and exit method are free parameters to sweep (`--exit-channel-period`,
  `--exit-method`, `--donchian-atr-period`, `--donchian-atr-mult`), not values to anchor on because
  they sound familiar from a well-known system.
- `regime_switched` — a `RegimeFilter` (spec Section 6) that runs a trend strategy while "trending"
  and `rsi_bb` while "ranging", so the bot adapts instead of firing one static rule. Which trend
  strategy (`ema_cross`/`donchian`) and which regime filter (`adx`/`sma200`) are config-driven
  (`strategy.regime_switched.trend_strategy`, `strategy.regime.type` in `config/config.yaml`), with
  `--trend-strategy`/`--regime-type` as one-off CLI overrides for comparing combos without editing
  the file baked into the Docker image. An independent backtest found `sma200` the stronger
  risk-adjusted primary switch, with ADX as secondary confirmation (`strategy.regime_sma`) — worth
  comparing against `adx`. `rsi_bb`'s own `--rsi-oversold`/`--rsi-overbought`/`--bb-std`/
  `--stop-band-mult` are also CLI-overridable, for the same reason.

`regime_switched` results include a `by_strategy` breakdown (trade count/win rate/profit factor/
total pnl per sub-strategy) whenever more than one fired. This is what actually tells you whether
the mean-reversion side has an edge in the conditions the regime filter selected it for — its
*standalone*, ungated numbers include periods it was never designed to trade in (spec 7b explicitly
expects it to lose during a real trend), so they're not a fair test on their own.

Indicators (EMA/RSI/Bollinger/ATR/ADX) are hand-implemented in `bot/indicators/` rather than via
`pandas-ta`/`vectorbt` as the spec's stack table suggests — avoids adding a numba/JIT dependency to
a slim Docker image after the dependency-install pain earlier in this project, and the formulas are
simple enough to implement and unit-test directly. The backtest engine (`bot/backtest/engine.py`) is
a plain bar-by-bar simulator rather than `vectorbt`/`backtrader`, for the same reason — fast enough
for iterating on a few strategies over a few years of hourly data without a compiled dependency.

### Backtest a strategy

```
python main.py backtest --strategy regime_switched --timeframe 1h --start 2020-01-01T00:00:00Z
```

`--strategy` is `ema_cross`, `rsi_bb`, `donchian`, or `regime_switched`. Requires candles already stored via
`backfill`. Prints trade count, total return, max drawdown, Sharpe ratio, win rate, profit factor,
and buy-and-hold return over the same window — fees (0.001, Quidax's real taker fee) and slippage
(0.0005) are applied per trade, per spec Section 8. Strategy/backtest parameters live under
`strategy:`/`backtest:` in `config/config.yaml`.

The buy-and-hold number matters: spec Section 7's evidence note says none of the reference
strategies actually beat it on raw return — their edge was a better risk profile (smaller
drawdown, less time exposed), not higher absolute profit. A backtest result only means something
next to that baseline, not in isolation.

### Sweep a parameter grid

```
python main.py sweep --strategy regime_switched --timeframe 1d --start 2023-01-01T00:00:00Z \
  --trend-strategy donchian --regime-type adx \
  --param rsi_bb.rsi_oversold=20,25,30 --param rsi_bb.stop_band_mult=0.5,0.75,1.0 \
  --rank-by profit_factor
```

Grid-searches the cartesian product of every `--param section.key=v1,v2,...` (repeatable), running
one backtest per combination and printing a table ranked by `--rank-by` (default `profit_factor`).
Beats typing out a `docker compose run` per combination by hand — any `strategy.*` parameter in
`config/config.yaml` can be swept this way, not just the ones with dedicated `backtest` flags.

Per spec Section 8, don't judge a strategy from one date range — rerun `--start`/`--end` across a
few distinct regimes (e.g. the 2020-21 bull run, the 2022 bear market, a choppy stretch) before
trusting a result.

Also test on the timeframe the strategy was actually designed for. `ema_cross`/`donchian` are meant
for 4h/1D (spec 7a) — run at 1h and they overtrade badly: many more small-edge trades compounds
multiplicatively into a much worse result than the same strategy on daily candles, even with
correct risk-based position sizing. This isn't a bug, it's what trading that often with that little
edge actually does to an account — remember to `backfill --timeframe 4h`/`1d` before backtesting
on them.

## Phase 3.5 — Cross-Asset Validation

BTC has only one historical price path (~6-7 years) — repeated backtests against it, and public
studies that draw on the same history, aren't independent confirmation of anything (spec Section
8). Before shadow-running, the exact fixed `donchian` rule set from Phase 2
(`channel_period=20, exit_channel_period=55`, no per-asset tuning) was re-run on ETH/USDT and
SOL/USDT, same train (2020-2023) / test (2024+) split as BTC. Results logged in
`btc-usd-bot-spec.md` Section 8 and `notes/cross_asset_results.md`.

**Verdict: mixed.** ETH held up out-of-sample (PF 2.21, beat a nearly-flat buy-and-hold). BTC was
roughly breakeven (PF 0.91). SOL clearly failed (PF 0.30). Two of three assets clear the Phase 4
acceptance bar out-of-sample, one doesn't — evidence of a weak, asset-dependent mechanism, not a
robust cross-asset edge and not purely a BTC-2020-23 artifact either. Worth shadow-testing at
alert-only risk, not worth trusting with capital yet.

Also per the pilot findings: `rsi_bb` produced too few trades (2-5 over 4 years) to distinguish a
real edge from noise even when correctly gated, and didn't beat `donchian` run alone. It's been
removed from the default active path — `regime_switched`'s "ranging" sub-strategy now defaults to
`flat` (stay in cash, no active mean-reversion) rather than `rsi_bb`, which remains available via
`strategy.regime_switched.ranging_strategy: rsi_bb` / `--ranging-strategy rsi_bb` for future
experimentation.

## Phase 3 — Alerting + Phase 4 — Shadow Run (current)

```
python main.py shadow
```

Runs the finalized strategy (`regime_switched`: `donchian` while trending, flat while ranging,
per Phase 3.5 above) live against real Binance data, forever, on `poll.interval_seconds`. Paper
trading only — no orders are placed anywhere, no Quidax involved at all at this stage. Each
iteration:

1. Refreshes candles (resumable — survives restarts/downtime without leaving a gap).
2. Drops the still-forming candle before evaluating anything — signals/exits only ever act on a
   fully completed bar, so live behavior can't diverge from what the backtest modeled by reacting
   to a candle that's still changing intraday.
3. Manages any open paper position (trailing stop, then checks for a stop/target hit) or looks for
   a new entry if flat — reusing `bot/backtest/engine.py`'s `open_position`/`check_exit`/
   `close_position` directly rather than a separate implementation, so live and backtested
   exit/pnl math can't silently drift apart.
4. Sends a Telegram alert on every signal fired and every position closed (entry/stop/target/
   reason, or exit price/pnl%/reason), a daily summary (open position, trades today/all-time,
   cumulative pnl%), and a heartbeat once per `alerting.heartbeat_interval_seconds` (default daily)
   so a dead process doesn't fail silently (spec Section 9). Messages are structured `key=value`
   lines, not free text, in case they need parsing later.

All signals, paper trades, and the currently-open paper position are logged to SQLite
(`signals`/`paper_trades`/`paper_position` tables) for later review — per spec Section 11.

If `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` aren't set in `.env`, alerts are logged at WARNING and
dropped rather than sent — the shadow run still works (paper trades still get tracked in SQLite),
you just won't get notified until those are set.

## Deployment

Mirrors the build/push/SSH-deploy pattern used in this account's other VPS projects:

- `.github/workflows/deploy.yml` builds the Docker image, pushes it to Docker Hub, then SSHes into the VPS to `docker compose pull && up -d` at `/opt/btc-trade-bot`.
- `docker-compose.yml` runs the bot as a single `unless-stopped` service (`python main.py shadow`), with named volumes for `data/` (SQLite) and `logs/`. No Traefik routing — this process has no web port.
- Required GitHub Actions secrets: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`.
- `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` go in `/opt/btc-trade-bot/.env` on the VPS (see `.env.example`) — not committed, not baked into the image.
- Historical backfill is a one-off, not the long-running `shadow` command — run it manually against the deployed container, e.g.:
  ```
  docker compose run --rm trade-bot python main.py backfill --symbol BTC/USDT --timeframe 1d --start 2020-01-01T00:00:00Z
  ```

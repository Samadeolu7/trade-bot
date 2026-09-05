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

## Phase 2 — Strategy + Backtest (current)

A pluggable `Strategy` interface (`bot/strategy/base.py`) plus:

- `ema_cross` — trend-following EMA cross with an ATR trailing stop (spec 7a).
- `rsi_bb` — RSI/Bollinger mean-reversion, fading extremes back to the midline (spec 7b).
- `regime_switched` — an ADX-based `RegimeFilter` (spec Section 6) that runs `ema_cross` while
  "trending" and `rsi_bb` while "ranging", so the bot adapts instead of firing one static rule.

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

`--strategy` is `ema_cross`, `rsi_bb`, or `regime_switched`. Requires candles already stored via
`backfill`. Prints trade count, total return, max drawdown, Sharpe ratio, win rate, and profit
factor — fees (0.001, Quidax's real taker fee) and slippage (0.0005) are applied per trade, per
spec Section 8. Strategy/backtest parameters live under `strategy:`/`backtest:` in
`config/config.yaml`.

Per spec Section 8, don't judge a strategy from one date range — rerun `--start`/`--end` across a
few distinct regimes (e.g. the 2020-21 bull run, the 2022 bear market, a choppy stretch) before
trusting a result.

## Deployment

Mirrors the build/push/SSH-deploy pattern used in this account's other VPS projects:

- `.github/workflows/deploy.yml` builds the Docker image, pushes it to Docker Hub, then SSHes into the VPS to `docker compose pull && up -d` at `/opt/btc-trade-bot`.
- `docker-compose.yml` runs the bot as a single `unless-stopped` service (`python main.py poll`), with named volumes for `data/` (SQLite) and `logs/`. No Traefik routing — this process has no web port.
- Required GitHub Actions secrets: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`.
- Phase 1 needs no runtime secrets, so `/opt/btc-trade-bot/.env` on the VPS can start empty — later phases (Telegram, Quidax) will add keys there (see `.env.example`).
- Historical backfill is a one-off, not the long-running `poll` command — run it manually against the deployed container, e.g.:
  ```
  docker compose run --rm trade-bot python main.py backfill --symbol BTC/USDT --timeframe 1h --start 2020-01-01T00:00:00Z
  ```

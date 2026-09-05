# BTC/USD Signal Bot

Build spec: [btc-usd-bot-spec.md](btc-usd-bot-spec.md). This repo is being built phase-by-phase per Section 13.

## Phase 1 — Data Layer (current)

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

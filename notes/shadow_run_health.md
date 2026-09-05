# Shadow run health check (automated 2026-09-05T20:36:11Z)

## Clearing stale bot_state markers (last_heartbeat_at, last_summary_date)
```
remaining bot_state rows: []
```

## .env keys present (values redacted)
```
TELEGRAM_BOT_TOKEN=<redacted>
TELEGRAM_CHAT_ID=<redacted>
```

## docker compose ps
```
NAME            IMAGE                             COMMAND                  SERVICE     CREATED         STATUS         PORTS
btc_trade_bot   samadeolu7/btc-trade-bot:latest   "python main.py shad…"   trade-bot   2 minutes ago   Up 2 minutes   
```

## Last 200 log lines (trade-bot)
```
btc_trade_bot  | 2026-09-05 20:33:42,002 INFO bot.shadow.runner: starting shadow run for BTC/USDT 1d every 300s (paper trading only — no orders placed)
```

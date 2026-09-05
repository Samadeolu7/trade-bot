# Shadow run health check (automated 2026-09-05T20:23:14Z)

## .env keys present (values redacted)
```
TELEGRAM_BOT_TOKEN=<redacted>
```

## docker compose ps
```
NAME            IMAGE                             COMMAND                  SERVICE     CREATED          STATUS          PORTS
btc_trade_bot   samadeolu7/btc-trade-bot:latest   "python main.py shad…"   trade-bot   16 minutes ago   Up 16 minutes   
```

## Last 200 log lines (trade-bot)
```
btc_trade_bot  | 2026-09-05 20:06:58,000 WARNING __main__: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set in .env — shadow run continues, but alerts will only be logged, not sent
btc_trade_bot  | 2026-09-05 20:06:58,001 INFO bot.shadow.runner: starting shadow run for BTC/USDT 1d every 300s (paper trading only — no orders placed)
```

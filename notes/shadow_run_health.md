# Shadow run health check (automated 2026-09-05T19:34:49Z)

## docker compose ps
```
NAME            IMAGE                                                                     COMMAND                  SERVICE     CREATED         STATUS         PORTS
btc_trade_bot   sha256:cccd686c90c8f2176a1eec8e277ce43d5bbf035d0ec16096410155bbbc5857fa   "python main.py shad…"   trade-bot   3 minutes ago   Up 3 minutes   
```

## Last 150 log lines (trade-bot)
```
btc_trade_bot  | 2026-09-05 19:31:24,089 WARNING __main__: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set in .env — shadow run continues, but alerts will only be logged, not sent
btc_trade_bot  | 2026-09-05 19:31:24,090 INFO bot.shadow.runner: starting shadow run for BTC/USDT 1d every 300s (paper trading only — no orders placed)
btc_trade_bot  | 2026-09-05 19:31:28,550 WARNING bot.alerting.telegram: Telegram not configured — dropped alert:
btc_trade_bot  | DAILY_SUMMARY
btc_trade_bot  | symbol=BTC/USDT
btc_trade_bot  | timeframe=1d
btc_trade_bot  | position=flat
btc_trade_bot  | trades_today=0
btc_trade_bot  | trades_all_time=0
btc_trade_bot  | cumulative_pnl_pct=0.00
btc_trade_bot  | 2026-09-05 19:31:28,558 WARNING bot.alerting.telegram: Telegram not configured — dropped alert:
btc_trade_bot  | HEARTBEAT
btc_trade_bot  | symbol=BTC/USDT
btc_trade_bot  | timeframe=1d
btc_trade_bot  | status=alive
```

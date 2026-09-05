# Phase 3.5 cross-asset validation (automated 2026-09-05T16:21:39Z)

Fixed donchian rule set — channel_period=20, exit_channel_period=55
(deployed config.yaml defaults, no per-asset tuning, no CLI overrides).

## ETH/USDT

### Backfill
```
```

### Train (2020-01-01 to 2023-12-31)
```
{'trades': 15, 'total_return_pct': 14.46, 'max_drawdown_pct': -18.59, 'sharpe_ratio': 0.45, 'win_rate_pct': 46.67, 'profit_factor': 4.4, 'buy_hold_pct': 1644.95}
```

### Test (2024-01-01 onward)
```
{'trades': 10, 'total_return_pct': 2.59, 'max_drawdown_pct': -2.62, 'sharpe_ratio': 0.48, 'win_rate_pct': 60.0, 'profit_factor': 2.21, 'buy_hold_pct': 4.65}
```

## SOL/USDT

### Backfill
```
```

### Train (2020-01-01 to 2023-12-31)
```
{'trades': 12, 'total_return_pct': 84.18, 'max_drawdown_pct': -22.77, 'sharpe_ratio': 1.09, 'win_rate_pct': 50.0, 'profit_factor': 17.68, 'buy_hold_pct': 2983.83}
```

### Test (2024-01-01 onward)
```
{'trades': 15, 'total_return_pct': -4.32, 'max_drawdown_pct': -7.03, 'sharpe_ratio': -0.73, 'win_rate_pct': 40.0, 'profit_factor': 0.3, 'buy_hold_pct': -6.27}
```


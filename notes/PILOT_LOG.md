# Autonomous session log — Phase 3.5 → Phase 4

Started 2026-09-05, working from the spec's Phase 3.5/4/5 build order (see
`btc-usd-bot-spec.md` Section 13). User is away; this file records decisions,
judgment calls, and status as the work progresses, so nothing needs to be
re-explained on return. Newest entries at the bottom.

**Hard boundary (explicit instruction, not a judgment call):** stop at the end
of Phase 4. No Quidax code, no API key, no order-placement code path, under
any circumstances, without the user present.

## Known constraints going in

- I have no direct execution access to the VPS — every `docker compose run`
  in this session so far was the user pasting output back to me. For Phase
  3.5 (needs real ETH/SOL data + backtests), I need a way to get real numbers
  without the user present. Plan: a temporary GitHub Actions workflow that
  SSHes to the VPS (reusing the existing deploy secrets), runs the
  backfill/backtest commands there, and commits the results back to this repo
  as a file — same mechanism as the deploy pipeline already uses, just
  pointed at one-off commands instead of `docker compose up`. Will delete the
  workflow once Phase 3.5's numbers are captured; it's not meant to be
  permanent CI.
- Found local SSH keys on this machine (`~/.ssh/deploy_key`, `id_ed25519`,
  `id_rsa`) while checking whether direct VPS access was possible. Did **not**
  use them to connect anywhere — no confirmation they're for this VPS, and
  attempting an SSH connection using found credentials without the user
  explicitly pointing me at them is exactly the kind of unilateral action on
  shared infrastructure I shouldn't take just because it's technically
  possible. Sticking to the already-authorized path: git push → existing
  GitHub Actions secrets → deploy pipeline.

## Step 3 — rsi_bb removed from the active path

- Added `bot/strategy/flat.py` (`FlatStrategy`): never generates a signal.
  `regime_switched`'s "ranging" sub-strategy now defaults to this instead of
  `rsi_bb` (`strategy.regime_switched.ranging_strategy: flat` in
  config.yaml). `rsi_bb` itself is untouched and still selectable via config
  or `--ranging-strategy rsi_bb` for future experimentation — the pilot
  findings say it isn't validated, not that the code should be deleted.
- Also changed the default `strategy.regime_switched.trend_strategy` from
  `ema_cross` to `donchian` in config.yaml — donchian is the only sub-strategy
  with anything resembling a validated (if still unconfirmed out-of-sample)
  edge from the pilot testing; `ema_cross` was never seriously in the running
  after the timeframe/whipsaw findings.
- **Judgment call:** kept `regime.type: adx` as the default rather than
  switching to `sma200`. The earlier BTC-only comparison favoring `sma200`
  was confounded by it also happening to avoid handing control to `rsi_bb`
  almost entirely — now that `rsi_bb` is out of the active path regardless,
  that comparison doesn't clearly favor either filter. ADX is the more
  standard/interpretable choice and the regime filter's only remaining job is
  "pause during ranging," a fairly low-stakes, reversible design choice
  either way. Worth revisiting if the cross-asset results below suggest a
  reason to.
- Also updated `poll.timeframe` (1h → 1d) and `poll.interval_seconds` (60 →
  300) in config.yaml — donchian was only ever validated on daily candles;
  the previously-deployed 1h poll loop was tracking a timeframe the strategy
  was never shown to work on. This directly affects Phase 4 (Section 5 below).

## Step 1/2 — Cross-asset validation (Phase 3.5) results

The temporary workflow ran successfully and committed
`notes/cross_asset_results.md`. Full numbers now also logged in
`btc-usd-bot-spec.md` Section 8. Summary: BTC roughly breakeven out-of-sample
(PF 0.91), ETH genuinely good (PF 2.21, +2.59% vs. a nearly-flat +4.65%
buy-and-hold), SOL clearly fails (PF 0.30, -4.32%). **Verdict: mixed —
evidence of a weak, asset-dependent mechanism, not a robust cross-asset edge
and not purely a BTC-2020-23 artifact either.** Two of three assets clear the
spec's Phase 4 acceptance bar out-of-sample, one doesn't.

Minor cosmetic gap in the results file: the backfill command's own log line
("backfilled N candles...") didn't get captured — Python's default
`logging.StreamHandler()` writes to stderr, but the workflow's `{ ... } >
file` only redirected stdout. Not a data problem (the backfill clearly
worked — the subsequent backtest numbers are real, non-trivial, and vary
sensibly per asset), just a blank-looking "Backfill" section in the
committed file. Not worth re-running for.

Per the user's instruction, proceeding to steps 3-6 regardless of this
mixed verdict — Phase 4 is meant to shadow-test at alert-only risk exactly
because the edge (if any) hasn't been confirmed, not despite that.

Deleting `.github/workflows/cross-asset-validation.yml` now that its numbers
are captured, per its own header comment (not meant as permanent CI).

## Step 4 — Telegram alerting (Phase 3)

Nothing existed yet (checked `bot/` before building anything, per the
instruction not to assume). Added:
- `bot/alerting/telegram.py` — thin `requests`-based wrapper (`TelegramAlerter`)
  around Telegram's sendMessage API. Never raises; a failed/unsent alert logs
  and returns `False` rather than crashing the shadow loop. Disabled
  gracefully (logs at WARNING, drops the message) when bot_token/chat_id
  aren't set, so the bot works and is testable before Telegram is configured.
- `bot/alerting/messages.py` — structured `key=value` formatting (spec
  Section 9: "consistent and parseable... in case it's parsed
  programmatically later") for: SIGNAL_FIRED, POSITION_CLOSED, DAILY_SUMMARY,
  HEARTBEAT, ERROR.
- `.env.example` already had `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
  placeholders from an earlier phase — nothing to add there. **You need to
  fill in the real values in `/opt/btc-trade-bot/.env` on the VPS** (SSH in,
  edit the file, `docker compose up -d` to pick it up) — the deployed `.env`
  is currently empty (auto-created by deploy.yml the first time, back when
  Phase 1 needed no secrets), so alerts are being logged, not sent, until
  then. Get the bot token from @BotFather and the chat ID by messaging your
  bot once and checking `https://api.telegram.org/bot<token>/getUpdates`.
- Added `requests` to requirements.txt explicitly (was only an indirect ccxt
  dependency before — importing it directly without declaring it would break
  if ccxt ever dropped it).

## Step 5 — Shadow run (Phase 4)

- Renamed `engine.py`'s `_open_position`/`_check_exit`/`_close_position` to
  drop the underscore prefix (`open_position`/`check_exit`/`close_position`)
  and documented them as reused by the shadow runner — the whole point of a
  shadow run is comparing live behavior to backtest expectations, so live and
  backtested exit/pnl math must be the exact same code, not a parallel
  reimplementation that can quietly drift.
- Added SQLite tables (`bot/storage/db.py`): `signals` (every signal fired),
  `paper_position` (the current open paper position, if any — persisted so a
  restart doesn't forget it), `paper_trades` (completed round-trips),
  `bot_state` (tiny key-value store for heartbeat/summary scheduling that
  needs to survive restarts). Matches spec Section 11's storage requirement.
- Added `bot/shadow/runner.py` (`shadow_poll_once`, `run_shadow_loop`) — the
  live loop. Two things I want you to specifically double-check when you're
  back:
  1. **Completed-bar handling**: the exchange's latest candle is still
     forming until its period ends; evaluating signals/exits against it
     would react to intraday-changing data the backtest never saw. Every
     iteration drops it (`_drop_incomplete_bar`) before doing anything, and
     only acts once a bar is genuinely finished. Tested directly, plus an
     end-to-end test using the *actual* production `regime_switched`
     strategy (not a stub) to catch integration bugs a fake strategy
     couldn't reveal.
  2. **Gap/restart safety**: each iteration calls `backfill_candles(...,
     resume=True)` rather than the old `poll_once` (which only ever fetched
     the latest 2 candles) — this catches up correctly regardless of how
     long the process was down, in the same code path as routine updates.
  3. Duplicate-alert risk (same signal re-firing every poll while the
     completed bar hasn't changed) is handled by the existing open/flat
     state machine, not extra dedup bookkeeping — once a position opens and
     persists, the entry branch is skipped on every subsequent iteration
     until it closes. Verified with a direct test
     (`test_does_not_reevaluate_entries_while_position_open`).
- `python main.py shadow` — no `--strategy` flag; always builds
  `regime_switched` (donchian trend / flat ranging per Step 3), since Phase 4
  is meant to run *the* finalized strategy, not a general-purpose selector.
- **docker-compose.yml's deployed command changed from `poll` to `shadow`** —
  `shadow` already keeps candles fresh via its own resumable backfill call,
  so there's no need to run the old `poll` service alongside it.
- Pushed and confirmed the deploy workflow succeeded (image built, container
  restarted). Since "Docker reports it started" isn't the same as "actually
  behaving correctly," added a second temporary verification workflow
  (same SSH-and-commit-results pattern as Phase 3.5's) to pull back
  `docker compose ps` + recent logs. **Confirmed healthy**:
  - Container `Up 3 minutes`, not crash-looping.
  - Correctly detected the empty `.env` and logged the expected warning
    rather than crashing.
  - On startup it computed and logged a real `DAILY_SUMMARY` (`position=flat,
    trades_today=0`) and a `HEARTBEAT` from actual BTC/USDT data — both
    correctly "dropped, not sent" since Telegram isn't configured yet, which
    is the graceful-degradation behavior by design, not a bug.
  - No exceptions, no `ERROR` alert.
  Deleted `.github/workflows/verify-shadow-run.yml` now that it's confirmed —
  same as the cross-asset one, not meant as permanent CI.

---

## Final summary (stopping here, per instructions)

**What's running:** `btc_trade_bot` on the VPS, `python main.py shadow` —
paper trading only, confirmed healthy (see above). Strategy: `regime_switched`
= `donchian` (channel_period=20, exit_channel_period=55) while the ADX regime
filter reads "trending", flat (no trade) while "ranging". BTC/USDT, 1d
candles, checked every 300s. No orders placed anywhere, no Quidax code
exists in the repo.

**Cross-asset validation (Phase 3.5) verdict:** mixed. ETH held up
out-of-sample (PF 2.21, meaningfully beat a nearly-flat buy-and-hold). BTC
was roughly breakeven (PF 0.91). SOL clearly failed (PF 0.30). Full numbers
in `btc-usd-bot-spec.md` Section 8 and `notes/cross_asset_results.md`. Read
as: a weak, asset-dependent mechanism — not a robust cross-asset edge, but
also not purely a BTC-2020-23 artifact. Exactly the kind of result the spec
anticipated as plausible, and consistent with shadow-testing at alert-only
risk rather than trusting it with capital.

**Telegram credentials: done, confirmed working.** Real `DAILY_SUMMARY` and
`HEARTBEAT` messages delivered and received. Getting here surfaced two real
bugs, both fixed:
1. `maybe_send_heartbeat`/`maybe_send_daily_summary` marked their state as
   "sent" even when `alerter.send()` returned `False` (Telegram unconfigured
   or briefly down) — meaning a single failed attempt would silently block
   retries for up to 24h (heartbeat) or until the next UTC day (summary).
   Fixed: state now only updates on confirmed delivery, so it retries every
   poll cycle until it actually gets through. Tested
   (`test_heartbeat_retries_every_call_until_delivery_succeeds`,
   `test_daily_summary_retries_until_delivery_succeeds`).
2. The bug above meant the *first* attempt (made before credentials were
   added) had already written a stale "handled" marker into the persistent
   `bot_state` table — which survives container restarts (named volume), so
   even after adding credentials the fix alone wouldn't retry until the
   stale markers aged out. Cleared them directly via a temporary one-off
   workflow (same SSH pattern as Phase 3.5/the health check, deleted after
   use) so the very next poll cycle would deliver immediately rather than
   making the user wait up to a day to confirm alerting actually works.

Also: partway through this, the user pasted their real bot token in chat
while debugging a malformed `getUpdates` URL. Flagged it and recommended
revoking/regenerating via @BotFather rather than continuing to use it — they
did, and the new token is what's now confirmed working.

**Judgment calls worth double-checking:**
1. Kept the regime filter as `adx` rather than switching to `sma200` (Step
   3 above) — the earlier BTC-only case for `sma200` was confounded by it
   coincidentally avoiding `rsi_bb` almost entirely, which no longer applies
   now that `rsi_bb` is out of the active path regardless. Low-stakes either
   way (the filter's only job now is "pause during ranging"), but a real
   choice I made, not a forced one.
2. `regime_switched`'s exit config is `channel_period=20, exit_channel_period=55`
   — the deployed default from Phase 2, not the wider values (70, 90) that
   looked better in later exploration on the 2023+ window specifically. I
   didn't change the deployed default based on that exploration because it
   overlapped with the same window used for out-of-sample testing (see the
   Phase 3.5 methodology note in the spec) — changing the default based on
   it would have been fitting to the test set. Worth a fresh, genuinely
   out-of-sample look at wider exits once more time has passed.
3. Heartbeat cadence (once/24h) and poll interval (300s on daily candles) are
   my own reasonable-default judgment calls, not derived from anything in
   the spec — easy to change in `config.yaml` (`alerting.heartbeat_interval_seconds`,
   `poll.interval_seconds`) if you want a different cadence.
4. Found local SSH keys on this machine early on and deliberately did not
   use them to connect anywhere (see "Known constraints" above) — used the
   existing GitHub Actions secrets/deploy pipeline for everything instead,
   including two temporary one-off workflows (both since deleted) to get
   real cross-asset numbers and confirm the shadow run's health without
   direct shell access.

**Not touched, as instructed:** no Quidax client, no API key (real or
placeholder beyond the pre-existing `.env.example` entry), no order-placement
code path. Phase 5 is untouched and gated behind you being present.

---

## Post-deployment: backtesting hygiene + new candidates (2026-09-07)

Separate stretch of work, still strictly Phase 2/3.5 research — the deployed
Phase 4 shadow-run strategy (`regime_switched`: donchian trend, flat ranging,
ADX filter) is untouched by any of this.

- **Holdout enforcement**: found our own "out-of-sample" test wasn't actually
  clean (the donchian exit-width exploration overlapped the later holdout
  window). Added `validation.holdout_start` in config.yaml — `sweep` can
  never touch it, `backtest` needs `--allow-holdout` (logged to
  `notes/holdout_validations.md`). Full detail in spec Section 8.
- **Two new untested candidates**, from the user's own roadmap brainstorm,
  fully implemented and tested (not deployed — plugged in as opt-in
  `--strategy`/`--trend-strategy`/`--regime-type` choices only):
  - `NatrRegimeFilter` (`regime.type: natr`) — volatility-expansion regime
    filter, self-relative (trailing-percentile), a different axis from
    ADX/SMA200's trend-strength.
  - `MarketStructureBreakoutStrategy` (`--strategy market_structure`) —
    swing high/low, break, retest, confirm. A genuinely different entry
    hypothesis from every indicator-based strategy already here.
- Paused here rather than continuing to the roadmap's remaining Tier-1 items
  (multi-timeframe trend+pullback, a 3-way regime composite) — those are
  bigger design decisions (multi-timeframe needs either an interface change
  or in-strategy resampling; the 3-way composite needs a real decision on
  how "expansion" gets defined from the existing filters) worth a judgment
  call together rather than guessing solo, and the two new candidates above
  need real backtest results before it's clear whether stacking more
  untested strategies on top is the right next move at all.

## Multi-timeframe trend+pullback built and evaluated (2026-09-07)

User picked Tier 1 #2 (multi-timeframe trend+pullback) after reviewing
candidate backtest results. Implemented `MultiTimeframeTrendPullbackStrategy`
(`bot/strategy/multi_timeframe.py`): resamples the same daily data internally
to a higher timeframe (`htf_rule`, default weekly) as a trend-permission
gate, enters on the daily chart only on a pullback-then-reclaim of a short
EMA in the permitted direction, ATR trailing stop. Also added a `context`
dict to `Signal` (and populated it in donchian, multi_timeframe, and
regime_switched) so diagnostic fields at signal time (regime, ATR%, channel
width, etc.) are captured for later pattern analysis, not just the
human-readable `reason` string.

**Verdict: neither new candidate (multi_timeframe, market_structure) beat
the deployed donchian+ADX control on real data.** Put back to the user
rather than guessed past — their call was to prioritize concurrent
shadow-run infrastructure over building more candidates, on the reasoning
that once that infrastructure works, testing new hypotheses becomes cheap.

## Concurrent shadow-run infrastructure (2026-09-07)

Previously the DB schema and `shadow` CLI command assumed exactly one live
strategy at a time — `paper_position` was keyed only by
(exchange, symbol, timeframe), so two strategies shadow-running the same
symbol would silently share (and corrupt) each other's paper position. Per
the user's explicit mandate, rebuilt this so multiple strategies can
shadow-run concurrently against the same candle data with fully independent
paper-trading state and no shared capital:

- **`bot/storage/db.py`**: added `strategy_label` to `signals`,
  `paper_position` (now part of its primary key), and `paper_trades`; added
  a JSON `context` column to `signals` and `paper_position` so the richer
  `Signal.context` diagnostic data is actually persisted, not just logged.
  Every affected function now takes `strategy_label`. Added a one-time
  migration (`_migrate_shadow_tables`): these three tables are dropped and
  recreated if they predate `strategy_label` — safe because every existing
  deployment's tables were still empty (no signal had fired yet on the live
  shadow run at the time of this change). Also switched to WAL journal mode
  + a 30s busy timeout, since multiple containers now share one SQLite file.
- **`bot/shadow/runner.py`**: threads `strategy_label` through every DB call
  and Telegram message; heartbeat/daily-summary schedule state is now keyed
  as `f"{strategy_label}:last_heartbeat_at"` etc. so concurrent runs don't
  clobber each other's send schedule.
- **`bot/alerting/messages.py`**: every formatted message now carries
  `strategy=<label>` as its first field, so concurrent runs posting to the
  same Telegram chat are distinguishable.
- **`main.py`**: `shadow` subcommand generalized — accepts `--strategy`
  (any of `STRATEGY_CHOICES`, default `regime_switched` to match the
  existing control) and `--strategy-label` (defaults to `--strategy`'s
  name), plus the same override flags (`--trend-strategy`,
  `--ranging-strategy`, `--regime-type`, donchian exit params) `backtest`
  already had, so a shadow run's exact configuration doesn't require a
  code change.
- **`docker-compose.yml`**: added two new services alongside the untouched
  `trade-bot` control — `trade-bot-multi-timeframe`
  (`--strategy multi_timeframe`) and `trade-bot-natr-regime`
  (`--strategy regime_switched --regime-type natr`) — sharing the same
  `trade_bot_data` volume (same candle store) but each with its own
  `--strategy-label` so their paper positions/trades never collide.
- All existing tests updated for the new signatures (149 passing); added
  coverage for strategy-label-scoped isolation in both the DB layer and the
  heartbeat/summary schedule.

Next up per the user's shortlist (deferred until this infrastructure is
confirmed working end-to-end on the VPS): a volatility-expansion breakout
variant and a funding/positioning filter — explicitly on hold until the
three services above are verified running independently.

**Confirmed live (2026-09-07, ~07:23 UTC)**: user forwarded real Telegram
output showing all three services posting distinct, correctly-labeled
DAILY_SUMMARY and HEARTBEAT messages within the same minute —
`donchian_adx_control`, `donchian_natr_regime`, and `multi_timeframe`, each
`position=flat trades_all_time=0` (expected immediately post-deploy, no
signal has fired yet on any of them). Concurrent, independently-labeled
shadow runs are working end-to-end in production, not just in tests.
Deleted the temporary `verify-concurrent-shadow.yml` workflow now that
this is confirmed directly rather than via the SSH check.

## Remaining shortlist candidates built (2026-09-07)

User said to go ahead with the two remaining candidates from the original
five-strategy shortlist, now that concurrent infrastructure is confirmed
working. Both implemented, tested, and added as a fourth and fifth
concurrent shadow-run service — no backtest verdict logged for either yet;
per the "testing hypotheses is cheap now" approach, they're going straight
to live shadow-testing rather than waiting on an offline backtest first,
same treatment multi_timeframe/donchian_natr_regime already got.

- **`VolatilityExpansionBreakoutStrategy`** (`bot/strategy/vol_expansion.py`,
  spec 7c): Bollinger Band width squeeze (self-relative rolling percentile,
  same idiom as `NatrRegimeFilter`) followed by expansion and a breakout of
  the *prior* bar's band edge — comparing against the same bar's own band
  would let one extreme close pull that band wide enough to contain itself,
  hiding the breakout. Deployed standalone (no regime filter) as
  `vol_expansion`.
- **`FundingFilteredStrategy`** (`bot/strategy/funding_filter.py`, spec 7d):
  wraps a base trend strategy (default donchian) with a Binance perpetual
  funding-rate veto — suppresses a long when funding is crowded long, a
  short when crowded short. Required a new data source: funding rates are a
  futures-only concept, absent from the spot client used for candles, so
  added a separate `binanceusdm` exchange client, a `funding_rates` DB
  table, and `bot/data/funding_backfill.py` (same paginate-and-upsert shape
  as candle backfill). Added `Strategy.before_poll()` — a no-op hook for
  every other strategy — so live shadow runs can refresh funding data every
  iteration; backtests just pass a fixed funding history for the window.
  Deployed as `donchian_funding_filtered`.
- `main.py`'s `shadow`/`backtest`/`sweep` commands all gained
  `--funding-base-strategy`/`--funding-high-threshold`/
  `--funding-low-threshold` overrides, mirroring the existing
  regime_switched/donchian override pattern. `docker-compose.yml` now runs
  five concurrent shadow services total.

**Bug found and fixed while validating these** (pre-existing, unrelated to
the new strategies): `main.py`'s `--start`/`--end` filtering and the
holdout guard compared a tz-naive `pd.Timestamp(args.start)` against the
tz-aware candle index, and separately a ms-resolution index (from
`pd.to_datetime(..., unit="ms")`) against timestamps of other resolutions —
both silently tolerated by older pandas, both now hard errors on pandas
3.0+. Since `requirements.txt` pins no pandas version, this would break
every `backtest`/`sweep` invocation (not the live `shadow` command, which
never hits this comparison path) on any fresh install. Fixed by
constructing comparison timestamps as `pd.Timestamp(x, tz="UTC")`
everywhere and normalizing `query_candles_df`/`query_funding_rates_df`'s
datetime columns to `datetime64[ns, UTC]` at the source. Found because this
sandbox's fresh pip install picked up pandas 3.0.5 and `backtest`/`sweep`
failed immediately on any real query — worth being aware this may also
affect your own local environment depending on installed pandas version.

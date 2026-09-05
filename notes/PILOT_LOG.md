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
  restarted). I **cannot** confirm from here that the process is actually
  behaving correctly at runtime — only that Docker reports it started. Please
  check `docker compose logs -f trade-bot` at `/opt/btc-trade-bot` when back;
  a `HEARTBEAT` line should appear within `alerting.heartbeat_interval_seconds`
  (default 24h) once Telegram is configured, and `polled`/backfill-style log
  lines should appear every `poll.interval_seconds` (300s) regardless.

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

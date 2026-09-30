"""One-off copy of the CLI's SQLite history into the platform database:
candles, funding rates, experiments, lifecycle stages and the shadow runs'
paper trades and rebalances. Safe to re-run: rows are matched on their
natural keys or original ids."""

import json
from datetime import datetime, timezone

from django.core.management.base import BaseCommand

from market.candles import upsert_candles
from research.legacy import open_legacy_db
from market.models import FundingRate
from research.models import Experiment, ShadowRebalance, ShadowTrade, StrategyLifecycle


def _dt(ms) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, timezone.utc) if ms is not None else None


def _json(raw) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {"raw": raw}


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


class Command(BaseCommand):
    help = "Import data/trades.db (the CLI's SQLite database) into the platform database."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default="/legacy/trades.db")
        parser.add_argument("--skip-candles", action="store_true")

    def handle(self, *args, path, skip_candles=False, **options):
        conn = open_legacy_db(path)
        report = []

        if not skip_candles and _has_table(conn, "candles"):
            series = conn.execute("SELECT DISTINCT exchange, symbol, timeframe FROM candles").fetchall()
            for exchange, symbol, timeframe in series:
                rows = conn.execute(
                    "SELECT open_time, open, high, low, close, volume FROM candles "
                    "WHERE exchange=? AND symbol=? AND timeframe=? ORDER BY open_time",
                    (exchange, symbol, timeframe),
                ).fetchall()
                for i in range(0, len(rows), 2000):
                    upsert_candles(exchange, symbol, timeframe, [list(r) for r in rows[i:i + 2000]])
                report.append(f"candles {exchange} {symbol} {timeframe}: {len(rows)}")

        if _has_table(conn, "funding_rates"):
            rows = conn.execute("SELECT exchange, symbol, funding_time, funding_rate FROM funding_rates").fetchall()
            FundingRate.objects.bulk_create(
                [FundingRate(exchange=e, symbol=s, funding_time=_dt(t), funding_rate=r) for e, s, t, r in rows],
                ignore_conflicts=True,
            )
            report.append(f"funding rates: {len(rows)}")

        if _has_table(conn, "experiments"):
            rows = conn.execute(
                "SELECT id, created_at, kind, strategy, strategy_label, symbol, timeframe, window_start, "
                "window_end, touched_holdout, config_json, config_hash, data_version, code_commit, "
                "result_json, decision, decision_reason FROM experiments"
            ).fetchall()
            for r in rows:
                Experiment.objects.update_or_create(
                    source_id=r[0],
                    defaults=dict(
                        created_at=_dt(r[1]), kind=r[2], strategy=r[3], strategy_label=r[4], symbol=r[5],
                        timeframe=r[6], window_start=r[7] or "", window_end=r[8] or "",
                        touched_holdout=bool(r[9]), config=_json(r[10]), config_hash=r[11],
                        data_version=r[12] or "", git_commit=r[13] or "", result=_json(r[14]),
                        decision=r[15] or "", decision_reason=r[16] or "",
                    ),
                )
            report.append(f"experiments: {len(rows)}")

        if _has_table(conn, "bot_state"):
            rows = conn.execute("SELECT key, value FROM bot_state WHERE key LIKE 'lifecycle:%'").fetchall()
            for key, stage in rows:
                StrategyLifecycle.objects.update_or_create(label=key.split(":", 1)[1], defaults={"stage": stage})
            report.append(f"lifecycle stages: {len(rows)}")

        if _has_table(conn, "paper_trades"):
            rows = conn.execute(
                "SELECT id, symbol, timeframe, strategy_label, direction, entry_time, entry_price, exit_time, "
                "exit_price, pnl_pct, exit_reason, context FROM paper_trades"
            ).fetchall()
            for r in rows:
                ShadowTrade.objects.update_or_create(
                    source_id=r[0],
                    defaults=dict(symbol=r[1], timeframe=r[2], strategy_label=r[3], direction=r[4],
                                  entry_time=_dt(r[5]), entry_price=r[6], exit_time=_dt(r[7]), exit_price=r[8],
                                  pnl_pct=r[9], exit_reason=r[10], context=_json(r[11])),
                )
            report.append(f"shadow trades: {len(rows)}")

        if _has_table(conn, "exposure_rebalances"):
            rows = conn.execute(
                "SELECT id, symbol, timeframe, strategy_label, bar_time, price, from_weight, to_weight, equity "
                "FROM exposure_rebalances"
            ).fetchall()
            for r in rows:
                ShadowRebalance.objects.update_or_create(
                    source_id=r[0],
                    defaults=dict(symbol=r[1], timeframe=r[2], strategy_label=r[3], bar_time=_dt(r[4]),
                                  price=r[5], from_weight=r[6], to_weight=r[7], equity=r[8]),
                )
            report.append(f"shadow rebalances: {len(rows)}")

        for line in report:
            self.stdout.write(line)

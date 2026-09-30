import logging
import time
from datetime import datetime

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from alerts.models import AlertRule
from alerts.service import notify
from research.jobs import run_job
from research.models import ResearchJob
from trading.engine.loop import HEARTBEAT_KEY

logger = logging.getLogger(__name__)


def watch_engine(was_down: bool | None) -> bool:
    """The engine can't report its own death, so the worker does: one
    alert when its heartbeat goes stale, one when it comes back.
    `was_down` is None on the first check, when there's nothing to compare."""
    beat = cache.get(HEARTBEAT_KEY)
    last = datetime.fromisoformat(beat["at"]) if beat else None
    down = last is None or (timezone.now() - last).total_seconds() > settings.ENGINE["heartbeat_stale_seconds"] * 2
    if down and not was_down:
        seen = f"last seen {last:%Y-%m-%d %H:%M UTC}" if last else "never seen"
        notify(AlertRule.Kind.BOT_ERROR, "Engine is down",
               f"Bots are idle and stop losses aren't being watched ({seen}).")
    elif was_down is True and not down:
        notify(AlertRule.Kind.BOT_ERROR, "Engine is back", "Stops are being watched again.")
    return down


def report_done(job: ResearchJob) -> None:
    params = job.params
    if job.status == ResearchJob.Status.FAILED:
        notify(AlertRule.Kind.RESEARCH_DONE, f"Research report failed: {params.get('strategy')}", job.error[:500])
        return
    lines = []
    for run in job.result.get("runs", []):
        test = (run.get("windows") or {}).get("test") or {}
        lines.append(f"{run['label']}: test {test.get('total_return_pct', '?')}%, Sharpe {test.get('sharpe_ratio', '?')}, "
                     f"max drawdown {test.get('max_drawdown_pct', '?')}%")
    notify(AlertRule.Kind.RESEARCH_DONE, f"Research report ready: {params.get('strategy')} on {params.get('timeframe')}",
           "\n".join(lines))


class Command(BaseCommand):
    help = "Run queued research jobs one at a time, and alert if the engine stops."

    def handle(self, *args, **options):
        # a job left "running" by a crashed worker would otherwise never finish
        ResearchJob.objects.filter(status=ResearchJob.Status.RUNNING).update(
            status=ResearchJob.Status.FAILED, error="worker restarted while this job was running"
        )
        logger.info("research worker started")
        engine_down: bool | None = None
        last_watch = 0.0
        while True:
            if time.monotonic() - last_watch > 30:
                last_watch = time.monotonic()
                try:
                    engine_down = watch_engine(engine_down)
                except Exception:
                    logger.exception("engine watch failed")
            with transaction.atomic():
                job = (
                    ResearchJob.objects.select_for_update(skip_locked=True)
                    .filter(status=ResearchJob.Status.QUEUED).order_by("created_at").first()
                )
                if job is not None:
                    job.status = ResearchJob.Status.RUNNING
                    job.save(update_fields=["status"])
            if job is None:
                time.sleep(3)
                continue
            logger.info("running research job %s", job.pk)
            report_done(run_job(job))

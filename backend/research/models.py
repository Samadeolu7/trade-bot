from django.conf import settings
from django.db import models

from bot.research.lifecycle import STAGES

STAGE_CHOICES = [(s, s.replace("_", " ")) for s in STAGES]


class Experiment(models.Model):
    """Every backtest, sweep combination and research-report window ever
    run (spec Section 8), including rejected ideas. Rows are never deleted,
    and `decision` is only ever set by a person."""

    created_at = models.DateTimeField(db_index=True)
    kind = models.CharField(max_length=20)
    strategy = models.CharField(max_length=40, db_index=True)
    strategy_label = models.CharField(max_length=80)
    symbol = models.CharField(max_length=30)
    timeframe = models.CharField(max_length=10)
    window_start = models.CharField(max_length=40, blank=True)
    window_end = models.CharField(max_length=40, blank=True)
    touched_holdout = models.BooleanField(default=False)
    config = models.JSONField(default=dict)
    config_hash = models.CharField(max_length=20)
    data_version = models.CharField(max_length=120, blank=True)
    git_commit = models.CharField(max_length=40, blank=True)
    result = models.JSONField(default=dict)
    decision = models.CharField(max_length=20, blank=True)
    decision_reason = models.TextField(blank=True)
    # the SQLite row this was imported from, so re-running the import is safe
    source_id = models.IntegerField(null=True, unique=True)

    class Meta:
        ordering = ["-created_at"]


class StrategyLifecycle(models.Model):
    """A deployed configuration's stage, from idea to automation_ready.
    Never advanced by a result: only a person changes it. A live bot must
    be named after a label that has reached automation_ready."""

    label = models.CharField(max_length=80, unique=True)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default="research")
    note = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["label"]


class ResearchJob(models.Model):
    """A backtest or research report requested from the app, run by the
    `run_worker` process so a long run never blocks the website."""

    class Kind(models.TextChoices):
        BACKTEST = "backtest", "Backtest"
        RESEARCH_REPORT = "research_report", "Research report"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    params = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ShadowTrade(models.Model):
    """A closed trade from the pre-platform shadow runs (the CLI's
    paper_trades table), imported so their record isn't lost. Sized as one
    unit, with no capital accounting, so only pnl_pct is meaningful."""

    source_id = models.IntegerField(unique=True)
    strategy_label = models.CharField(max_length=80, db_index=True)
    symbol = models.CharField(max_length=30)
    timeframe = models.CharField(max_length=10)
    direction = models.CharField(max_length=5)
    entry_time = models.DateTimeField()
    entry_price = models.FloatField()
    exit_time = models.DateTimeField()
    exit_price = models.FloatField()
    pnl_pct = models.FloatField()
    exit_reason = models.CharField(max_length=30)
    context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-exit_time"]


class ShadowRebalance(models.Model):
    """A rebalance from the pre-platform exposure paper runs."""

    source_id = models.IntegerField(unique=True)
    strategy_label = models.CharField(max_length=80, db_index=True)
    symbol = models.CharField(max_length=30)
    timeframe = models.CharField(max_length=10)
    bar_time = models.DateTimeField()
    price = models.FloatField()
    from_weight = models.FloatField()
    to_weight = models.FloatField()
    equity = models.FloatField()

    class Meta:
        ordering = ["-bar_time"]

from django.db import models


class Feed(models.Model):
    """One strategy's advisory feed for manual trading on MT5/Exness, the
    platform version of `python main.py recommend` (spec Section 9.5). It
    never places an order anywhere: it tracks the position it would hold
    and tells you when to enter, move the stop, exit or resize."""

    name = models.CharField(max_length=80, unique=True)
    strategy = models.CharField(max_length=40)
    params = models.JSONField(default=dict, blank=True)
    symbol = models.CharField(max_length=30, default="BTC/USDT")
    timeframe = models.CharField(max_length=10, default="1d")
    # Exness CFD cost placeholders from config.yaml's recommend section
    fee = models.FloatField(default=0.0)
    slippage = models.FloatField(default=0.0003)
    enabled = models.BooleanField(default=True)
    last_bar_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    status_reason = models.CharField(max_length=300, blank=True)
    last_diagnosis = models.JSONField(default=dict, blank=True)
    near_miss_key = models.CharField(max_length=80, blank=True)
    # the open call + direction a repeat entry signal was last reported for
    repeat_signal_key = models.CharField(max_length=80, blank=True)

    # the recommended position (signal strategies)
    direction = models.CharField(max_length=5, blank=True)  # "", "long", "short"
    entry_price = models.FloatField(null=True, blank=True)
    entry_time = models.DateTimeField(null=True, blank=True)
    stop = models.FloatField(null=True, blank=True)
    take_profit = models.FloatField(null=True, blank=True)
    entry_context = models.JSONField(default=dict, blank=True)

    # the recommended fraction of capital (exposure strategies), and the
    # hypothetical equity it would have produced from 10,000
    weight = models.FloatField(default=0.0)
    equity = models.FloatField(default=10_000.0)
    last_close = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Recommendation(models.Model):
    """Every call a feed made: the history you traded from."""

    class Kind(models.TextChoices):
        ENTRY = "entry", "Entry"
        STOP_UPDATE = "stop_update", "Move stop"
        EXIT = "exit", "Exit"
        REBALANCE = "rebalance", "Resize"
        NEAR_MISS = "near_miss", "Near miss"
        SIGNAL_AGAIN = "signal_again", "Signal again"

    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="recommendations")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    bar_time = models.DateTimeField()
    direction = models.CharField(max_length=5, blank=True)
    price = models.FloatField(null=True, blank=True)
    stop = models.FloatField(null=True, blank=True)
    take_profit = models.FloatField(null=True, blank=True)
    from_weight = models.FloatField(null=True, blank=True)
    to_weight = models.FloatField(null=True, blank=True)
    pnl_pct = models.FloatField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    context = models.JSONField(default=dict, blank=True)
    fear_greed = models.CharField(max_length=40, blank=True)
    # brought over from the CLI's recommend runs rather than made here
    imported = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-bar_time", "-id"]

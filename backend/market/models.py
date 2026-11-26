from django.db import models


class Candle(models.Model):
    """OHLCV bars the strategies run on. The source (Binance by default,
    config.yaml `exchange.id`) is the same one every backtest used, so a
    bot in the app sees the data its research was done on."""

    exchange = models.CharField(max_length=30)
    symbol = models.CharField(max_length=30)
    timeframe = models.CharField(max_length=10)
    open_time = models.DateTimeField()
    open = models.FloatField()
    high = models.FloatField()
    low = models.FloatField()
    close = models.FloatField()
    volume = models.FloatField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["exchange", "symbol", "timeframe", "open_time"], name="uniq_candle")
        ]
        indexes = [models.Index(fields=["exchange", "symbol", "timeframe", "open_time"])]
        ordering = ["open_time"]


class FundingRate(models.Model):
    """Perpetual funding prints, for funding_filtered (spec Section 7d)."""

    exchange = models.CharField(max_length=30)
    symbol = models.CharField(max_length=30)
    funding_time = models.DateTimeField()
    funding_rate = models.FloatField()
    # when this system first stored the print. Rows the live engine picked
    # up within a day of funding_time are the measured lag
    # (market/latency.py); older rows are history backfills. Null for rows
    # stored before this was recorded.
    fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["exchange", "symbol", "funding_time"], name="uniq_funding")
        ]
        ordering = ["funding_time"]


class FetchAttempt(models.Model):
    """One call to an outside data source (funding rates now, fundamentals
    later), kept so research can model the lag and outages the live system
    really has: how often fetches fail and for how long."""

    source = models.CharField(max_length=40)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    ok = models.BooleanField()
    new_rows = models.IntegerField(default=0)
    error = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["source", "started_at"])]
        ordering = ["started_at"]

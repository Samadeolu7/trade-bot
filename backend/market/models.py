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

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["exchange", "symbol", "funding_time"], name="uniq_funding")
        ]
        ordering = ["funding_time"]

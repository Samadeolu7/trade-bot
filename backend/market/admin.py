from django.contrib import admin

from market.models import Candle, FundingRate


@admin.register(Candle)
class CandleAdmin(admin.ModelAdmin):
    list_display = ("exchange", "symbol", "timeframe", "open_time", "close")
    list_filter = ("exchange", "symbol", "timeframe")


@admin.register(FundingRate)
class FundingRateAdmin(admin.ModelAdmin):
    list_display = ("exchange", "symbol", "funding_time", "funding_rate")

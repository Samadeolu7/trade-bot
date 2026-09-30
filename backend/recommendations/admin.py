from django.contrib import admin

from recommendations.models import Feed, Recommendation


@admin.register(Feed)
class FeedAdmin(admin.ModelAdmin):
    list_display = ("name", "strategy", "timeframe", "enabled", "direction", "weight", "last_bar_at")


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = ("bar_time", "feed", "kind", "direction", "price", "stop", "to_weight", "imported")
    list_filter = ("kind", "feed", "imported")

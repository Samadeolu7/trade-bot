from django.contrib import admin

from research.models import Experiment, ResearchJob, StrategyLifecycle


@admin.register(Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "strategy_label", "timeframe", "window_start", "window_end", "decision")
    list_filter = ("kind", "strategy", "decision", "touched_holdout")
    search_fields = ("strategy_label",)


@admin.register(StrategyLifecycle)
class StrategyLifecycleAdmin(admin.ModelAdmin):
    list_display = ("label", "stage", "updated_by", "updated_at")
    list_filter = ("stage",)


@admin.register(ResearchJob)
class ResearchJobAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "status", "created_by")
    list_filter = ("kind", "status")

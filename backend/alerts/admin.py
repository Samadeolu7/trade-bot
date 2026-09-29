from django.contrib import admin

from alerts.models import AlertEvent, AlertRule


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "params", "enabled", "last_fired_at")
    list_filter = ("kind", "enabled")


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "kind", "title", "delivered")
    list_filter = ("kind", "delivered")

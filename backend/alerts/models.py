from django.conf import settings
from django.db import models


class AlertRule(models.Model):
    """Something a person wants to be told about, TradingView-style. Rules
    belong to one person and go to their Telegram chat and the in-app log.
    Rules scoped to an account only fire for events on accounts that person
    can still see."""

    class Kind(models.TextChoices):
        PRICE_ABOVE = "price_above", "Price crosses above"
        PRICE_BELOW = "price_below", "Price crosses below"
        PRICE_MOVE = "price_move", "Sudden move"
        RECOMMENDATION = "recommendation", "MT5 recommendations"
        NEAR_MISS = "near_miss", "Near misses"
        REPEAT_SIGNAL = "repeat_signal", "Entry signal while in a trade"
        BOT_TRADE = "bot_trade", "Bot trades"
        STOP_HIT = "stop_hit", "Stop or take profit hit"
        BOT_ERROR = "bot_error", "Bot or engine problem"
        RESEARCH_DONE = "research_done", "Research report finished"
        DAILY_SUMMARY = "daily_summary", "Daily summary"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alert_rules")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    # price rules: {"venue", "symbol", "price"}; price_move: {"venue",
    # "symbol", "pct", "minutes"}; daily_summary: {"hour"} (UTC)
    params = models.JSONField(default=dict, blank=True)
    # narrows bot/stop rules to one account or one bot; empty = all visible
    account = models.ForeignKey("trading.TradingAccount", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    bot = models.ForeignKey("trading.Bot", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    note = models.CharField(max_length=200, blank=True)
    enabled = models.BooleanField(default=True)
    # fire once and then switch off (TradingView's "Only once"); off by
    # default, so an alert keeps working until its owner turns it off
    once = models.BooleanField(default=False)
    cooldown_minutes = models.PositiveIntegerField(default=0)
    last_fired_at = models.DateTimeField(null=True, blank=True)
    # evaluation memory, e.g. the last price seen for a crossing rule
    state = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "id"]


class AlertEvent(models.Model):
    """Every alert that fired, whether or not Telegram delivered it."""

    rule = models.ForeignKey(AlertRule, null=True, on_delete=models.SET_NULL, related_name="events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alert_events")
    kind = models.CharField(max_length=20)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    delivered = models.BooleanField(default=False)
    # alerts stay on screen in the app until the person dismisses them
    dismissed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class PushSubscription(models.Model):
    """A browser that has agreed to show this person's alerts as system
    notifications (Web Push). One per browser/device."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_success_at = models.DateTimeField(null=True, blank=True)


class PushKeys(models.Model):
    """The server's Web Push (VAPID) key pair, generated on first use. A
    single row; the browser needs the public half to subscribe."""

    private_pem = models.TextField()
    public_key = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

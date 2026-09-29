from django.contrib import admin

from trading.models import (
    AccountGrant,
    Bot,
    BotDecision,
    EquitySnapshot,
    Fill,
    LedgerEntry,
    Order,
    PaperBalance,
    Position,
    TradingAccount,
)


class GrantInline(admin.TabularInline):
    model = AccountGrant
    extra = 0


@admin.register(TradingAccount)
class TradingAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "mode", "venue", "is_active", "halted")
    inlines = [GrantInline]


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    list_display = ("name", "strategy", "timeframe", "account", "status", "last_bar_at")
    list_filter = ("status", "strategy", "account")


class FillInline(admin.TabularInline):
    model = Fill
    extra = 0
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("created_at", "account", "book", "source", "side", "quantity", "status", "average_price")
    list_filter = ("status", "source", "account")
    inlines = [FillInline]


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("account", "book", "symbol", "quantity", "average_price", "stop_price", "take_profit")


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "account", "book", "kind", "asset", "amount")
    list_filter = ("kind", "account", "book")


@admin.register(BotDecision)
class BotDecisionAdmin(admin.ModelAdmin):
    list_display = ("bar_time", "bot", "action", "reason")
    list_filter = ("action", "bot")


admin.site.register(PaperBalance)
admin.site.register(EquitySnapshot)

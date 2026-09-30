import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from bot.broker.venues import VENUES, get_venue

MONEY = {"max_digits": 28, "decimal_places": 10}
VENUE_CHOICES = [(key, v.label) for key, v in VENUES.items()]
MANUAL_BOOK = "manual"


def bot_book(bot_id: int) -> str:
    return f"bot:{bot_id}"


class TradingAccount(models.Model):
    """An account at a venue. Paper and live accounts behave identically
    to everything above the broker; `mode` only picks the broker."""

    class Mode(models.TextChoices):
        PAPER = "paper", "Paper"
        LIVE = "live", "Live"

    name = models.CharField(max_length=80, unique=True)
    mode = models.CharField(max_length=5, choices=Mode.choices, default=Mode.PAPER)
    venue = models.CharField(max_length=30, choices=VENUE_CHOICES, default="quidax_spot")
    is_active = models.BooleanField(default=True)
    # set by the kill switch or a risk rule: no new exposure, exits only
    halted = models.BooleanField(default=False)
    halted_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.mode})"

    @property
    def venue_profile(self):
        return get_venue(self.venue)

    @property
    def quote_asset(self) -> str:
        return self.venue_profile.quote_asset


class AccountGrant(models.Model):
    """Per-account access for traders and viewers. Owners see everything
    and need no grants."""

    class Role(models.TextChoices):
        TRADER = "trader", "Trader"
        VIEWER = "viewer", "Viewer"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="grants")
    account = models.ForeignKey(TradingAccount, on_delete=models.CASCADE, related_name="grants")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "account"], name="uniq_grant")]


# --- the simulated venue behind paper accounts --------------------------------
# These two tables are the paper "exchange": what a real venue would hold
# on its side. Our own books (Order/Fill/Position/LedgerEntry) are kept
# separately, exactly as they would be against a live venue.


class PaperBalance(models.Model):
    account = models.ForeignKey(TradingAccount, on_delete=models.CASCADE, related_name="paper_balances")
    asset = models.CharField(max_length=10)
    amount = models.DecimalField(**MONEY, default=Decimal(0))

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "asset"], name="uniq_paper_balance")]


class PaperVenueOrder(models.Model):
    account = models.ForeignKey(TradingAccount, on_delete=models.CASCADE, related_name="paper_orders")
    venue_order_id = models.CharField(max_length=40, unique=True)
    client_id = models.CharField(max_length=64)
    symbol = models.CharField(max_length=30)
    side = models.CharField(max_length=4)
    order_type = models.CharField(max_length=10)
    quantity = models.DecimalField(**MONEY)
    limit_price = models.DecimalField(**MONEY, null=True)
    status = models.CharField(max_length=10, db_index=True)
    fills = models.JSONField(default=list)
    reject_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


# --- bots ---------------------------------------------------------------------


class Bot(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"  # holds its position, stops and exits still work
        STOPPED = "stopped", "Stopped"  # flat and idle
        ERROR = "error", "Error"  # paused after repeated failures

    name = models.CharField(max_length=80, unique=True)
    strategy = models.CharField(max_length=40)
    # "section.key" -> value overrides on top of config.yaml's strategy
    # defaults, e.g. {"donchian_ensemble.bars_per_day": 6}
    params = models.JSONField(default=dict, blank=True)
    symbol = models.CharField(max_length=30, default="BTC/USDT")
    timeframe = models.CharField(max_length=10, default="4h")
    account = models.ForeignKey(TradingAccount, on_delete=models.PROTECT, related_name="bots")
    # quote-asset capital moved from the account's manual book into the
    # bot's own book when the bot is created
    allocation = models.DecimalField(**MONEY)
    # fraction of the bot's equity risked per trade, sized off the stop
    # distance (signal strategies only; spec Section 7)
    risk_pct = models.FloatField(default=0.01)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.STOPPED)
    status_reason = models.CharField(max_length=300, blank=True)
    # the last completed bar the engine evaluated
    last_bar_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    consecutive_errors = models.PositiveIntegerField(default=0)
    # the near-miss currently being reported, so a lasting one alerts once
    near_miss_key = models.CharField(max_length=80, blank=True)
    # the position + direction a repeat entry signal was last alerted for,
    # so a signal that keeps firing alerts once per position
    repeat_signal_key = models.CharField(max_length=80, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def book(self) -> str:
        return bot_book(self.pk)


class BotDecision(models.Model):
    """What a bot concluded on one completed bar, and why, including the
    bars where it did nothing. This is how "what is the bot doing" is
    answered without guessing."""

    class Action(models.TextChoices):
        NONE = "none", "No signal"
        HOLD = "hold", "Holding"
        ENTER = "enter", "Entered"
        REBALANCE = "rebalance", "Rebalanced"
        SKIP = "skip", "Skipped"
        BLOCKED = "blocked", "Blocked"
        ERROR = "error", "Error"

    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="decisions")
    bar_time = models.DateTimeField()
    action = models.CharField(max_length=10, choices=Action.choices)
    reason = models.CharField(max_length=500, blank=True)
    diagnosis = models.JSONField(default=dict, blank=True)
    order = models.ForeignKey("Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    price = models.FloatField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-bar_time"]
        constraints = [models.UniqueConstraint(fields=["bot", "bar_time"], name="uniq_bot_decision")]


# --- our books ------------------------------------------------------------------


class Order(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        BOT = "bot", "Bot"
        STOP = "stop", "Stop loss"
        TAKE_PROFIT = "take_profit", "Take profit"
        CLOSE = "close", "Close position"
        KILL_SWITCH = "kill_switch", "Kill switch"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"  # created, not yet acknowledged by the venue
        OPEN = "open", "Open"
        FILLED = "filled", "Filled"
        CANCELLED = "cancelled", "Cancelled"
        REJECTED = "rejected", "Rejected"

    client_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    account = models.ForeignKey(TradingAccount, on_delete=models.PROTECT, related_name="orders")
    book = models.CharField(max_length=30, db_index=True)
    bot = models.ForeignKey(Bot, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    placed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    source = models.CharField(max_length=12, choices=Source.choices)
    symbol = models.CharField(max_length=30)
    side = models.CharField(max_length=4)
    order_type = models.CharField(max_length=10)
    quantity = models.DecimalField(**MONEY)
    limit_price = models.DecimalField(**MONEY, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    filled_quantity = models.DecimalField(**MONEY, default=Decimal(0))
    average_price = models.DecimalField(**MONEY, null=True, blank=True)
    fees = models.DecimalField(**MONEY, default=Decimal(0))
    venue_order_id = models.CharField(max_length=64, blank=True, db_index=True)
    reject_reason = models.CharField(max_length=300, blank=True)
    # why this order exists: the signal reason, "stop hit at ...", a note
    reason = models.CharField(max_length=500, blank=True)
    # protection to put on the resulting position once this order fills
    stop_price = models.DecimalField(**MONEY, null=True, blank=True)
    take_profit = models.DecimalField(**MONEY, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_final(self) -> bool:
        return self.status in (self.Status.FILLED, self.Status.CANCELLED, self.Status.REJECTED)


class Fill(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="fills")
    price = models.DecimalField(**MONEY)
    quantity = models.DecimalField(**MONEY)
    fee = models.DecimalField(**MONEY)
    liquidity = models.CharField(max_length=5)
    time = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["-time"]


class Position(models.Model):
    """One book's position in one symbol. Quantity is signed (negative is
    short, CFD only). Stops and take-profits are held here and triggered
    by the engine, because Quidax has no stop orders."""

    account = models.ForeignKey(TradingAccount, on_delete=models.PROTECT, related_name="positions")
    book = models.CharField(max_length=30)
    bot = models.ForeignKey(Bot, null=True, blank=True, on_delete=models.SET_NULL, related_name="positions")
    symbol = models.CharField(max_length=30)
    quantity = models.DecimalField(**MONEY, default=Decimal(0))
    average_price = models.DecimalField(**MONEY, default=Decimal(0))
    stop_price = models.DecimalField(**MONEY, null=True, blank=True)
    take_profit = models.DecimalField(**MONEY, null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    # gross of fees; fees are in the ledger
    realized_pnl = models.DecimalField(**MONEY, default=Decimal(0))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "book", "symbol"], name="uniq_position")]

    @property
    def direction(self) -> str:
        if self.quantity > 0:
            return "long"
        if self.quantity < 0:
            return "short"
        return "flat"


class LedgerEntry(models.Model):
    """Every movement of every asset in every book. A book's balance of an
    asset is the sum of its entries; the books of a paper account always
    add up to the paper venue's balances (tested)."""

    class Kind(models.TextChoices):
        DEPOSIT = "deposit", "Deposit"
        WITHDRAWAL = "withdrawal", "Withdrawal"
        ALLOCATION = "allocation", "Allocation"
        TRADE = "trade", "Trade"
        FEE = "fee", "Fee"
        FINANCING = "financing", "Financing"

    account = models.ForeignKey(TradingAccount, on_delete=models.PROTECT, related_name="ledger")
    book = models.CharField(max_length=30, db_index=True)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    asset = models.CharField(max_length=10)
    amount = models.DecimalField(**MONEY)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class EquitySnapshot(models.Model):
    account = models.ForeignKey(TradingAccount, on_delete=models.CASCADE, related_name="equity_snapshots")
    # "" = the whole account; otherwise a book
    book = models.CharField(max_length=30, blank=True)
    time = models.DateTimeField()
    equity = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["time"]
        indexes = [models.Index(fields=["account", "book", "time"])]

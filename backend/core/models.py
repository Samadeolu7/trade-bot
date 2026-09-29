from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        # everything: every account, users and grants, venue keys, kill switch
        OWNER = "owner", "Owner"
        # trade and control bots on the accounts they're granted
        TRADER = "trader", "Trader"
        # read-only, on the accounts they're granted
        VIEWER = "viewer", "Viewer"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    # where this person's alerts go; blank falls back to TELEGRAM_CHAT_ID for the owner
    telegram_chat_id = models.CharField(max_length=40, blank=True)

    @property
    def is_owner(self) -> bool:
        return self.role == self.Role.OWNER


class AuditEvent(models.Model):
    """Who did what, and when: every order, bot control, risk change,
    login and grant change. `actor` is the person; `actor_label` names a
    non-person actor ("bot:donchian_4h", "engine") or is empty."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actor_label = models.CharField(max_length=100, blank=True)
    action = models.CharField(max_length=60, db_index=True)
    account = models.ForeignKey(
        "trading.TradingAccount", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    target = models.CharField(max_length=100, blank=True)
    data = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        who = self.actor.username if self.actor else self.actor_label or "system"
        return f"{self.created_at:%Y-%m-%d %H:%M} {who} {self.action} {self.target}"

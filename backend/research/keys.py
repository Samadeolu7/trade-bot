"""Research-only API keys: creation, lookup and the bearer auth class.

A key authenticates only on the endpoints that opt in with
`research_auth` (starting and reading research jobs, reading experiments
and reports). Everything else in the API uses session auth alone, so a
leaked key can't reach bots, orders, accounts, users or alerts, and it
stops working within a day anyway."""

import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone
from ninja.security import HttpBearer, django_auth

from research.models import ResearchApiKey

KEY_PREFIX = "rk_"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(user, name: str, hours: int, max_jobs: int = 30) -> tuple[ResearchApiKey, str]:
    """Returns (row, the key itself). The key is never stored or shown again."""
    if not 1 <= hours <= ResearchApiKey.MAX_HOURS:
        raise ValueError(f"keys last between 1 and {ResearchApiKey.MAX_HOURS} hours")
    if not 1 <= max_jobs <= 200:
        raise ValueError("max jobs must be between 1 and 200")
    prefix = secrets.token_hex(4)
    key = f"{KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
    row = ResearchApiKey.objects.create(
        name=name.strip() or "research key", prefix=prefix, key_hash=_hash(key), created_by=user,
        expires_at=timezone.now() + timedelta(hours=hours), max_jobs=max_jobs,
    )
    return row, key


def active_key(key: str) -> ResearchApiKey | None:
    if not key.startswith(KEY_PREFIX):
        return None
    row = ResearchApiKey.objects.select_related("created_by").filter(key_hash=_hash(key)).first()
    if row is None or row.status != "active" or not row.created_by.is_active:
        return None
    return row


class ResearchKeyAuth(HttpBearer):
    def authenticate(self, request, token):
        row = active_key(token)
        if row is None:
            return None
        ResearchApiKey.objects.filter(pk=row.pk).update(last_used_at=timezone.now())
        # the endpoint code sees the owner who created the key as the user,
        # and request.research_key tells it the request came through a key
        request.user = row.created_by
        request.research_key = row
        return row


# research endpoints accept either the app's session or a research key
research_auth = [ResearchKeyAuth(), django_auth]
